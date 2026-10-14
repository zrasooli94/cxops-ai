# Taken first, before any other application import, so the boot-time metric
# measures the application's whole import phase (see ``app.core.build_info``).
from app.core import build_info as _build_info  # noqa: F401
