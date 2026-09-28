"""A1 Cash for Cars adapter (Phase 1P.2).

No real A1 API exists in this codebase, so the adapter is an in-database,
deterministic LOCAL DEMO provider. Every result is tagged
``"provider": LOCAL_DEMO_TAG`` and availability follows a fixed local schedule,
so simulated business data can never be mistaken for a live provider call. The
adapter never computes pricing — quote status only reports a bounded stage.

Customer-visible wording contract
---------------------------------
Because nothing is sent to A1, no customer-visible message may assert an
external state that no system established. A tagged ``metadata.provider`` is a
developer affordance; a customer never sees it, so the message itself is the
only thing standing between a local demo and a false promise to a real person.

Every ``customer_message`` here therefore describes only the one thing that
genuinely happened — this system recorded a request locally — and never states
that a person was contacted or that a booking, quote, offer, valuation, or hold
exists. No human reviews these requests during the pilot.
Concretely, the pilot must not say "your pickup is scheduled", "we'll hold that
slot", "an offer is ready", "your vehicle is valued", or "payment completed".
The same rule applies to ``summary``, which is staff-facing but is read during a
live pilot and copied into tickets.

``tests/test_a1_local_demo_wording.py`` pins this: it exercises every tool and
fails on any completion-style phrasing. A real provider integration landing later
is the one legitimate reason to relax it, and it must relax these strings
deliberately rather than by drift.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.a1_cash_for_cars.schemas import (
    PickupAvailabilityRequest,
    PickupRequest,
    QuoteStatusRequest,
    VehicleLead,
    VehicleLeadUpdate,
    VehiclePhotosRequest,
)
from app.models.business_action import BusinessAction
from app.repositories.business_action_repository import BusinessActionRepository
from app.tools.base import (
    ACTION_STATUS_COMPLETED,
    BusinessToolContext,
    BusinessToolExecutionError,
    BusinessToolExecutor,
    ToolExecutionResult,
)

LOCAL_DEMO_TAG = "local_demo"

A1_CREATE_LEAD = "a1.create_vehicle_lead"

# Local demo availability: fixed windows per weekday. This schedule is the
# whole truth for the provider; there is no real backend to consult.
_AVAILABLE_WINDOWS: dict[int, tuple[str, ...]] = {
    0: ("morning", "afternoon", "evening"),  # Monday
    1: ("morning", "afternoon", "evening"),  # Tuesday
    2: ("morning", "afternoon", "evening"),  # Wednesday
    3: ("morning", "afternoon", "evening"),  # Thursday
    4: ("morning", "afternoon", "evening"),  # Friday
    5: ("morning",),  # Saturday
    6: (),  # Sunday closed
}

_MAX_AVAILABILITY_LOOKAHEAD_DAYS = 60


def _available_windows_for(day: date) -> tuple[str, ...]:
    return _AVAILABLE_WINDOWS[day.weekday()]


def _deterministic_reference(
    context: BusinessToolContext,
    tool_name: str,
    suffix: str = "",
) -> str:
    scope = context.run_id or f"ticket:{context.ticket_id}"
    raw = f"{context.organization_id}:{scope}:{tool_name}:{suffix}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
    return f"A1-{digest}"


def _lead_error_message(reference_id: str) -> str:
    return (
        f"Vehicle lead reference {reference_id} was not found for this "
        "organization. Re-request a quote from the initial vehicle listing."
    )


def _stage_for_lead(lead: BusinessAction) -> str:
    """Deterministic quote stage; never a dollar amount."""
    if lead.payload_json.get("photos_requested"):
        marker = int(str(lead.reference_id)[-1], 16)
        return "offer_available" if marker % 2 else "quote_in_preparation"
    return "quote_pending"


class A1CashForCarsExecutor(BusinessToolExecutor):
    """Dispatcher over the six registered A1 tools.

    All operations are idempotent-safe: writes converge on BusinessAction rows
    keyed by the run's dedupe key, and reads are pure. Deterministic reference
    ids keep retries of the same run producing the same lead reference.
    """

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        handler_name = f"_{self.tool_name.replace('.', '_')}"
        handler = getattr(self, handler_name)
        return await handler(db, context=context, arguments=arguments)

    async def _a1_create_vehicle_lead(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = VehicleLead.model_validate(arguments)
        reference_id = _deterministic_reference(context, self.tool_name)

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=(
                f"Vehicle lead created for {args.vehicle_year} "
                f"{args.vehicle_make} {args.vehicle_model}."
            ),
            customer_visible=True,
            customer_message=(
                "Your vehicle listing request has been recorded for the A1 "
                "Cash for Cars team. No one has been contacted and no quote "
                "has been prepared yet."
            ),
            reference_id=reference_id,
            metadata={
                "provider": LOCAL_DEMO_TAG,
                "lead_reference": reference_id,
            },
        )

    async def _a1_update_vehicle_details(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = VehicleLeadUpdate.model_validate(arguments)
        lead = await BusinessActionRepository.find_most_recent_by_reference(
            db,
            organization_id=context.organization_id,
            request_type=A1_CREATE_LEAD,
            reference_id=args.reference_id,
        )
        if lead is None:
            raise BusinessToolExecutionError(_lead_error_message(args.reference_id))

        updated = dict(lead.payload_json)
        for name, value in args.model_dump(exclude={"reference_id"}).items():
            if value is not None:
                updated[name] = value
        await BusinessActionRepository.update_payload(db, lead, payload_json=updated)

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=f"Vehicle details updated for lead {args.reference_id}.",
            customer_visible=True,
            customer_message=(
                "Your vehicle details have been updated and saved for this "
                "pilot. No one has reviewed them yet."
            ),
            reference_id=lead.reference_id,
            metadata={"provider": LOCAL_DEMO_TAG},
        )

    async def _a1_request_vehicle_photos(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = VehiclePhotosRequest.model_validate(arguments)
        lead = await BusinessActionRepository.find_most_recent_by_reference(
            db,
            organization_id=context.organization_id,
            request_type=A1_CREATE_LEAD,
            reference_id=args.reference_id,
        )
        if lead is None:
            raise BusinessToolExecutionError(_lead_error_message(args.reference_id))

        updated = dict(lead.payload_json)
        updated["photos_requested"] = True
        await BusinessActionRepository.update_payload(db, lead, payload_json=updated)

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=(
                f"Photo request recorded for lead {args.reference_id}; "
                f"nothing was sent to A1."
            ),
            customer_visible=True,
            customer_message=(
                "Your request for vehicle photos has been recorded for this "
                "pilot. No photos were requested from anyone."
            ),
            reference_id=lead.reference_id,
            metadata={"provider": LOCAL_DEMO_TAG},
        )

    async def _a1_get_quote_status(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = QuoteStatusRequest.model_validate(arguments)
        lead = await BusinessActionRepository.find_most_recent_by_reference(
            db,
            organization_id=context.organization_id,
            request_type=A1_CREATE_LEAD,
            reference_id=args.reference_id,
        )
        if lead is None:
            raise BusinessToolExecutionError(_lead_error_message(args.reference_id))

        stage = _stage_for_lead(lead)
        # local_demo wording contract: these describe only what this system
        # actually did - it recorded a request and looked up a row. They never
        # assert that a quote exists, that anyone is preparing one, or that an
        # offer has been made, because no A1 backend and no human is on the other
        # end during the pilot. See the module docstring and
        # tests/test_a1_local_demo_wording.py.
        messages = {
            "quote_pending": (
                "Your request has been recorded for this pilot. No one has "
                "been contacted, and no quote has been prepared."
            ),
            "quote_in_preparation": (
                "Your request has been recorded for this pilot. No one has "
                "been contacted, and no quote has been prepared."
            ),
            "offer_available": (
                "Your request has been recorded for this pilot. No one has "
                "been contacted, and no offer has been made."
            ),
        }

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=(
                f"Quote lookup for lead {args.reference_id}: internal stage "
                f"{stage!r}, from the local_demo schedule. No quote or offer "
                f"exists."
            ),
            customer_visible=True,
            customer_message=messages[stage],
            reference_id=lead.reference_id,
            metadata={"provider": LOCAL_DEMO_TAG, "stage": stage},
        )

    async def _a1_check_pickup_availability(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = PickupAvailabilityRequest.model_validate(arguments)
        today = datetime.now(UTC).date()

        if args.preferred_date < today:
            raise BusinessToolExecutionError(
                "Pickup preferences must be for a future date."
            )

        candidate = args.preferred_date
        found: date | None = None
        windows: tuple[str, ...] = ()
        for _ in range(_MAX_AVAILABILITY_LOOKAHEAD_DAYS):
            open_windows = _available_windows_for(candidate)
            if args.preferred_window:
                if args.preferred_window in open_windows:
                    found = candidate
                    windows = (args.preferred_window,)
                    break
            elif open_windows:
                found = candidate
                windows = (open_windows[0],)
                break
            candidate += timedelta(days=1)

        if found is None:
            raise BusinessToolExecutionError(
                "No pickup availability could be found within the next two "
                "months. Please try a different date."
            )

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=(
                f"Typical pickup window {found.isoformat()} "
                f"({windows[0]}) from the local_demo schedule; nothing held."
            ),
            customer_visible=True,
            customer_message=(
                f"Based on our standard pickup pattern, {found.isoformat()} "
                f"({windows[0]}) is a typical window. Nothing has been held and "
                f"no one has been contacted."
            ),
            metadata={
                "provider": LOCAL_DEMO_TAG,
                "earliest_date": found.isoformat(),
                "windows": list(windows),
            },
        )

    async def _a1_create_pickup_request(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        args = PickupRequest.model_validate(arguments)
        lead = await BusinessActionRepository.find_most_recent_by_reference(
            db,
            organization_id=context.organization_id,
            request_type=A1_CREATE_LEAD,
            reference_id=args.reference_id,
        )
        if lead is None:
            raise BusinessToolExecutionError(_lead_error_message(args.reference_id))

        if args.preferred_window not in _available_windows_for(args.preferred_date):
            raise BusinessToolExecutionError(
                "Pickup is not available on the requested date. Check "
                "availability first and choose an open slot."
            )

        reference_id = _deterministic_reference(
            context,
            self.tool_name,
            suffix=args.preferred_date.isoformat(),
        )

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary=(
                f"Pickup REQUESTED for {args.preferred_date.isoformat()} "
                f"({args.preferred_window}) - not booked, pending human review."
            ),
            customer_visible=True,
            # The critical wording rule for local_demo: this records a request,
            # it does not book anything. No A1 backend receives this request, so
            # saying "your pickup is scheduled" would tell a customer a vehicle
            # is coming when nothing was ever arranged. "scheduled" is also
            # self-contradicting here, since the same sentence defers to a human.
            customer_message=(
                f"Your pickup request for {args.preferred_date.isoformat()} "
                f"({args.preferred_window}) has been recorded for this pilot. "
                f"Nothing was submitted to A1 and no one has been contacted, so "
                f"this is not a confirmed booking."
            ),
            reference_id=reference_id,
            metadata={
                "provider": LOCAL_DEMO_TAG,
                "pickup_date": args.preferred_date.isoformat(),
                "pickup_window": args.preferred_window,
            },
        )
