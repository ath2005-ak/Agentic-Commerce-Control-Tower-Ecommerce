"""
Conversational agent loop: Propose -> Confirm -> Act.

Default mode is a small rule-based intent parser — no external LLM
dependency, so the demo runs offline and deterministically during judging.
If OPENAI_API_KEY or GOOGLE_API_KEY is present in the environment, the
`interpret()` function is the single seam where you'd swap in a real LLM
call to replace keyword matching with proper intent understanding —
everything downstream (confirmation handling, action execution) is
already LLM-agnostic and stays the same either way.

The agent NEVER calls actions.py's mutating functions directly without
going through this proposal step for anything policy flagged as
needs_confirmation — see handle_message() below.
"""

import os
import re

import store
import actions
from crosssell import _products_by_id, suggest_addons

USE_LLM = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

_CATALOG_INDEX = [
    (sku_id, p["name"].lower()) for sku_id, p in _products_by_id.items()
]

_AFFIRM = {"yes", "y", "confirm", "sure", "ok", "okay", "do it", "go ahead", "accept"}
_DENY = {"no", "n", "cancel", "nevermind", "never mind", "decline", "skip"}


def _find_product(text: str) -> str | None:
    text = text.lower()
    # exact id match first
    for sku_id in _products_by_id:
        if sku_id in text:
            return sku_id
    # substring name match, longest name first so "vela book 14 sleeve" beats "vela book 14"
    for sku_id, name in sorted(_CATALOG_INDEX, key=lambda t: -len(t[1])):
        if name in text:
            return sku_id
    # loose word-overlap fallback
    words = set(re.findall(r"[a-z0-9]+", text))
    best, best_overlap = None, 0
    for sku_id, name in _CATALOG_INDEX:
        overlap = len(words & set(re.findall(r"[a-z0-9]+", name)))
        if overlap > best_overlap:
            best, best_overlap = sku_id, overlap
    return best if best_overlap >= 1 else None


def interpret(message: str, session_id: str) -> dict:
    """
    Returns {"intent": ..., "sku_id": ...}. This is the seam to replace
    with a real LLM function-calling call if USE_LLM is True — same
    return shape either way.
    """
    text = message.strip().lower()

    if session_id in store.pending_confirmations:
        if any(a in text for a in _AFFIRM):
            return {"intent": "confirm"}
        if any(d in text for d in _DENY):
            return {"intent": "deny"}

    if any(w in text for w in ("checkout", "buy now", "place order", "pay now", "complete order")):
        return {"intent": "checkout"}
    if any(w in text for w in ("suggest", "recommend", "what goes well", "anything else", "upsell", "pair")):
        return {"intent": "suggest"}
    if text.startswith("remove") or "remove " in text or "take out" in text:
        return {"intent": "remove", "sku_id": _find_product(text)}
    if any(w in text for w in ("add ", "i want", "i'll take", "get me", "buy ")) or _find_product(text):
        return {"intent": "add", "sku_id": _find_product(text)}
    if any(w in text for w in ("cart", "what's in", "show cart")):
        return {"intent": "view_cart"}

    return {"intent": "unknown"}


def handle_message(session_id: str, message: str) -> dict:
    parsed = interpret(message, session_id)
    intent = parsed["intent"]

    if intent == "confirm" and session_id in store.pending_confirmations:
        pending = store.pending_confirmations.pop(session_id)
        result = actions.add_item(session_id, pending["sku_id"], reasoning=pending["reasoning"],
                                   force_confirmed=True)
        return _reply(f"Added {_products_by_id[pending['sku_id']]['name']} to your cart.", result)

    if intent == "deny" and session_id in store.pending_confirmations:
        store.pending_confirmations.pop(session_id)
        return _reply("No problem, I'll leave that out.", None)

    if intent == "add":
        sku_id = parsed.get("sku_id")
        if not sku_id:
            return _reply("I couldn't match that to an item in the catalog — try naming the product.", None)
        result = actions.add_item(session_id, sku_id, reasoning=f"user message: '{message}'")
        if result.status == "needs_confirmation":
            product = _products_by_id[sku_id]
            return _reply(
                f"That's a bigger jump than my usual auto-approve limit ({result.message}). "
                f"Add {product['name']} anyway?", result,
            )
        if result.status == "blocked":
            return _reply(f"Can't do that: {result.message}", result)
        return _reply(result.message + ". Want me to suggest anything that pairs well with it?", result)

    if intent == "remove":
        sku_id = parsed.get("sku_id")
        if not sku_id:
            return _reply("Which item should I remove?", None)
        result = actions.remove_item(session_id, sku_id)
        return _reply(result.message, result)

    if intent == "suggest":
        suggestions = actions.get_suggestions(session_id)
        if not suggestions:
            return _reply("Nothing obvious pairs with your current cart yet.", None, suggestions=[])
        lines = [f"{s['name']} — {s['reason']}" for s in suggestions]
        return _reply("Here's what I'd suggest: " + "; ".join(lines), None, suggestions=suggestions)

    if intent == "view_cart":
        cart = store.get_cart(session_id)
        if not cart:
            return _reply("Your cart is empty.", None)
        names = [_products_by_id[s]["name"] for s in cart]
        value = store.cart_value_paise(session_id, _products_by_id)
        return _reply(f"In your cart: {', '.join(names)} — ₹{value/100:,.2f} total.", None)

    if intent == "checkout":
        result = actions.checkout(session_id)
        return _reply(result.message, result)

    return _reply(
        "I can add items, suggest pairings, or check out — try 'add Aria X12' or 'suggest something'.",
        None,
    )


def _reply(text: str, result, suggestions=None) -> dict:
    return {
        "reply": text,
        "result": result.__dict__ if result else None,
        "suggestions": suggestions,
    }
