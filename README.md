# CodGate

Magic Checkout already scores RTO. We expose the gate — named rules, frozen metrics, Payment Link when we block COD.

**Track 02 — AI Risk Manager.** One class: **will this COD RTO.** Policy v1.0 is frozen on 2026-09-02. `decide(order)` is pure: no network, no LLM, no I/O. The HTTP layer alone issues a Razorpay **test** Payment Link when test keys are present; otherwise it writes `plink_SIMULATED_{order_id}` and says so in the result stamp.

CodGate is not another RTO model. It is the control plane after risk intelligence: a versioned action, named reasons, reproducible economics, rollout controls and receipts. The public prototype does **not** claim access to Razorpay's private Magic Checkout score feed.

## Direct Razorpay value — Risk Canary

The original COD gate helps merchants when their checkout routes an order through CodGate. **Risk Canary is different: Razorpay can use it internally without waiting for any new e-commerce integration.**

Before Razorpay ships a new Magic Checkout RTO model, rule set or threshold release, Canary accepts a replay window containing:

- the **current** production decision,
- the **candidate** release decision,
- the observed `DELIVERED / RTO` outcome,
- amount and merchant segment.

Canary does **not** contain a second RTO model. Razorpay's existing risk system produces both decisions; Canary verifies the release and returns exactly one governance verdict:

- `SHIP` — modeled ₹ loss does not increase, decision blast radius is within 15%, and no merchant segment's false-block rate worsens by more than 2 percentage points.
- `SHADOW` — candidate may be better, but it changes too much traffic or has a segment-level regression that needs live observation first.
- `BLOCK_RELEASE` — modeled ₹ loss increases or a merchant segment's false-block rate worsens by more than 5 percentage points.

Every check produces a deterministic `cgrl_...` **release receipt**, current/candidate confusion costs, blocked GMV deltas, merchant-segment false-block deltas and changed-order examples.

This directly protects Razorpay from a bad risk release affecting merchants already on Razorpay. It is an internal release verifier for Razorpay's own risk stack, not a new merchant product.

API:

```text
POST /release/check
GET  /release/demo/good   -> SHIP
GET  /release/demo/wide   -> SHADOW
GET  /release/demo/bad    -> BLOCK_RELEASE
```

The three demo windows are synthetic and are labelled as such. In production the rows would come from Razorpay-owned shadow/replay decisions and observed fulfilment outcomes.

## Working prototype

| Control | What ships |
|---|---|
| Pure transaction policy | `decide(order)` remains the single v1.0 COD policy and returns no Payment Link |
| Shadow rollout | `X-CodGate-Mode: shadow` records the real decision but never issues a link |
| Enforcement | `X-CodGate-Mode: enforce` turns `FORCE_PREPAID` into a Razorpay test link or explicit simulation |
| Counterfactual Risk Repair | Proves whether a legitimate customer correction can move the same frozen policy below threshold; never edits history to manufacture an allow |
| **Razorpay Risk Canary** | Verifies precomputed current-vs-candidate release outputs and returns `SHIP / SHADOW / BLOCK_RELEASE` |
| Retry safety | `Idempotency-Key` replays the same receipt/link metadata and prevents duplicate decision audit rows |
| Decision receipt | Deterministic `cgr_...` receipt binds order fingerprint + policy source + decision + rules |
| Repair receipt | Deterministic `cgrr_...` receipt binds the before/after policy proof |
| Release receipt | Deterministic `cgrl_...` receipt binds the candidate-release verification window and verdict |
| Audit integrity | New `audit.jsonl` rows are SHA-256 chained; `/audit/verify` detects edits or chain breaks |
| Outcome feedback | `POST /orders/{order_id}/outcome` records `DELIVERED` or `RTO` in a separate chained ledger |
| Live economics | `/metrics/live` joins observed outcomes to real decisions without editing the frozen benchmark |
| Policy provenance | `/policy/manifest` hashes `policy.py`, `features.py` and `pincodes.py` without duplicating policy logic |
| Readiness | `/ready` fails if the held-out CSV hash, decision chain or outcome chain is broken |
| Payment trace | Razorpay test Payment Links use the CodGate receipt as `reference_id` and in notes |
| Safe simulation | Unknown/fabricated `plink_SIMULATED_*` ids cannot be marked paid |

The desk keeps the same four surfaces: **Gate / Policy / Metrics / Audit**. Risk Canary lives inside **Policy** as an internal release-control section rather than becoming another dashboard.

## Counterfactual Risk Repair

Most risk systems stop at **why was this blocked?** CodGate asks: **what is the smallest legitimate correction that would make COD safe under the exact same frozen policy?**

Risk Repair is deliberately **not an override engine**. It cannot decrease `prior_rto_count`, make an account older, invent prepaid history, lower the order amount, or change the pincode risk band just to cross the threshold. Today it proves one bounded repair class: **address completion**. Invalid required fields are returned as correction requirements rather than guessed values.

Repairable example:

```text
560038 · near temple · ₹899 · new customer · no prepaid history
Policy v1.0: FORCE_PREPAID · 60 pts
Complete the real address and re-score
Same Policy v1.0: 12 pts · ALLOW_COD
```

Canonical Siwan case:

```text
841226 · near temple · ₹3499 · new · prior RTO ×2 · no prepaid history
Policy v1.0: FORCE_PREPAID · 145 pts
Strongest legitimate address repair: 97 pts
Result: NO SAFE REPAIR — keep prepaid
```

`POST /orders/repair` exposes the proof, and every successful `/orders/score` response also includes `risk_repair`.

## Policy v1.0

STOP short-circuits. Else points are additive, floored at 0. `FORCE_PREPAID` iff points ≥ 50; otherwise `ALLOW_COD`.

