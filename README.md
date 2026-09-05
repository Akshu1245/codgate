# CodGate

**Razorpay AI Buildathon · Track 02 — AI Risk Manager · one loss class: COD Return-to-Origin (RTO).**

CodGate is a working **RTO release verifier + bounded checkout-control prototype**. It does not claim that a tiny public model is better than Razorpay's private RTO intelligence. It proves a narrower and safer idea:

> A candidate RTO model or policy should not be allowed to change money-moving checkout behaviour until its held-out evidence, paired replay impact, rollout blast radius and audit trail have been checked.

The product has six operator surfaces: **Control Room, Decision Desk, Customer Correctability, Risk Canary, Audit Terminal and Integration Simulator**.

## What is real, and what is only a regression fixture

CodGate keeps these categories deliberately separate.

| Surface | Evidence class | Purpose |
|---|---|---|
| `GET /evidence/real-rto` | Public real-data prototype evidence | Reproduced held-out RTO evaluation with source SHA and fail-closed release verdict |
| `POST /release/check` | Real when supplied observed paired outcomes | Razorpay-internal verifier for CURRENT vs CANDIDATE decisions |
| `POST /orders/{id}/outcome` + `/metrics/live` | Real when fed trusted fulfilment outcomes | Measures decisions against observed `DELIVERED / RTO` outcomes |
| `data/heldout.csv` | **Handcrafted synthetic regression fixture** | Catches deterministic policy/test drift only; not accuracy evidence |
| `/release/demo/good|wide|bad` | **Synthetic regression fixtures** | Proves `SHIP / SHADOW / BLOCK_RELEASE` code paths only |
| pincode severity bands | **Deterministic control fixture** | Preserves original policy behaviour; no empirical pincode RTO rate is claimed |
| ₹180 / ₹250 error values | **Scenario assumptions** | Makes regression economics deterministic; not measured merchant economics |

The deployed `/metrics` endpoint is **real-evidence first**. The old 80-row values are nested under `regression_fixture` and explicitly marked as synthetic/software-regression-only.

## Reproduced real RTO evidence

The evidence pipeline downloads the public Kaggle dataset `sahilr05/meesho-orders`, whose dataset page describes it as Meesho supplier order data. Raw third-party rows are **not committed or redistributed**. CI records the source archive SHA and exports only derived aggregate evidence, a derived model artifact and de-identified holdout predictions.

Source identity used by the current frozen evidence:

```text
Kaggle slug       sahilr05/meesho-orders
CSV member        meesho Orders Aug.csv
Source ZIP SHA    bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e
Source rows       208
Terminal rows     138 after deduplication/filtering
RTO               28
Delivered         110
Date range         2022-08-01 → 2022-08-31
```

### Leakage controls

- Positive label: `RTO_COMPLETE`.
- Negative label: `DELIVERED`.
- `CANCELLED`, `SHIPPED`, `RTO_INITIATED`, `RTO_LOCKED` and other intermediate/non-terminal states are excluded.
- Only order-time fields are whitelisted: order date, customer state, product, SKU, size, quantity and listed price when present.
- Settlement, delivery, final-status, return-charge and other post-outcome fields are forbidden.
- Split is chronological **60/20/20**, with no shuffle, SMOTE, synthetic expansion or duplicate augmentation.
- Threshold is selected on validation only; the final test is not used for threshold tuning.

Split:

```text
Train        82 rows · 16 RTO · Aug 01 → Aug 24
Validation   28 rows ·  4 RTO · Aug 24 → Aug 28
Test         28 rows ·  8 RTO · Aug 28 → Aug 31
```

### Untouched held-out result

The real public-data candidate is weak. CodGate does **not** hide or tune away that result.

```text
Holdout n          28
RTO positives       8
Threshold         0.35
Precision        23.08%    (TP 3 / predicted-positive 13)
Recall           37.50%    (TP 3 / actual-positive 8)
F1                0.2857
PR-AUC             0.3656
ROC-AUC            0.4313
Balanced accuracy  0.4375
TP / FP / FN / TN  3 / 10 / 5 / 10
Precision bootstrap 95% CI  0.0% → 46.15%
Recall bootstrap 95% CI     0.0% → 75.07%
```

Holdout prevalence is `8/28 = 28.57%`. The candidate's flagged-order precision is only `23.08%`, and its ROC-AUC is below the `0.5` random-ranking baseline. Therefore:

```text
REAL EVIDENCE VERDICT = BLOCK_RELEASE
```

That is the intended working behaviour. Automatically forcing prepaid from this model would be unsafe.

Standalone evidence can **never** return `SHIP`. Even a strong standalone holdout result is capped at `SHADOW` until the paired CURRENT-vs-CANDIDATE release verifier also clears its safety gates.

## Why this fits Track 02

CodGate focuses on one loss class, **COD RTO**, and implements a verifier/control layer rather than adding unrelated fraud products.

```text
candidate RTO model/rules
        │
        ├── public/merchant held-out evidence
        │        ↓
        │   Evidence Gate
        │   BLOCK_RELEASE / SHADOW
        │
        └── paired CURRENT + CANDIDATE decisions
                 + observed fulfilment outcomes
                         ↓
                    Risk Canary
              SHIP / SHADOW / BLOCK_RELEASE
                         ↓
                  checkout rollout record
```

For the public candidate currently bundled as aggregate evidence, the first gate stops the release.

## Runtime checkout control

