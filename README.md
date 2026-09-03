# CodGate

Magic Checkout already scores RTO. We expose the gate — named rules, frozen metrics, Payment Link when we block COD.

**Track 02 — AI Risk Manager. One loss class: COD Return-to-Origin (RTO).** CodGate is deliberately not another claim that a small public model can replace Razorpay's RTO intelligence. It is a working **RTO decision verifier + bounded policy executor**: the approved policy decides `ALLOW_COD / FORCE_PREPAID / STOP`, Counterfactual Risk Repair tests legitimate customer-correctable fixes through the same policy, and Risk Canary verifies whether a candidate RTO model/rule release has earned the right to change production checkout behaviour.

Policy v1.0 is frozen on 2026-09-02. `decide(order)` is pure: no network, no LLM, no I/O. The HTTP layer alone issues a Razorpay **test** Payment Link when test keys are present; otherwise it writes an explicit `plink_SIMULATED_...` id.

## Why this fits Track 02

The Track 02 brief asks for a working **detector, verifier or auto-responder for one class of loss**, held-out precision/recall, honest false-positive cost and defense-only behavior.

CodGate chooses **verifier + bounded enforcement** for one class only:

```text
LOSS CLASS
COD RTO
   │
   ├── approved order policy ──► ALLOW / FORCE PREPAID / STOP
   │
   ├── fixable false block? ───► Counterfactual Risk Repair
   │
   └── new model/rule release? ► RTO Risk Canary
                                  SHIP / SHADOW / BLOCK RELEASE
```

Risk Repair and Risk Canary are **not additional fraud products**. Both exist only to make the COD-RTO control safer.

## Direct Razorpay value

The order gate can be used at checkout, but the strongest Razorpay-internal component is **Risk Canary**. It does not require a new e-commerce company to install CodGate.

Before Razorpay promotes a new RTO model, threshold or ruleset, the owning risk system supplies a paired replay/shadow window containing:

- current production decision,
- candidate decision,
- observed `RTO / delivered` outcome,
- amount,
- merchant cohort.

Canary measures the consequences on the **same orders** and returns exactly one release verdict:

- `SHIP` — evidence, ₹ economics, recall, merchant slices and blast radius all clear the release gate.
- `SHADOW` — candidate may be better, but the evidence is insufficient/uncertain or the traffic change is too wide for immediate rollout.
- `BLOCK_RELEASE` — priced loss rises, recall drops materially, or an evidence-qualified merchant cohort gets a severe false-block regression.

This protects a Razorpay-owned deployment decision. The public repo does **not** claim access to Razorpay's private Magic Checkout/RTO model or replay data.

## Working prototype

| Control | What ships |
|---|---|
| Pure transaction policy | `decide(order)` is the single frozen v1.0 policy and returns `payment_link=None` |
| Frozen held-out evidence | `python -m app.score` reproduces Precision / Recall / ₹ error cost + SHA |
| Judge preflight | `python -m app.preflight` fails if canonical cases, frozen evidence, Repair or Canary claims drift |
| Shadow rollout | `X-CodGate-Mode: shadow` records the policy decision but creates no Payment Link |
| Enforcement | `X-CodGate-Mode: enforce` attaches a Razorpay test Payment Link or explicit simulation after `FORCE_PREPAID` |
| Counterfactual Risk Repair | Tests only legitimate mutable fields through the same `decide()`; it never edits historical risk to manufacture an allow |
| RTO Risk Canary | Verifies paired current/candidate RTO decisions and returns `SHIP / SHADOW / BLOCK_RELEASE` |
| Statistical evidence gate | Wilson 95% intervals for precision/recall plus paired 95% interval for ₹ cost delta |
| Sample sufficiency | A release cannot `SHIP` with fewer than 100 rows, 20 observed RTOs or 50 delivered orders |
| Slice safety | Merchant-segment false-block regressions gate release only when the slice has enough delivered evidence |
| Trivial baselines | Canary reports `always allow` and `always force prepaid` alongside current/candidate |
| Evidence identity | SHA-256 replay identity + deterministic `cgrl_...` release receipt |
| Retry safety | `Idempotency-Key` prevents duplicate decision side effects |
| Decision receipt | Deterministic `cgr_...` binds policy source + order + decision + rules |
| Repair receipt | Deterministic `cgrr_...` binds before/after repair proof |
| Audit integrity | New decision rows are hash-chained; `/audit/verify` detects tampering |
| Outcome feedback | `POST /orders/{id}/outcome` records `DELIVERED` / `RTO` in a separate chained ledger |
| Live economics | `/metrics/live` measures observed decisions/outcomes without editing the frozen benchmark |
| Policy provenance | `/policy/manifest` hashes `policy.py`, `features.py` and `pincodes.py` |
| Readiness | `/ready` checks frozen SHA plus decision/outcome chain integrity |

