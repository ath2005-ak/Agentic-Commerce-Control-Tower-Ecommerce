"""
Test suite.

Run: pytest test_suite.py -v
"""

import os
import sqlite3
import pytest

import policy
import crosssell
import audit
import store
import actions
from razorpay_client import RazorpayGateway, RazorpayError


# ---------- catalog / cross-sell ----------

def test_catalog_has_products():
    assert len(crosssell._catalog["products"]) >= 10


def test_suggest_addons_returns_ranked_reasons():
    suggestions = crosssell.suggest_addons(["sku_phone_01"])
    assert len(suggestions) > 0
    assert all("reason" in s and s["reason"] for s in suggestions)
    # results should be sorted by score, descending
    scores = [s["score"] for s in suggestions]
    assert scores == sorted(scores, reverse=True)


def test_suggest_addons_excludes_items_already_in_cart():
    suggestions = crosssell.suggest_addons(["sku_phone_01", "sku_case_01"])
    ids = {s["sku_id"] for s in suggestions}
    assert "sku_case_01" not in ids


def test_high_margin_items_ranked_favorably():
    suggestions = crosssell.suggest_addons(["sku_phone_01"], max_suggestions=4)
    # screen protector (60% margin) and case (55% margin) should outrank
    # lower-margin same-affinity items given equal co-occurrence count
    assert any(s["margin_percent"] >= 55 for s in suggestions)


# ---------- policy gating ----------

def test_small_upsell_is_allowed():
    result = policy.check_action("add_item", cart_value_paise=200000, delta_paise=10000)
    assert result.decision == policy.Decision.ALLOW


def test_large_upsell_needs_confirmation():
    result = policy.check_action("add_item", cart_value_paise=200000, delta_paise=90000)
    assert result.decision == policy.Decision.NEEDS_CONFIRMATION


def test_order_ceiling_blocks_outright():
    result = policy.check_action("create_order", cart_value_paise=0, delta_paise=policy.MAX_SINGLE_ORDER_PAISE + 1)
    assert result.decision == policy.Decision.BLOCK


def test_discount_over_cap_is_blocked():
    result = policy.check_action("apply_discount", cart_value_paise=100000, discount_percent=15)
    assert result.decision == policy.Decision.BLOCK


def test_discount_within_cap_is_allowed():
    result = policy.check_action("apply_discount", cart_value_paise=100000, discount_percent=5)
    assert result.decision == policy.Decision.ALLOW


def test_non_whitelisted_action_is_blocked():
    result = policy.check_action("delete_merchant_account", cart_value_paise=0)
    assert result.decision == policy.Decision.BLOCK


# ---------- audit log ----------

@pytest.fixture
def temp_audit_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_audit.db"
    monkeypatch.setattr(audit, "DB_PATH", db_path)
    audit.init_db()
    return db_path


def test_audit_log_round_trip(temp_audit_db):
    audit.log_event(
        session_id="s1", action="add_item", reasoning="test", policy_decision="allow",
        policy_reason="ok", input_data={"sku": "x"}, output_data={}, status="allow",
    )
    trail = audit.get_trail("s1")
    assert len(trail) == 1
    assert trail[0]["action"] == "add_item"
    assert trail[0]["session_id"] == "s1"


def test_audit_summary_counts_by_decision(temp_audit_db):
    audit.log_event("s1", "add_item", "t", "allow", "r", {}, {}, "allow")
    audit.log_event("s1", "add_item", "t", "block", "r", {}, {}, "block")
    audit.log_event("s1", "add_item", "t", "allow", "r", {}, {}, "allow")
    summary = audit.get_summary("s1")
    assert summary["allow"] == 2
    assert summary["block"] == 1


# ---------- gateway: mock mode + failure injection ----------

def test_gateway_defaults_to_mock_without_keys(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    gw = RazorpayGateway()
    assert gw.mode == "mock"


def test_mock_order_creation_shape(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    gw = RazorpayGateway()
    order = gw.create_order(amount_paise=50000, session_id="s1")
    assert order["amount"] == 50000
    assert order["status"] == "created"
    assert order["id"].startswith("order_MOCK")


def test_forced_failure_raises_razorpay_error(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    gw = RazorpayGateway()
    with pytest.raises(RazorpayError):
        gw.create_order(amount_paise=50000, session_id="s1", force_failure=True)


# ---------- end-to-end actions (uses real store/policy/audit modules) ----------

@pytest.fixture
def clean_session(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "DB_PATH", tmp_path / "e2e_audit.db")
    audit.init_db()
    sid = "e2e_test_session"
    store.reset(sid)
    yield sid
    store.reset(sid)


def test_add_item_then_checkout_end_to_end(clean_session):
    sid = clean_session
    result = actions.add_item(sid, "sku_case_01")
    assert result.ok and result.status == "added"

    checkout_result = actions.checkout(sid)
    assert checkout_result.ok
    assert checkout_result.status == "checked_out"
    assert "order_id" in checkout_result.data
    # Cart remains before payment confirmation
    assert store.get_cart(sid) == ["sku_case_01"]

    # Confirm payment
    from main import confirm_payment, ConfirmPaymentRequest
    link_id = checkout_result.data["payment_link_id"]
    confirm_res = confirm_payment(ConfirmPaymentRequest(link_id=link_id, session_id=sid))
    assert confirm_res["ok"]
    assert confirm_res["status"] == "captured"
    # Cart should be cleared after successful payment confirmation
    assert store.get_cart(sid) == []


def test_oversized_upsell_requires_confirmation_before_adding(clean_session):
    sid = clean_session
    actions.add_item(sid, "sku_case_01")  # small base item
    result = actions.add_item(sid, "sku_laptop_01")  # huge relative jump
    assert result.status == "needs_confirmation"
    # item should NOT have been added yet
    assert "sku_laptop_01" not in store.get_cart(sid)


def test_checkout_with_forced_failure_does_not_clear_cart(clean_session):
    sid = clean_session
    actions.add_item(sid, "sku_case_01")
    result = actions.checkout(sid, force_failure=True)
    assert not result.ok
    assert result.status == "failed"
    # cart should survive a failed checkout so the user can retry
    assert store.get_cart(sid) == ["sku_case_01"]


def test_pay_page_link_storage_and_confirmation(clean_session):
    sid = clean_session
    actions.add_item(sid, "sku_case_01")
    result = actions.checkout(sid)
    assert result.ok
    link_id = result.data.get("payment_link_id")
    assert link_id in store.payment_links
    assert store.payment_links[link_id]["amount_paise"] == 79900
    assert result.data["payment_link"].startswith("/pay/")


def test_pay_page_simulated_failure_does_not_clear_cart(clean_session):
    sid = clean_session
    actions.add_item(sid, "sku_case_01")
    checkout_result = actions.checkout(sid)
    assert checkout_result.ok
    link_id = checkout_result.data["payment_link_id"]

    from main import confirm_payment, ConfirmPaymentRequest
    confirm_res = confirm_payment(ConfirmPaymentRequest(link_id=link_id, session_id=sid, force_failure=True))
    assert not confirm_res["ok"]
    assert confirm_res["status"] == "failed"
    # Cart should survive pay page simulated failure
    assert store.get_cart(sid) == ["sku_case_01"]

