"""Insert-or-detect-duplicate inside a SAVEPOINT.

Several worker paths insert one row and treat a unique-constraint collision as
"a concurrent writer got there first": they re-query the winner and carry on.
Doing that with ``db.rollback()`` is wrong inside a larger unit of work:

- the rollback discards every OTHER pending change of the caller's
  transaction, silently, while the function returns as if it succeeded;
- it expires every instance loaded in the session (``expire_on_commit=False``
  only covers commit), so the next attribute read on any of them is an
  implicit sync refresh, which an ``AsyncSession`` rejects with
  ``MissingGreenlet``.

A SAVEPOINT confines the collision to the one INSERT: only the savepoint is
rolled back, the new instance is expunged, and nothing else is expired or lost.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_in_savepoint(db: AsyncSession, *instances: object) -> IntegrityError | None:
    """Add and flush ``instances`` inside a SAVEPOINT.

    Returns ``None`` on success (the rows are flushed, not committed: the caller
    keeps ownership of the transaction) or the ``IntegrityError`` on a
    collision, after which the outer transaction is still usable and every
    previously loaded object is still intact.
    """
    # Flush the caller's own pending work FIRST, outside the savepoint:
    # ``begin_nested()`` would otherwise flush it inside the ``try`` and a
    # unique violation in the caller's work would be misreported as "our
    # insert lost a race". The caller's error must surface as-is.
    await db.flush()
    try:
        async with db.begin_nested():
            db.add_all(instances)
    except IntegrityError as exc:
        return exc
    return None
