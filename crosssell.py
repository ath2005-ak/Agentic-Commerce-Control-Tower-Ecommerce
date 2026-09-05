"""
Cross-sell / upsell suggestion engine.

Scoring is deliberately simple and inspectable: co-occurrence affinity from
catalog.json, weighted by margin and by how well the candidate's price fits
the current cart's price band. Nothing here is a black box — every score
factor shows up in the reason string the agent surfaces to the user and
writes to the audit log.
"""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / "catalog.json"

with open(CATALOG_PATH) as f:
    _catalog = json.load(f)

_products_by_id = {p["id"]: p for p in _catalog["products"]}
_pairs = _catalog["co_purchase_pairs"]

MARGIN_WEIGHT = 0.02      # each margin point adds this much to the score
PRICE_BAND_PENALTY = 0.35  # penalty applied when candidate is priced way outside cart's band


def _price_band_fit(candidate_price: int, cart_prices: list[int]) -> float:
    """1.0 = fits the cart's price band well, lower = increasingly out of place."""
    if not cart_prices:
        return 1.0
    avg_cart_price = sum(cart_prices) / len(cart_prices)
    if avg_cart_price == 0:
        return 1.0
    ratio = candidate_price / avg_cart_price
    # Add-ons priced far above or below the cart's average feel mismatched
    if ratio > 1.0:
        return max(0.0, 1.0 - PRICE_BAND_PENALTY * (ratio - 1.0))
    return max(0.0, 1.0 - PRICE_BAND_PENALTY * (1.0 - ratio) * 0.3)


def suggest_addons(cart_item_ids: list[str], max_suggestions: int = 2) -> list[dict]:
    """
    Given the SKUs currently in cart, return ranked add-on suggestions with
    an explicit reason string covering affinity, margin, and price fit —
    this is the explainability hook the agent surfaces to the user and logs.
    """
    cart_set = set(cart_item_ids)
    cart_prices = [_products_by_id[i]["price_paise"] for i in cart_item_ids if i in _products_by_id]

    affinity: dict[str, dict] = {}
    for item_id in cart_item_ids:
        for a, b in _pairs:
            partner = None
            if a == item_id and b not in cart_set:
                partner = b
            elif b == item_id and a not in cart_set:
                partner = a
            if partner:
                entry = affinity.setdefault(partner, {"co_occurrence": 0, "because_of": set()})
                entry["co_occurrence"] += 1
                entry["because_of"].add(item_id)

    scored = []
    for sku_id, info in affinity.items():
        product = _products_by_id[sku_id]
        margin = product.get("margin_percent", 0)
        band_fit = _price_band_fit(product["price_paise"], cart_prices)
        score = info["co_occurrence"] + (margin * MARGIN_WEIGHT) + band_fit
        scored.append((sku_id, info, product, margin, band_fit, score))

    scored.sort(key=lambda row: row[5], reverse=True)

    suggestions = []
    for sku_id, info, product, margin, band_fit, score in scored[:max_suggestions]:
        anchor_names = [_products_by_id[a]["name"] for a in info["because_of"]]
        reason_parts = [f"frequently bought with {', '.join(anchor_names)}"]
        if margin >= 45:
            reason_parts.append(f"high margin ({margin}%)")
        if band_fit < 0.8:
            reason_parts.append("priced outside the cart's usual range")
        suggestions.append({
            "sku_id": sku_id,
            "name": product["name"],
            "price_paise": product["price_paise"],
            "description": product.get("description", ""),
            "margin_percent": margin,
            "reason": "; ".join(reason_parts),
            "score": round(score, 3),
        })
    return suggestions