The desk stays intentionally small: **Gate / Policy / Metrics / Audit**. The Razorpay-internal release verifier lives under **Policy** instead of becoming another generic AI dashboard.

## Frozen Track 02 evidence

`data/heldout.csv` remains the frozen v1.0 public benchmark. Positive class is `FORCE_PREPAID`; STOP rows are skipped.

```text
CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a
```

These numbers are **prototype evidence, not a claim of production accuracy**. The file is small; the repo says so instead of inflating the claim. A real Razorpay release check should run Risk Canary on a much larger Razorpay-owned replay/shadow window.

## RTO Risk Canary v2

`POST /release/check` consumes precomputed current/candidate decisions on the same observed outcomes. It never contains the candidate model.

It reports for current, candidate and two trivial baselines:

- TP / FP / FN / TN,
- precision + Wilson 95% CI,
- recall + Wilson 95% CI,
- false-positive / false-negative rates,
- false-block ₹,
- missed-RTO ₹,
- modeled total ₹ loss,
- blocked / false-block / missed-RTO GMV.

It also computes:

- paired 95% interval for current→candidate ₹ cost delta,
- decision blast radius and flip direction,
- merchant-segment false-block deltas,
- low-sample slice warnings,
- replay SHA-256,
- `cgrl_...` release receipt bound to data + governance version + verdict.

### Release rules

`BLOCK_RELEASE` when:

- modeled ₹ loss increases,
- candidate recall drops by more than 2 percentage points, or
- an evidence-qualified merchant segment worsens false-block rate by more than 5 percentage points.

`SHADOW` when:

- replay has <100 rows, <20 RTOs or <50 delivered orders,
- candidate looks cheaper but the paired 95% cost interval still crosses zero,
- >15% of decisions change, or
- an evidence-qualified segment worsens false-block rate by >2 percentage points.

Only a candidate clearing all gates can `SHIP`.

Judge fixtures are deterministic 200-row synthetic **verifier fixtures**, not accuracy claims:

```text
GET /release/demo/good  -> SHIP
GET /release/demo/wide  -> SHADOW
GET /release/demo/bad   -> BLOCK_RELEASE
```

The important case is `wide`: the candidate is perfect on that synthetic replay but changes 20% of existing decisions, so CodGate refuses immediate production rollout.

See [`docs/RISK_CANARY.md`](docs/RISK_CANARY.md).

## Counterfactual Risk Repair

Most risk systems stop at **why was this blocked?** CodGate asks a narrower question: **is the block caused by legitimate customer-correctable data, or by structural risk?**

Risk Repair is not an override engine. It cannot reduce prior RTO count, make an account older, invent order/prepaid history, lower amount, or change the pincode risk band simply to cross the threshold.

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

The original decision is never silently mutated. A corrected order must be verified and scored again.

## Policy v1.0

STOP short-circuits. Otherwise points add, floor at 0. `FORCE_PREPAID` iff points >= 50; else `ALLOW_COD`.

