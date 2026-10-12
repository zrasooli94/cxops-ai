import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.zendesk_oauth_service import (
    ZendeskOAuthService,
)


class ZendeskAPIError(Exception):
    """A non-2xx Zendesk response. ``status_code`` lets callers classify it."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ZendeskClient:
    def __init__(self) -> None:
        self.base_url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2"

    async def request(
        self,
        db: AsyncSession,
        method: str,
        path: str,
        *,
        organization_id: int,
        retry_on_unauthorized: bool = True,
        **kwargs,
    ) -> dict:
        """Per-org request: credential lookup is scoped to the organization.

        The scope of the credential used for the Zendesk call is the passed
        ``organization_id``. This is the ONLY path that selects an access token.
        """

        token = await ZendeskOAuthService.get_valid_token(
            db,
            organization_id,
        )

        headers = {
            "Authorization": (f"Bearer {token.access_token}"),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=20.0,
        ) as client:
            response = await client.request(
                method=method,
                url=path,
                headers=headers,
                **kwargs,
            )

        if (
            response.status_code == 401
            and retry_on_unauthorized
            and token.refresh_token
        ):
            await ZendeskOAuthService.refresh_access_token(
                db=db,
                refresh_token=token.refresh_token,
                organization_id=organization_id,
            )

            return await self.request(
                db=db,
                method=method,
                path=path,
                organization_id=organization_id,
                retry_on_unauthorized=False,
                **kwargs,
            )

        if response.is_error:
            raise ZendeskAPIError(
                f"Zendesk API error {response.status_code}",
                status_code=response.status_code,
            )

        if not response.content:
            return {}

        return response.json()

    async def get_current_user(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            "/users/me.json",
            organization_id=organization_id,
        )

    async def get_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        *,
        organization_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            f"/tickets/{ticket_id}.json",
            organization_id=organization_id,
        )

    async def create_ticket(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        subject: str,
        comment: str,
        requester_name: str | None = None,
        requester_email: str | None = None,
        priority: str | None = None,
    ) -> dict:

        ticket: dict = {
            "subject": subject,
            "comment": {
                "body": comment,
            },
        }

        if requester_email and requester_name:
            ticket["requester"] = {
                "name": requester_name,
                "email": requester_email,
            }

        if priority:
            ticket["priority"] = priority

        return await self.request(
            db,
            "POST",
            "/tickets.json",
            organization_id=organization_id,
            json={
                "ticket": ticket,
            },
        )

    async def update_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        changes: dict,
        *,
        organization_id: int,
    ) -> dict:

        return await self.request(
            db,
            "PUT",
            f"/tickets/{ticket_id}.json",
            organization_id=organization_id,
            json={
                "ticket": changes,
            },
        )

    async def get_user(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        organization_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            f"/users/{user_id}.json",
            organization_id=organization_id,
        )

    async def get_ticket_comments(
        self,
        db: AsyncSession,
        ticket_id: int,
        *,
        organization_id: int,
        newest_first: bool = False,
    ) -> dict:
        """One page (at most 100) of a ticket's comments.

        Zendesk returns comments oldest first by default, so on a ticket with
        more than one page the newest comments are NOT in the response.
        Exactly-once marker scans look for a comment this system just wrote and
        must pass ``newest_first=True`` (``sort_order=desc``).
        """
        extra: dict = {"params": {"sort_order": "desc"}} if newest_first else {}
        return await self.request(
            db,
            "GET",
            f"/tickets/{ticket_id}/comments.json",
            organization_id=organization_id,
            **extra,
        )

    async def get_ticket_comments_page(
        self,
        db: AsyncSession,
        ticket_id: int,
        *,
        organization_id: int,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """One cursor-paginated page of comments, NEWEST FIRST.

        ``page[size]`` (max 100) + ``sort=-created_at``; follow
        ``meta.after_cursor`` while ``meta.has_more`` is true. Used by the
        exactly-once marker scans (``app/integrations/zendesk/marker_scan.py``).
        """
        params: dict[str, str | int] = {
            "page[size]": max(1, min(page_size, 100)),
            "sort": "-created_at",
        }
        if cursor:
            params["page[after]"] = cursor
        return await self.request(
            db,
            "GET",
            f"/tickets/{ticket_id}/comments.json",
            organization_id=organization_id,
            params=params,
        )

    async def apply_agent_action(
        self,
        db,
        ticket_id: int,
        *,
        organization_id: int,
        priority: str | None = None,
        comment: str | None = None,
        public: bool = False,
        group_id: int | None = None,
    ):
        ticket_payload: dict = {}

        if priority:
            ticket_payload["priority"] = priority

        if group_id is not None:
            ticket_payload["group_id"] = group_id

        if comment:
            ticket_payload["comment"] = {
                "body": comment,
                "public": public,
            }

        return await self.request(
            db,
            "PUT",
            f"/tickets/{ticket_id}.json",
            organization_id=organization_id,
            json={"ticket": ticket_payload},
        )

    async def get_groups(
        self,
        db,
        *,
        organization_id: int,
    ):
        return await self.request(
            db,
            "GET",
            "/groups.json",
            organization_id=organization_id,
        )

    async def find_group_id(
        self,
        db,
        group_name: str,
        *,
        organization_id: int,
    ) -> int | None:

        response = await self.get_groups(
            db,
            organization_id=organization_id,
        )

        groups = response.get(
            "groups",
            [],
        )

        wanted = group_name.lower().replace("-", " ").strip()

        for group in groups:
            actual = str(group.get("name", "")).lower().replace("-", " ").strip()

            if actual == wanted:
                return int(group["id"])

        return None


zendesk_client = ZendeskClient()
