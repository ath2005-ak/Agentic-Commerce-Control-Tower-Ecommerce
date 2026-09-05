"""
Core business actions. Every money-shaped or cart-shaped action goes
through here: policy check -> audit log -> (maybe) execute. Both the REST
API (main.py) and the conversational agent (agent.py) call these same
functions, so there's exactly one place the gating logic can be bypassed
from — nowhere.
"""

from dataclasses import dataclass, field

import policy
import audit
import store
import crosssell
from crosssell import _products_by_id
from razorpay_client import gateway, RazorpayError


@dataclass
class ActionResult:
    ok: bool
    status: str            # "added" | "needs_confirmation" | "blocked" | "checked_out" | "failed"
    message: str
    data: dict = field(default_factory=dict)


def add_item(session_id: str, sku_id: str, reasoning: str = "user requested",
             force_confirmed: bool = False) -> ActionResult:
    if sku_id not in _products_by_id:
        return ActionResult(False, "blocked", f"'{sku_id}' isn't in the catalog", {})

    cart_value = store.cart_value_paise(session_id, _products_by_id)
    delta = _products_by_id[sku_id]["price_paise"]

    action = "add_item"
    result = policy.check_action(action, cart_value_paise=cart_value, delta_paise=delta)

    # A previously-confirmed NEEDS_CONFIRMATION action is allowed through explicitly.
    effective_decision = policy.Decision.ALLOW if force_confirmed else result.decision

    audit.log_event(
        session_id=session_id, action=action, reasoning=reasoning,
        policy_decision=effective_decision.value, policy_reason=result.reason,
        input_data={"sku_id": sku_id, "cart_value_before": cart_value},
        output_data={}, status=effective_decision.value,
    )

    if effective_decision == policy.Decision.BLOCK:
        return ActionResult(False, "blocked", result.reason, {})

    if effective_decision == policy.Decision.NEEDS_CONFIRMATION:
        store.pending_confirmations[session_id] = {"sku_id": sku_id, "reasoning": reasoning}
        return ActionResult(True, "needs_confirmation", result.reason, {"sku_id": sku_id})

    store.get_cart(session_id).append(sku_id)
    store.pending_confirmations.pop(session_id, None)
    new_value = store.cart_value_paise(session_id, _products_by_id)
    return ActionResult(True, "added", f"Added {_products_by_id[sku_id]['name']}", {
        "cart": store.get_cart(session_id), "cart_value_paise": new_value,
    })


def remove_item(session_id: str, sku_id: str) -> ActionResult:
    cart = store.get_cart(session_id)
    if sku_id not in cart:
        return ActionResult(False, "blocked", "Item not in cart", {})
    cart.remove(sku_id)
    audit.log_event(
        session_id=session_id, action="remove_item", reasoning="user requested",
        policy_decision="allow", policy_reason="Standard checkout action",
        input_data={"sku_id": sku_id}, output_data={}, status="allow",
    )
    return ActionResult(True, "removed", f"Removed {_products_by_id[sku_id]['name']}", {
        "cart": cart, "cart_value_paise": store.cart_value_paise(session_id, _products_by_id),
    })


def get_suggestions(session_id: str) -> list[dict]:
    cart = store.get_cart(session_id)
    suggestions = crosssell.suggest_addons(cart)
    audit.log_event(
        session_id=session_id, action="suggest_addons", reasoning="cross-sell lookup",
        policy_decision="allow", policy_reason="Read-only recommendation, no state change",
        input_data={"cart": cart}, output_data={"suggestions": suggestions}, status="allow",
    )
    return suggestions


def checkout(session_id: str, force_failure: bool = False) -> ActionResult:
    cart = store.get_cart(session_id)
    if not cart:
        return ActionResult(False, "blocked", "Cart is empty", {})

    cart_value = store.cart_value_paise(session_id, _products_by_id)
    result = policy.check_action("create_order", cart_value_paise=cart_value, delta_paise=0)

    if result.decision != policy.Decision.ALLOW:
        audit.log_event(
            session_id=session_id, action="create_order", reasoning="checkout attempt",
            policy_decision=result.decision.value, policy_reason=result.reason,
            input_data={"cart_value_paise": cart_value}, output_data={}, status="blocked",
        )
        return ActionResult(False, "blocked", result.reason, {})

    try:
        order = gateway.create_order(cart_value, session_id, force_failure=force_failure)
        link = gateway.create_payment_link(
            cart_value, order["id"], "Agentic commerce demo order", force_failure=force_failure
        )
    except RazorpayError as e:
        audit.log_event(
            session_id=session_id, action="create_order", reasoning="checkout attempt",
            policy_decision="allow", policy_reason=f"gateway error: {e.code}",
            input_data={"cart_value_paise": cart_value}, output_data={"error": str(e)}, status="failed",
        )
        return ActionResult(False, "failed",
                             f"Payment provider error ({e.code}): {e}. Not retried automatically — "
                             f"you can retry checkout or reference session {session_id} with support.",
                             {"error_code": e.code})

    cart_items = [_products_by_id[s] for s in cart if s in _products_by_id]
    link_id = link.get("id")
    if link_id:
        store.payment_links[link_id] = {
            "link_id": link_id,
            "order_id": order["id"],
            "session_id": session_id,
            "amount_paise": cart_value,
            "currency": "INR",
            "description": "Agentic Commerce Order",
            "items": cart_items,
            "status": "created",
            "short_url": link.get("short_url"),
        }

    audit.log_event(
        session_id=session_id, action="create_order", reasoning="checkout success",
        policy_decision="allow", policy_reason="Order and payment link created",
        input_data={"cart_value_paise": cart_value},
        output_data={"order_id": order["id"], "payment_link": link.get("short_url")}, status="success",
    )
    return ActionResult(True, "checked_out", "Order placed", {
        "order_id": order["id"], "payment_link": link.get("short_url"), "amount_paise": cart_value, "payment_link_id": link_id,
        "cart": list(cart), "cart_value_paise": cart_value
    })
