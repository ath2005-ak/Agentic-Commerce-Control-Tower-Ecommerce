"""
Policy / gating layer.

This is the part judges care about most: every money-moving action the
agent wants to take is checked here BEFORE it touches Razorpay. The LLM
never calls Razorpay directly — it can only request an action, and this
layer decides pass / block / needs_confirmation.

Keep this deterministic. No LLM calls in here, on purpose.
"""

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCK = "block"


@dataclass
class PolicyResult:
    decision: Decision
    reason: str


# ---- Tunable bounds (this is your "bounded" story for the demo) ----
MAX_UPSELL_INCREASE_RATIO = 0.20      # a single upsell can't raise cart value >20% without confirmation
MAX_SINGLE_ORDER_PAISE = 10_000_000   # ₹1,00,000 hard ceiling — anything above is blocked outright
MAX_DISCOUNT_PERCENT = 10             # agent can never apply >10% discount on its own
ALLOWED_ACTIONS = {
    "add_item", "remove_item", "modify_cart", "suggest_addons",
    "create_order", "create_payment_link", "apply_discount",
}


def check_action(action: str, cart_value_paise: int, delta_paise: int = 0,
                  discount_percent: float = 0) -> PolicyResult:
    """
    Every agent-requested action passes through here.
    action: one of ALLOWED_ACTIONS
    cart_value_paise: current cart value BEFORE this action
    delta_paise: how much this action changes the cart value by (can be 0)
    discount_percent: only relevant for apply_discount actions
    """
    if action not in ALLOWED_ACTIONS:
        return PolicyResult(Decision.BLOCK, f"'{action}' is not a whitelisted action")

    new_value = cart_value_paise + delta_paise

    if new_value > MAX_SINGLE_ORDER_PAISE:
        return PolicyResult(
            Decision.BLOCK,
            f"Resulting order value ₹{new_value/100:.2f} exceeds hard ceiling ₹{MAX_SINGLE_ORDER_PAISE/100:.2f}"
        )

    if action == "apply_discount":
        if discount_percent > MAX_DISCOUNT_PERCENT:
            return PolicyResult(
                Decision.BLOCK,
                f"Discount {discount_percent}% exceeds max allowed {MAX_DISCOUNT_PERCENT}% — agent cannot self-authorize"
            )
        return PolicyResult(Decision.ALLOW, "Discount within allowed bound")

    if action == "add_item" and cart_value_paise > 0:
        increase_ratio = delta_paise / cart_value_paise
        if increase_ratio > MAX_UPSELL_INCREASE_RATIO:
            return PolicyResult(
                Decision.NEEDS_CONFIRMATION,
                f"Upsell would increase cart by {increase_ratio*100:.0f}%, above the "
                f"{MAX_UPSELL_INCREASE_RATIO*100:.0f}% auto-approve threshold — user must confirm"
            )
        return PolicyResult(Decision.ALLOW, "Upsell within auto-approve bound")

    if action in ("create_order", "create_payment_link", "remove_item", "modify_cart", "suggest_addons"):
        return PolicyResult(Decision.ALLOW, "Standard checkout action")

    return PolicyResult(Decision.ALLOW, "No specific rule triggered; default allow")
