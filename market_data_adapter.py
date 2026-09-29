"""Single integration boundary for the proprietary vnstock/vnai packages.

Application modules must not import vnstock or vnai directly. Keeping the
vendor API behind this module makes package upgrades and source replacement a
contained change and gives tests one stable seam to mock.
"""

from __future__ import annotations

from typing import Any
import importlib.util


class VendorPackageUnavailable(RuntimeError):
    """Raised when the configured vendor client cannot be imported."""


def provider_availability():
    """Return a non-throwing status suitable for unattended dashboards."""
    try:
        available = importlib.util.find_spec("vnstock") is not None
    except (ImportError, ValueError):
        available = False
    return {
        "available": available,
        "status": "ok" if available else "degraded",
        "detail": "vnstock available" if available else "vnstock unavailable; cached/non-vendor data only",
    }


def _vendor_class(module: str, name: str):
    try:
        imported = __import__(module, fromlist=[name])
        return getattr(imported, name)
    except (ImportError, AttributeError) as exc:
        raise VendorPackageUnavailable(
            "vnstock is unavailable or incompatible; install the pinned vendor requirements"
        ) from exc


def quote(symbol: str, source: str = "VCI") -> Any:
    return _vendor_class("vnstock.api.quote", "Quote")(
        symbol=str(symbol).upper(), source=source
    )


def quote_history(symbol: str, source: str, **kwargs):
    return quote(symbol, source).history(**kwargs)


def trading(symbol: str, source: str = "VCI", **kwargs) -> Any:
    return _vendor_class("vnstock.api.trading", "Trading")(
        symbol=str(symbol).upper(), source=source, **kwargs
    )


def finance(symbol: str, source: str = "VCI", **kwargs) -> Any:
    return _vendor_class("vnstock.api.financial", "Finance")(
        symbol=str(symbol).upper(), source=source, **kwargs
    )


def vendor_status(*args, **kwargs):
    return _vendor_class("vnstock", "check_status")(*args, **kwargs)


def vnstock_client(*args, **kwargs):
    return _vendor_class("vnstock", "Vnstock")(*args, **kwargs)


def market_events() -> dict:
    """vnstock's table of VN market holidays ({ISO date: {"event", "type"}})."""
    return dict(_vendor_class("vnstock.core.utils.market_events", "MARKET_EVENTS"))