The deterministic `decide(order)` path is pure: no network, DB, LLM or payment creation. It returns one of:

- `ALLOW_COD`
- `FORCE_PREPAID`
- `STOP`

The HTTP layer handles action execution after the decision.

- `X-CodGate-Mode: shadow` records the decision and **never** creates a Payment Link.
- `X-CodGate-Mode: enforce` may create a Razorpay **test-mode** Payment Link after `FORCE_PREPAID` when valid `rzp_test_...` credentials are present.
- Without usable test credentials, the link is visibly `plink_SIMULATED_...`; CI never pretends it moved real money.
- Live Razorpay keys are not accepted by this public prototype.
- `Idempotency-Key` prevents duplicate checkout side effects.

The deterministic rule policy is a **control/regression policy**, not the claimed real predictive model. Its location severity bands are legacy test controls and contain no empirical RTO rates.

## Customer Correctability

Counterfactual Risk Repair asks whether a block is caused by legitimate customer-correctable information or by structural/history signals.

It can test corrections such as a complete address through the same pure policy. It cannot reduce prior RTO count, age an account, invent prepaid history, lower order amount or change the pincode merely to manufacture an allow.

Outputs include:

- `ALREADY_SAFE`
- `NEEDS_CORRECTION`
- `REPAIRABLE`
- `STRUCTURAL_RISK`

Every repair proof receives a deterministic `cgrr_...` receipt.

## Paired Risk Canary

`POST /release/check` receives precomputed CURRENT and CANDIDATE decisions for the same orders plus observed outcomes. It does not contain or call the candidate model.

It checks:

- TP / FP / FN / TN,
- precision/recall and Wilson intervals,
- false-positive / false-negative rates,
- decision blast radius,
- merchant-segment false-block deltas,
- sample sufficiency,
- paired scenario-cost delta,
- replay SHA-256 and deterministic `cgrl_...` release receipt.

The rupee constants in the public regression setup are explicitly **assumptions**. A real merchant/Razorpay integration must configure measured economics before treating rupee fields as business impact.

The three demo endpoints are deterministic verifier regression tests, not model evidence:

```text
GET /release/demo/good  -> SHIP
GET /release/demo/wide  -> SHADOW
GET /release/demo/bad   -> BLOCK_RELEASE
```

## Audit and operational safety

- Decision rows are append-only SHA-256 chained JSONL records.
- Fulfilment outcomes are stored in a separate SHA-256 chained ledger.
- `/audit/verify` and `/outcomes/verify` detect tampering.
- Decision receipts `cgr_...` bind policy source, order, decision and fired rules.
- Release/evidence receipts bind evidence identity and verdict.
- `/ready` reports operational health separately from `release_authorized`.
- The current public candidate is operationally visible but **not release-authorized**.

## Main API

```text
GET  /health
GET  /ready
GET  /ops/status
GET  /policy/manifest
GET  /evidence/real-rto
GET  /metrics
GET  /metrics/live

POST /orders/score
POST /orders/repair
POST /orders/{order_id}/outcome

POST /release/check
GET  /release/demo/{good|wide|bad}

GET  /audit/verify
GET  /outcomes/verify
```

Checkout request:

```text
POST /orders/score
X-CodGate-Mode: shadow | enforce
Idempotency-Key: <retry key>
```

Paired release request:

```json
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

## Run the working prototype

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pytest -q
python -m app.preflight
uvicorn app.repair_app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

To independently rebuild the real public-data evidence:

```bash
pip install -r requirements-evidence.txt
python -m evidence.build_real_rto
```

The evidence build downloads the source at run time and writes derived files under `artifacts/real-rto-evidence/`. GitHub Actions regenerates the report and checks it against `data/real_rto_evidence.json`; if the source SHA, counts, confusion matrix, metrics or confidence intervals drift, the submission package is not built.

For a real Razorpay **test** Payment Link, set:

```text
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
```

Without test credentials, simulation remains explicit.

## CI gates before packaging

The final ZIP is created only after all required jobs pass:

1. Python 3.12 full tests.
2. Python 3.13 full tests.
3. policy/preflight/regression checks.
4. JavaScript syntax checks.
5. external Meesho dataset download + evidence reproduction.
6. frozen-vs-regenerated evidence equality check.
7. fail-closed `BLOCK_RELEASE` assertion for the weak real candidate.
8. Chromium desktop/mobile judge journey.
9. ZIP integrity test.

## Scope and limitations

- The public exact-RTO sample is small: 138 terminal orders and only 28 RTOs.
- The final holdout has only 28 orders and 8 RTO positives; confidence intervals are wide.
- The source is supplier-specific public data and cannot establish Razorpay-wide production accuracy.
- The current real-data candidate performs poorly and is intentionally blocked.
- The deterministic checkout policy is not offered as a learned production RTO detector.
- Pincode severity bands and public rupee costs are test controls/assumptions, not measured real-world values.
- Production use would require Razorpay-owned features, trusted fulfilment outcomes, durable storage/idempotency, service auth/tenancy, verified Payment Link webhooks and internal rollout controls.

The strongest claim CodGate makes is therefore verifiable and modest:

> **When real held-out evidence is weak, CodGate refuses to let the candidate change checkout behaviour, records why, and keeps the decision/release trail auditable.**

See `docs/INTEGRATION.md`, `docs/RISK_CANARY.md`, `docs/JUDGE_REDTEAM.md` and the generated `artifacts/real-rto-evidence/report.md` after running the evidence builder.
