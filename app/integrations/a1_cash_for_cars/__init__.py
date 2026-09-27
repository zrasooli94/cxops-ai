"""A1 Cash for Cars integration package (Phase 1P.2).

Importing this package (or ``app.tools.registry``) registers the A1 business
tools and their authorization policies. A1 is a local-demo provider: no real
API is called, and every result is tagged accordingly.
"""

from app.integrations.a1_cash_for_cars import service, tools

__all__ = ["service", "tools"]