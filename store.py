"""
In-memory session state: carts and pending (needs_confirmation) actions.
Fine for a hackathon demo — single process, no persistence needed beyond
the audit log. Swap for Redis if you ever need multi-instance.
"""

carts: dict[str, list[str]] = {}
pending_confirmations: dict[str, dict] = {}  # session_id -> {"sku_id": ..., "delta_paise": ...}
payment_links: dict[str, dict] = {}  # link_id -> payment link data


def get_cart(session_id: str) -> list[str]:
    return carts.setdefault(session_id, [])


def cart_value_paise(session_id: str, products_by_id: dict) -> int:
    return sum(products_by_id[sku]["price_paise"] for sku in carts.get(session_id, []))


def reset(session_id: str):
    carts.pop(session_id, None)
    pending_confirmations.pop(session_id, None)