| ID | Rule | Points | When |
|---|---|---:|---|
| R1 | PINCODE_INVALID | STOP | Pincode is not 6 digits |
| R2 | AMOUNT_INVALID | STOP | Amount ≤ 0 |
| R3 | ADDRESS_EMPTY | STOP | Address is empty |
| R4 | LANDMARK_ONLY | +40 | near/opp/beside temple, mandir, mosque, masjid, church, dargah or gurudwara; no house number |
| R5 | HIGH_RTO_PIN | +25 | Pin RTO ≥ 28%; frozen Bihar / east-UP / NE belt and known high pins |
| R6 | NEW_CUSTOMER | +20 | `orders_count == 0` or `account_age_days < 21` |
| R7 | HIGH_TICKET | +15 | Amount ≥ ₹3,000 |
| R8 | PRIOR_RTO_PHONE | +35 | `prior_rto_count ≥ 1` |
| R9 | NO_PREPAID_HISTORY | +10 | `prepaid_orders == 0` |
| R10 | SHORT_ADDRESS | +20 | Address < 12 chars, after landmark check |
| R11 | MID_RTO_PIN | +10 | Frozen mid pin band |
| R13 | PARTIAL_ADDRESS | +8 | House or locality, not both / otherwise incomplete |
| C1 | PREPAID_VETERAN | −15 | `prepaid_orders ≥ 3` |
| C2 | OLD_CUSTOMER | −10 | `account_age_days ≥ 180` and customer is not new |
| C3 | LOW_RTO_PIN | −10 | Pin RTO ≤ 8% |
| C4 | COMPLETE_ADDRESS | −8 | House number and street/locality |

Known high pins: `841226`, `848101`, `854301`, `846004`, `277001`, `271001`, `786001`, `795001`. Known low pins include `560038`, `560001`, `400001`, `400050`, `110001`, `110017`, `600001`, `500001`, `411001`, `380001`, `560034`. Known pins are resolved before the frozen prefix table; unknown prefixes fall to mid.

## Frozen metrics

```text
CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a
```

These are prototype economics on the frozen 80-row file, not a production-accuracy claim. Canary is separate: its production input would be a larger Razorpay-owned replay/shadow window.

## Observed outcomes

Live rollout evidence is kept separately from the frozen test set:

```text
POST /orders/ord_123/outcome
{"outcome":"DELIVERED","source":"courier"}
```

or:

```text
POST /orders/ord_123/outcome
{"outcome":"RTO","source":"courier"}
```

`/metrics/live` joins latest decisions to outcomes and reports coverage, precision/recall, ₹ false-block/missed-RTO cost, shadow volume and prepaid conversion. Outcome rows are SHA-256 chained and are **never** written into `data/heldout.csv`.

## What broke

- Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).
- Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).
- Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).

Risk Repair does not hide these failures. Risk Canary exists so a future candidate policy/model cannot silently ship a worse economic or segment outcome.

## Canonical cases

`ALLOW_COD`: `ord_blr_vet_01`, `560038`, complete Indiranagar address, ₹899, age 640, prepaid 11, prior RTO 0, orders 24 → **0 pts**, C4+C3+C2+C1.

`FORCE_PREPAID`: `ord_siwan_temple_01`, `841226`, `near temple`, ₹3499, age 2, prepaid 0, prior RTO 2, orders 0 → **145 pts**, R4+R5+R6+R7+R8+R9. `decide()` still returns `payment_link=None`; the HTTP layer issues the link in enforce mode. Risk Repair proves that even a complete address would still score **97**.

`STOP`: `ord_bad_pin_01`, pincode `56` → **R1**. Risk Repair asks for the real six-digit pincode and refuses to invent one.

## Run

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pytest -q
python -m app.score
uvicorn app.repair_app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

For a real **test-mode** Payment Link only, set `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET=...`. Live keys are ignored by design.

## Integration contracts

Checkout:

```text
POST /orders/score
X-CodGate-Mode: shadow | enforce
Idempotency-Key: <checkout retry key>
```

Internal Razorpay release verification:

```text
POST /release/check
{
  "release_id": "magic-rto-v42",
  "rows": [
    {
      "order_id": "ord_123",
      "merchant_segment": "enterprise",
      "amount": 3499,
      "actual_rto": true,
      "current_decision": "ALLOW_COD",
      "candidate_decision": "FORCE_PREPAID"
    }
  ]
}
```

The release payload contains **precomputed** current/candidate outputs. Canary does not call or embed either model.

Operational endpoints: `/health`, `/ready`, `/ops/status`, `/policy/manifest`, `/orders/repair`, `/release/check`, `/release/demo/{good|wide|bad}`, `/audit/verify`, `/outcomes/verify`, `/metrics`, `/metrics/live`.

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) and [`docs/JUDGE_REDTEAM.md`](docs/JUDGE_REDTEAM.md).

## What would still be required inside Razorpay

This repo is a working prototype, not a claim that a public FastAPI service should be dropped into production unchanged. Productionisation would replace local JSONL/idempotency storage with Razorpay durable infrastructure, add service auth and tenancy, bind order features to internal Magic Checkout/RTO signals, ingest real fulfilment events, close Payment Link state through signed webhooks, and put policy versions under internal approval/rollback controls.

For **Risk Canary**, the critical production integration is simpler: connect it to Razorpay's internal risk-model registry/release pipeline and feed it current-vs-candidate shadow/replay decisions plus observed outcomes. It can then become a required CI/release check before an RTO policy/model reaches production.

The part CodGate asks Razorpay to value is the governance contract: **risk intelligence should not only make a decision; Razorpay should be able to prove the action, repair legitimate false blocks, and stop a bad risk release before it reaches every merchant.**