| ID | Rule | Points | Human condition |
|---|---|---:|---|
| R1 | PINCODE_INVALID | STOP | Pincode is not 6 digits |
| R2 | AMOUNT_INVALID | STOP | Amount <= 0 |
| R3 | ADDRESS_EMPTY | STOP | Address is empty |
| R4 | LANDMARK_ONLY | +40 | near/opp/beside temple, mandir, mosque, masjid, church, dargah or gurudwara; no house number |
| R5 | HIGH_RTO_PIN | +25 | Frozen high RTO pin band |
| R6 | NEW_CUSTOMER | +20 | `orders_count == 0` or `account_age_days < 21` |
| R7 | HIGH_TICKET | +15 | Amount >= ₹3,000 |
| R8 | PRIOR_RTO_PHONE | +35 | `prior_rto_count >= 1` |
| R9 | NO_PREPAID_HISTORY | +10 | `prepaid_orders == 0` |
| R10 | SHORT_ADDRESS | +20 | Address < 12 chars after landmark check |
| R11 | MID_RTO_PIN | +10 | Frozen mid pin band |
| R13 | PARTIAL_ADDRESS | +8 | House or locality, not both / otherwise incomplete |
| C1 | PREPAID_VETERAN | -15 | `prepaid_orders >= 3` |
| C2 | OLD_CUSTOMER | -10 | `account_age_days >= 180` and not new |
| C3 | LOW_RTO_PIN | -10 | Frozen low pin band |
| C4 | COMPLETE_ADDRESS | -8 | House number AND street/locality |

Known high pins: `841226`, `848101`, `854301`, `846004`, `277001`, `271001`, `786001`, `795001`.

Known low pins include: `560038`, `560001`, `400001`, `400050`, `110001`, `110017`, `600001`, `500001`, `411001`, `380001`, `560034`.

Known pins resolve before the frozen prefix table; unknown prefixes fall to mid.

## What broke

- Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).
- Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).
- Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).

Risk Repair does not hide these errors. Risk Canary exists so a future candidate model/policy cannot silently ship a worse economic or cohort outcome.

## Canonical cases

**ALLOW** — `ord_blr_vet_01`, `560038`, complete Indiranagar address, ₹899, age 640, prepaid 11, prior RTO 0, orders 24 -> `ALLOW_COD`, **0 pts**, C4+C3+C2+C1.

**FORCE** — `ord_siwan_temple_01`, `841226`, `near temple`, ₹3499, age 2, prepaid 0, prior RTO 2, orders 0 -> `FORCE_PREPAID`, **145 pts**, R4+R5+R6+R7+R8+R9. `decide()` still returns `payment_link=None`; the HTTP layer attaches the Payment Link only in enforce mode.

**STOP** — `ord_bad_pin_01`, pincode `56` -> `STOP`, **R1**.

## API contracts

Checkout:

```text
POST /orders/score
X-CodGate-Mode: shadow | enforce
Idempotency-Key: <checkout retry key>
```

Risk Repair:

```text
POST /orders/repair
```

Razorpay-internal RTO release verification:

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

Observed fulfilment outcome:

```text
POST /orders/ord_123/outcome
{"outcome":"RTO","source":"courier"}
```

Operational endpoints:

`/health`, `/ready`, `/ops/status`, `/policy/manifest`, `/orders/repair`, `/release/check`, `/release/demo/{good|wide|bad}`, `/audit/verify`, `/outcomes/verify`, `/metrics`, `/metrics/live`.

## Run and verify

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pytest -q
python -m app.score
python -m app.preflight
uvicorn app.repair_app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

For a real **test-mode** Payment Link only, set `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET=...`. Live keys are ignored by design. Without valid test keys, simulation remains explicit.

## Production boundary

This is a working public prototype, not a claim that a Railway/FastAPI service should be copied unchanged into Razorpay production.

Productionisation would replace local ledgers/idempotency state with Razorpay durable infrastructure, add service auth and tenancy, bind order features to internal RTO signals, ingest trusted fulfilment events, verify real Payment Link webhooks, and put policy/model approval under Razorpay internal controls.

For Risk Canary specifically, the production integration is deliberately small:

```text
candidate RTO model/rule
        ↓
paired current + candidate shadow decisions
        ↓
observed fulfilment outcomes
        ↓
CodGate RTO verifier
        ↓
SHIP / SHADOW / BLOCK_RELEASE
        ↓
model registry / rollout record
```

The claim is not **"our 80-row policy is better than Razorpay's model."** The claim is:

> **Razorpay's RTO intelligence should not be allowed to change money-moving checkout behaviour until its errors, rupee impact, merchant slices and rollout blast radius have been verified on held-out outcomes and bound to an auditable release receipt.**

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md), [`docs/RISK_CANARY.md`](docs/RISK_CANARY.md) and [`docs/JUDGE_REDTEAM.md`](docs/JUDGE_REDTEAM.md).
