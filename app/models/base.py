from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # Phase 1S: fetch server-generated values (ids, timestamps, onupdate) in the
    # INSERT/UPDATE's own RETURNING, so a write needs no follow-up SELECT to be
    # readable. Combined with ``refresh_if_stale`` this removes one database
    # round trip per repository write.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
