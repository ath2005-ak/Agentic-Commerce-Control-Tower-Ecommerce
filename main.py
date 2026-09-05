"""
FastAPI backend for the Agentic Commerce hackathon track.

Endpoints:
  GET  /catalog            merchant's agent-readable catalog
  GET  /cart/{session_id}  current cart contents + value
  POST /cart/add           gated add-to-cart
  POST /cart/remove        remove an item
  GET  /suggest-addons/{session_id}   ranked, explainable cross-sell suggestions
  POST /policy/check       dry-run a policy decision without acting (for the UI's bounds panel)
  POST /checkout           gated Razorpay order + payment link creation
  POST /simulate-failure/{session_id}   deliberately trips the gateway failure path
  POST /chat               conversational agent loop (propose -> confirm -> act)
  GET  /audit-logs         full audit trail (optionally filtered by session_id)
  GET  /audit-summary      counts by policy decision, for the UI header
  GET  /                   the console UI (static/index.html)

Run:
  pip install -r requirements.txt
  uvicorn main:app --reload
Runs in mock payment mode with zero setup. Set RAZORPAY_KEY_ID /
RAZORPAY_KEY_SECRET to switch to real Razorpay test-mode calls.
"""

import uuid
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

import policy
import audit
import store
import actions
import agent


class ConfirmPaymentRequest(BaseModel):
    link_id: str | None = None
    session_id: str | None = None
    force_failure: bool = False

from crosssell import _catalog, _products_by_id
from razorpay_client import gateway

app = FastAPI(title="Agentic Commerce Demo")
audit.init_db()

STATIC_DIR = Path(__file__).parent / "static"


class AddItemRequest(BaseModel):
    session_id: str
    sku_id: str
    reasoning: str = "user requested"


class RemoveItemRequest(BaseModel):
    session_id: str
    sku_id: str


