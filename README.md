# Agentic Commerce — Track 01

Autonomous upsell agent for a Razorpay merchant, with a machine-readable catalog,
an explainable cross-sell engine, a deterministic policy/guardrail layer, real
(or mock) Razorpay checkout, a SQLite audit trail, and a custom console UI. Every
piece below was actually run end-to-end (backend via curl, JS syntax-checked,
20 tests passing) before being written up here.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # optional — leave blank keys for zero-setup mock mode
uvicorn main:app --reload
```

Open **http://localhost:8000** — that's the console UI, served directly by FastAPI.

No Razorpay keys needed to demo. `razorpay_client.py` detects their absence and
runs in mock mode automatically — same code path, realistic fake responses.
Add real `rzp_test_...` keys to `.env` to switch to live test-mode calls with
zero code changes.

## Architecture

```
                 ┌─────────────────────┐
   chat/UI  ───▶ │  agent.py            │  propose → confirm → act
                 │  (rule-based NLU,    │  loop; the ONE place a
                 │  LLM-swappable)      │  "confirm" from the user
                 └──────────┬───────────┘  is allowed through
                            │
                            ▼
                 ┌─────────────────────┐
                 │  actions.py          │  the only path to state
                 │  (gated business     │  change or a Razorpay call
                 │  logic)              │
                 └──────┬───────┬───────┘
                        │       │
              ┌─────────▼─┐   ┌─▼──────────────┐
              │ policy.py │   │ razorpay_client │  mock ⇄ live,
              │ (deterministic│ .py (dual mode) │  failure injection
              │ gate, no LLM) └─────────────────┘
              └─────────┬─┘
                        ▼
                 ┌─────────────┐
                 │  audit.py    │  every decision, every API call
                 │  (SQLite)    │  logged with reasoning + outcome
                 └─────────────┘
```

`main.py` exposes this over REST for the UI (and for wiring an external agent
framework to, if you want). `crosssell.py` and `store.py` are the supporting
data layer — catalog scoring and in-memory session state, respectively.

**Why this shape:** the LLM (or rule-based parser, by default) never talks to
Razorpay directly. It can only request an action through `actions.py`, which
checks `policy.py` before doing anything, and logs the outcome either way. That's
the actual answer to "how is this bounded and gated" — not a claim, a code path
you can point to.

## Files

| File | What it does |
|---|---|
| `catalog.json` | 12-SKU agent-readable catalog: price, margin, stock, description, co-purchase pairs |
| `crosssell.py` | `suggest_addons(cart)` — ranks by co-occurrence + margin + price-band fit, returns a reason string per suggestion |
| `policy.py` | Deterministic gate: 20% upsell auto-approve threshold, ₹1L order ceiling, 10% max self-authorized discount, action whitelist |
| `razorpay_client.py` | Dual-mode gateway (mock / live test-mode) + on-demand failure injection |
| `audit.py` | SQLite logger (`audit_logs.db`) — every action, reasoning, policy decision, and outcome |
| `store.py` | In-memory cart + pending-confirmation state per session |
| `actions.py` | The single gated entry point for add/remove/suggest/checkout — everything else calls into this |
| `agent.py` | Conversational loop: parses chat messages into intents, handles the confirm/deny step, calls `actions.py` |
| `main.py` | FastAPI app: REST endpoints + serves the console UI |
| `static/index.html` | The console UI — custom HTML/CSS/JS, no framework, no template |
| `test_suite.py` | 20 tests: catalog, scoring, policy gating, audit log, mock gateway, failure injection, full add→checkout flow, pay page payment failure cart preservation |

## Verified demo flow

Run this yourself, or walk judges through it live — every step below was tested
against the running server, not just written:

1. **`add Aria X12`** in chat (or click the catalog chip) → item added, agent
   offers to suggest a pairing.
2. **`suggest something`** → returns the screen protector and case, each with a
   reason ("frequently bought with Aria X12; high margin (60%)"). Click a
   suggestion chip to add it directly.
3. **`add Vela Book 14 Laptop`** on top of a small cart → policy catches the
   264% jump and asks for confirmation instead of silently adding it. Say
   **`yes`** and it goes through.
4. **Checkout** → creates a mock (or real test-mode) Razorpay order + payment
   link.
5. **Simulate failure** button → trips the gateway's failure path on demand,
   shows the agent's non-retrying, clearly-worded failure message, and logs it.
6. **Control Tower** (right pane) → live-updating audit ledger, color-coded by
   decision (green = allowed, amber = needs confirmation, red = blocked/failed),
   plus the current policy bounds — tune `policy.py`'s constants and refresh to
   show judges it's a real, adjustable control, not decoration.

## Where to plug in a real LLM

`agent.py`'s `interpret()` function is the one seam to replace: it currently
does keyword matching against the catalog, returning `{"intent": ..., "sku_id": ...}`.
Swap that body for a real OpenAI/Gemini function-calling request with the same
return shape, and nothing downstream (confirmation handling, `actions.py`,
`policy.py`) needs to change — the gating layer doesn't care whether the
request came from a regex or a model.

## Running the tests

```bash
pip install pytest
pytest test_suite.py -v
```

20 passed, covering: catalog/scoring, policy gating (allow/confirm/block cases),
audit log round-trip, mock gateway shape, forced-failure error handling, cart preservation on payment failure, and two
full add→confirm→checkout integration paths.

## Next steps if you have time left

- Real embeddings over product descriptions instead of co-occurrence pairs
- A `/webhook` endpoint for Razorpay payment confirmation callbacks
- Multi-turn negotiation ("what if I skip the case and get the earbuds instead")
