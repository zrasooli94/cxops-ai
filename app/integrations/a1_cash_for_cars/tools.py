"""A1 Cash for Cars business tool registration (Phase 1P.2).

Registers the six A1 tools in the shared registry and their policies in
``ToolAuthorizationService`` so the existing authorization pipeline governs
them. All tools ``requires_approval=True``: Phase 1P.2 never auto-executes a
business tool from any channel, even though the policy engine could express
auto-authorization.
"""

from app.integrations.a1_cash_for_cars.schemas import (
    PickupAvailabilityRequest,
    PickupRequest,
    QuoteStatusRequest,
    VehicleLead,
    VehicleLeadUpdate,
    VehiclePhotosRequest,
)
from app.integrations.a1_cash_for_cars.service import A1CashForCarsExecutor
from app.services.tool_authorization_service import ToolAuthorizationService
from app.tools.base import (
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    BusinessToolDefinition,
)
from app.tools.registry import (
    PROVIDER_A1_CASH_FOR_CARS,
    business_tool_registry,
)

_A1_TOOLS: list[tuple[str, str, str, type]] = [
    (
        "a1.create_vehicle_lead",
        "Create a vehicle listing and start a purchase/quote lead.",
        RISK_MEDIUM,
        VehicleLead,
    ),
    (
        "a1.update_vehicle_details",
        (
            "Update vehicle details (variant, odometer, condition, contacts) "
            "on an existing A1 lead by its reference."
        ),
        RISK_MEDIUM,
        VehicleLeadUpdate,
    ),
    (
        "a1.request_vehicle_photos",
        "Request vehicle photos for an existing A1 lead by its reference.",
        RISK_MEDIUM,
        VehiclePhotosRequest,
    ),
    (
        "a1.get_quote_status",
        (
            "Report the current quote stage for an existing A1 lead "
            "reference. Never returns pricing."
        ),
        RISK_LOW,
        QuoteStatusRequest,
    ),
    (
        "a1.check_pickup_availability",
        "Find the first available pickup slot at or after a preferred date.",
        RISK_LOW,
        PickupAvailabilityRequest,
    ),
    (
        "a1.create_pickup_request",
        "Schedule a vehicle pickup for an existing A1 lead reference.",
        RISK_HIGH,
        PickupRequest,
    ),
]


def _register() -> None:
    for name, description, risk_level, model in _A1_TOOLS:
        executor = A1CashForCarsExecutor(name)
        definition = BusinessToolDefinition(
            name=name,
            provider=PROVIDER_A1_CASH_FOR_CARS,
            description=description,
            risk_level=risk_level,
            requires_approval=True,
            required_capability=None,
            argument_model=model,
            executor=executor,
        )
        business_tool_registry.register(definition)

        ToolAuthorizationService.register_policy(
            name,
            {
                "risk_level": risk_level,
                "requires_approval": True,
                "auto_authorize": False,
                "required_capability": None,
                "argument_schema": definition.argument_schema,
                "business_tool": True,
                "provider": PROVIDER_A1_CASH_FOR_CARS,
                "description": description,
            },
        )


_register()