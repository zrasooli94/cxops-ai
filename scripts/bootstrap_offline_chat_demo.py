"""Seed a synthetic, disposable tenant for the local real-backend demo."""

from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import LIFECYCLE_ACTIVE, Organization
from app.models.public_chat import PublicChatConfiguration
from app.services.public_chat_service import hash_digest

WIDGET_KEY = "pk_local_aster_cycle_offline_demo"
ORG_EXTERNAL_ID = "cxops-local-offline-demo"
KNOWLEDGE = (
    (
        "Opening hours | ساعات کاری",
        (
            "Our bicycle workshop is open Monday to Friday from 9:00 AM to 6:00 PM, "
            "Saturday from 10:00 AM to 4:00 PM, and closed Sunday. "
            "ساعات کاری تعمیرگاه دوچرخه دوشنبه تا جمعه ۹ صبح تا ۶ عصر، شنبه ۱۰ صبح تا ۴ عصر است و یکشنبه تعطیل است."
        ),
    ),
    (
        "Workshop services | خدمات",
        (
            "We offer bicycle tune-ups, puncture repairs, safety inspections, and accessory sales. "
            "خدمات ما شامل سرویس دوچرخه، تعمیر پنچری، بررسی ایمنی و فروش لوازم جانبی است."
        ),
    ),
    (
        "Pricing | قیمت",
        (
            "A standard bicycle tune-up costs RM 80. A puncture repair starts at RM 25. "
            "Other repairs require an inspection before a quote. هزینه سرویس معمولی دوچرخه ۸۰ رینگیت "
            "و تعمیر پنچری از ۲۵ رینگیت شروع می‌شود."
        ),
    ),
    (
        "Returns and warranty | بازگشت و ضمانت",
        (
            "Unused accessories may be returned within 14 days with proof of purchase. "
            "Repair workmanship is covered for 90 days; accidental damage and normal wear are excluded. "
            "لوازم جانبی استفاده‌نشده تا ۱۴ روز با رسید قابل بازگشت‌اند و کیفیت تعمیرات ۹۰ روز ضمانت دارد."
        ),
    ),
)


def _require_disposable_local_database() -> None:
    if settings.environment == "production":
        raise RuntimeError("The offline demo seed is disabled in production.")
    host = urlsplit(settings.database_url.replace("postgresql+asyncpg", "postgresql")).hostname
    if host not in {"localhost", "127.0.0.1", None}:
        raise RuntimeError("The offline demo seed requires a loopback database host.")


async def seed() -> None:
    _require_disposable_local_database()
    async with AsyncSessionLocal() as db:
        organization = await db.scalar(
            select(Organization).where(Organization.external_id == ORG_EXTERNAL_ID)
        )
        if organization is None:
            organization = Organization(
                name="Aster Cycle Studio (local synthetic)",
                external_id=ORG_EXTERNAL_ID,
                industry="Fictional bicycle workshop",
                lifecycle_status=LIFECYCLE_ACTIVE,
            )
            db.add(organization)
            await db.flush()

        configuration = await db.scalar(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == organization.id
            )
        )
        if configuration is None:
            configuration = PublicChatConfiguration(organization_id=organization.id)
            db.add(configuration)
        configuration.public_widget_key_hash = hash_digest(WIDGET_KEY)
        configuration.display_name = "Aster Cycle Studio"
        configuration.welcome_message = (
            "Hi! Ask about our fictional workshop. This local demo uses the real "
            "CXOps backend and an offline grounded provider, not a paid AI model."
        )
        configuration.enabled = True
        configuration.grounded_auto_reply_enabled = True
        configuration.async_turns_enabled = False
        configuration.allowed_origins = [
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ]
        configuration.theme_token = "default"
        configuration.max_message_length = 1000
        configuration.max_messages_per_minute = 60
        configuration.session_ttl_hours = 24
        await db.flush()

        document_ids = select(KnowledgeDocument.id).where(
            KnowledgeDocument.organization_id == organization.id
        )
        await db.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.organization_id == organization.id
            )
        )
        await db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids)))
        for index, (title, content) in enumerate(KNOWLEDGE):
            document = KnowledgeDocument(
                organization_id=organization.id,
                title=title,
                source="local_synthetic",
                checksum=hashlib.sha256(content.encode()).hexdigest(),
                metadata_json={"demo": True, "synthetic": True},
            )
            db.add(document)
            await db.flush()
            db.add(
                KnowledgeChunk(
                    organization_id=organization.id,
                    document_id=document.id,
                    chunk_index=index,
                    content=content,
                    token_count=len(content.split()),
                    metadata_json={"demo": True, "synthetic": True},
                    embedding=[0.0] * 1536,
                )
            )
        await db.commit()
        print(f"Seeded organization_id={organization.id} widget_key={WIDGET_KEY}")


if __name__ == "__main__":
    asyncio.run(seed())
