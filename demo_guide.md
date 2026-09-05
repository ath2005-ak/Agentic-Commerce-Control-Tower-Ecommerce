# Demo & Edge Case Script — Agentic Commerce (Track 01)

This script details how to demonstrate error handling, deterministic policy gating, and edge-case recovery to judges.

---

## 1. The Core Pitch (30-second hook)
> *"In agentic commerce, you can never let an LLM talk to Razorpay or modify money states directly. In our architecture, the agent can only **request** actions. Every request is intercepted by a deterministic policy layer ([policy.py](file:///c:/Users/Atharva/Downloads/razorpay_intern/policy.py)) before touching Razorpay, and every decision is logged to an immutable SQLite audit trail ([audit.py](file:///c:/Users/Atharva/Downloads/razorpay_intern/audit.py))."*

---

## 2. Step-by-Step Demo Script with Edge Cases

### Edge Case 1: Oversized Upsell Protection (`NEEDS_CONFIRMATION`)
- **What to do**:
  1. Click `+ Add Aria X12` (Earbuds, ₹2,999).
  2. Type: `add Vela Book 14` (Laptop, ₹79,990).
- **What happens**:
  - Cart increases by **2,667%**, exceeding the `MAX_UPSELL_INCREASE_RATIO` limit of 20%.
  - Instead of silently adding an expensive laptop, the policy gate intercepts it with `NEEDS_CONFIRMATION`.
  - The UI prompts: *"That's a bigger jump than my usual auto-approve limit. Add Vela Book 14 Laptop anyway?"* with `[Yes, add it]` and `[No, cancel]` buttons.
- **Judge Takeaway**: Shows that high-value upsells cannot be silently pushed onto users.

---

### Edge Case 2: Order Hard Ceiling (`BLOCK`)
- **What to do**:
  1. Try to add items until the total exceeds ₹1,00,000 (or tune `MAX_SINGLE_ORDER_PAISE` in [policy.py](file:///c:/Users/Atharva/Downloads/razorpay_intern/policy.py)).
- **What happens**:
  - The policy engine returns decision `BLOCK` with reason:
    `"Resulting order value exceeds hard ceiling ₹1,00,000.00"`.
  - The action is blocked completely without creating a Razorpay order.
- **Judge Takeaway**: Shows deterministic bounds that no LLM prompt injection or hallucination can bypass.

---

### Edge Case 3: Payment Gateway Failure Injection & Recovery (`FAILED`)
- **What to do**:
  1. Add an item (e.g. `add Aria X12`) and click **Checkout**.
  2. Click **Pay Now** to open the custom Razorpay Pay Page (`/pay/{link_id}`).
  3. Click **"Demo: Simulate Gateway Payment Failure"**.
- **What happens**:
  - The Pay Page simulates bank decline/gateway timeout.
  - A clear error message is shown (*"Payment rejected by gateway - simulated bank decline"*).
  - **Cart Preservation**: The user's cart is **NOT cleared** on failure, allowing the user to safely retry payment without losing their cart items.
  - A red `FAILED` entry is recorded in the Audit Log with exact error codes.
- **Judge Takeaway**: Demonstrates graceful failure handling, non-retrying errors, and cart state preservation.

---

### Edge Case 4: Live Control Tower & Audit Ledger (`GOVERNANCE`)
- **What to do**:
  1. Click **Audit Trail** in the top-right header of the console UI.
  2. Toggle between **Current Session** and **All System Logs**.
  3. Expand any card under **View Details & Payload**.
- **What happens**:
  - Displays live policy counters in header: `Allow: X`, `Confirm: Y`, `Block: Z`.
  - Shows precise timestamps (`18:47:25`), policy rationale, reasoning, and full JSON `input_json` / `output_json` payloads.
- **Judge Takeaway**: Gives judges 100% empirical proof of governance and auditability.

---

## 3. Pre-computed Answers for Judges' Questions

| Question | Answer |
|---|---|
| **"What if the LLM hallucinates an invalid discount or SKU?"** | *"The action parser checks against `catalog.json` and `policy.py`. Discounts over 10% or non-whitelisted SKUs are rejected with `BLOCK` before hitting any money function."* |
| **"How does this integrate with real Razorpay?"** | *"Our `razorpay_client.py` uses a dual-mode pattern. If `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are set in `.env`, it makes real Razorpay test-mode API calls. Without keys, it falls back to realistic mock mode with zero code changes."* |
| **"How are payment failures handled?"** | *"Checkout failure does not reset the cart. The error code is logged to SQLite, and the user receives a non-retrying human-readable message with their session reference ID for support."* |
