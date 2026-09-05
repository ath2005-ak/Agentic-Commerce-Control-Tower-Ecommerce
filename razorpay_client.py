"""
Razorpay client wrapper.

Dual mode, on purpose: if RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are set in
the environment, this makes real test-mode API calls. If they're absent,
it drops into mock mode automatically — same function signatures, fake
but realistic responses — so the whole demo runs end to end with zero
setup, and the pitch isn't hostage to whether keys were configured in
time.

Failure injection is a first-class mode here, not a hack: /simulate-failure
in main.py flips `force_failure` to make this raise a realistic Razorpay-
shaped error on demand, so the "one graceful failure" requirement doesn't
depend on network flakiness during judging.
"""

import os
import time
import uuid

try:
    import razorpay
except ImportError:
    razorpay = None


class RazorpayError(Exception):
    """Normalized error shape, whether it came from the real SDK or mock mode."""
    def __init__(self, message: str, code: str = "GATEWAY_ERROR"):
        self.code = code
        super().__init__(message)


class RazorpayGateway:
    def __init__(self):
        key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        self.live = bool(key_id and key_secret and razorpay is not None)
        self._client = razorpay.Client(auth=(key_id, key_secret)) if self.live else None

    @property
    def mode(self) -> str:
        return "live-test" if self.live else "mock"

    def create_order(self, amount_paise: int, session_id: str, force_failure: bool = False) -> dict:
        if force_failure:
            raise RazorpayError(
                "Payment gateway timed out while creating the order (simulated)",
                code="GATEWAY_TIMEOUT",
            )

        receipt = f"receipt_{uuid.uuid4().hex[:10]}"

        if self.live:
            try:
                return self._client.order.create({
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {"session_id": session_id},
                })
            except Exception as e:
                raise RazorpayError(str(e), code="GATEWAY_ERROR") from e

        # Mock mode: realistic-shaped fake response
        return {
            "id": f"order_MOCK{uuid.uuid4().hex[:14]}",
            "entity": "order",
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "status": "created",
            "created_at": int(time.time()),
            "notes": {"session_id": session_id},
        }

    def create_payment_link(self, amount_paise: int, order_id: str, description: str,
                             force_failure: bool = False) -> dict:
        if force_failure:
            raise RazorpayError(
                "Payment link creation rejected: signature mismatch (simulated)",
                code="SIGNATURE_MISMATCH",
            )

        if self.live:
            try:
                return self._client.payment_link.create({
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": description,
                    "notes": {"order_id": order_id},
                })
            except Exception as e:
                raise RazorpayError(str(e), code="GATEWAY_ERROR") from e

        link_id = f"plink_MOCK{uuid.uuid4().hex[:14]}"
        return {
            "id": link_id,
            "entity": "payment_link",
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "short_url": f"/pay/{link_id}",
            "status": "created",
            "notes": {"order_id": order_id},
        }


gateway = RazorpayGateway()