class CheckoutRequest(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


class PolicyCheckRequest(BaseModel):
    action: str
    session_id: str
    sku_id: str | None = None
    discount_percent: float = 0


@app.get("/catalog")
def get_catalog():
    return _catalog


@app.get("/cart/{session_id}")
def get_cart(session_id: str):
    cart = store.get_cart(session_id)
    items = [_products_by_id[s] for s in cart]
    return {"cart": items, "cart_value_paise": store.cart_value_paise(session_id, _products_by_id)}


@app.post("/cart/add")
def add_item(req: AddItemRequest):
    if req.sku_id not in _products_by_id:
        raise HTTPException(404, "unknown sku")
    result = actions.add_item(req.session_id, req.sku_id, reasoning=req.reasoning)
    if result.status == "blocked":
        raise HTTPException(403, result.message)
    return {"status": result.status, "message": result.message, **result.data}


@app.post("/cart/remove")
def remove_item(req: RemoveItemRequest):
    result = actions.remove_item(req.session_id, req.sku_id)
    if not result.ok:
        raise HTTPException(400, result.message)
    return {"status": result.status, "message": result.message, **result.data}


@app.get("/suggest-addons/{session_id}")
def suggest_addons(session_id: str):
    return {"suggestions": actions.get_suggestions(session_id)}


@app.post("/policy/check")
def policy_check(req: PolicyCheckRequest):
    cart_value = store.cart_value_paise(req.session_id, _products_by_id)
    delta = _products_by_id[req.sku_id]["price_paise"] if req.sku_id in _products_by_id else 0
    result = policy.check_action(
        req.action, cart_value_paise=cart_value, delta_paise=delta,
        discount_percent=req.discount_percent,
    )
    return {"decision": result.decision.value, "reason": result.reason}


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    result = actions.checkout(req.session_id)
    if not result.ok:
        code = 403 if result.status == "blocked" else 502
        raise HTTPException(code, result.message)
    return {"status": result.status, "message": result.message, **result.data}


@app.post("/simulate-failure/{session_id}")
def simulate_failure(session_id: str):
    """Deliberately trips the gateway's failure path so the graceful-failure
    requirement can be demoed on demand, independent of real network conditions."""
    if not store.get_cart(session_id):
        store.get_cart(session_id).append(next(iter(_products_by_id)))
    result = actions.checkout(session_id, force_failure=True)
    return {"simulated_failure": True, "status": result.status, "message": result.message}


@app.post("/chat")
def chat(req: ChatRequest):
    return agent.handle_message(req.session_id, req.message)


@app.get("/audit-logs")
def audit_logs(session_id: str | None = None, limit: int = 200):
    return {"trail": audit.get_trail(session_id, limit=limit)}


@app.get("/audit-summary")
def audit_summary(session_id: str | None = None):
    return {"summary": audit.get_summary(session_id)}


@app.get("/gateway-mode")
def gateway_mode():
    return {"mode": gateway.mode}


@app.get("/policy/bounds")
def policy_bounds():
    return {
        "max_upsell_increase_ratio": policy.MAX_UPSELL_INCREASE_RATIO,
        "max_single_order_paise": policy.MAX_SINGLE_ORDER_PAISE,
        "max_discount_percent": policy.MAX_DISCOUNT_PERCENT,
        "allowed_actions": sorted(policy.ALLOWED_ACTIONS),
    }


@app.get("/pay/{link_id}")
def pay_page_by_id(link_id: str):
    pay_html = STATIC_DIR / "pay.html"
    if pay_html.exists():
        return FileResponse(str(pay_html))
    raise HTTPException(404, "Pay page not found")


@app.get("/pay")
def pay_page_default():
    pay_html = STATIC_DIR / "pay.html"
    if pay_html.exists():
        return FileResponse(str(pay_html))
    raise HTTPException(404, "Pay page not found")


@app.get("/api/payment/{link_id}")
def get_payment_details(link_id: str):
    if link_id == "latest":
        if store.payment_links:
            last_key = list(store.payment_links.keys())[-1]
            return store.payment_links[last_key]
        return {"error": "No active payment links"}

    link_data = store.payment_links.get(link_id)
    if not link_data:
        return {"error": "Payment link not found"}
    return link_data


@app.post("/api/pay/confirm")
def confirm_payment(req: ConfirmPaymentRequest):
    session_id = req.session_id or "session_unknown"
    link_id = req.link_id or "plink_MOCK"

    if link_id in store.payment_links:
        stored_sid = store.payment_links[link_id].get("session_id")
        if stored_sid and (session_id == "session_unknown" or not session_id):
            session_id = stored_sid

    if req.force_failure:
        audit.log_event(
            session_id=session_id, action="payment_complete", reasoning="mock checkout user failure simulation",
            policy_decision="allow", policy_reason="Payment gateway failure simulated on pay page",
            input_data={"link_id": link_id}, output_data={"status": "failed", "code": "GATEWAY_DECLINED"},
            status="failed",
        )
        return {
            "ok": False,
            "status": "failed",
            "error_code": "GATEWAY_DECLINED",
            "message": "Payment rejected by gateway (simulated bank decline)."
        }

    payment_id = f"pay_MOCK{uuid.uuid4().hex[:14]}"
    if link_id in store.payment_links:
        store.payment_links[link_id]["status"] = "paid"
        store.payment_links[link_id]["payment_id"] = payment_id
        session_id = store.payment_links[link_id].get("session_id", session_id)

    # Cart reset only on successful payment confirmation
    store.reset(session_id)

    audit.log_event(
        session_id=session_id, action="payment_complete", reasoning="user completed checkout on pay page",
        policy_decision="allow", policy_reason="Razorpay payment captured successfully",
        input_data={"link_id": link_id}, output_data={"payment_id": payment_id, "status": "captured"},
        status="success",
    )
    return {
        "ok": True,
        "status": "captured",
        "payment_id": payment_id,
        "message": "Payment captured successfully."
    }



if STATIC_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))