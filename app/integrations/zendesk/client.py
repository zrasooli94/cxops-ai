import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.zendesk_oauth_service import (
    ZendeskOAuthService,
    ZendeskReauthorizationRequired,
)


class ZendeskAPIError(Exception):
    pass


class ZendeskClient:

    def __init__(self) -> None:
        self.base_url = (
            f"https://{settings.zendesk_subdomain}"
            ".zendesk.com/api/v2"
        )

    async def request(
        self,
        db: AsyncSession,
        method: str,
        path: str,
        *,
        retry_on_unauthorized: bool = True,
        **kwargs,
    ) -> dict:

        token = await ZendeskOAuthService.get_valid_token(
            db
        )

        headers = {
            "Authorization": (
                f"Bearer {token.access_token}"
            ),
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
            )

            return await self.request(
                db=db,
                method=method,
                path=path,
                retry_on_unauthorized=False,
                **kwargs,
            )

        if response.is_error:
            raise ZendeskAPIError(
                f"Zendesk API error "
                f"{response.status_code}: "
                f"{response.text}"
            )

        if not response.content:
            return {}

        return response.json()

    async def get_current_user(
        self,
        db: AsyncSession,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            "/users/me.json",
        )

    async def get_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            f"/tickets/{ticket_id}.json",
        )

    async def create_ticket(
        self,
        db: AsyncSession,
        *,
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
            json={
                "ticket": ticket,
            },
        )

    async def update_ticket(
        self,
        db: AsyncSession,
        ticket_id: int,
        changes: dict,
    ) -> dict:

        return await self.request(
            db,
            "PUT",
            f"/tickets/{ticket_id}.json",
            json={
                "ticket": changes,
            },
        )

    async def get_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            f"/users/{user_id}.json",
        )

    async def get_ticket_comments(
        self,
        db: AsyncSession,
        ticket_id: int,
    ) -> dict:

        return await self.request(
            db,
            "GET",
            f"/tickets/{ticket_id}/comments.json",
        )

    

zendesk_client = ZendeskClient()