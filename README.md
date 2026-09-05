# CodGate

**Razorpay AI Buildathon · Track 02 — AI Risk Manager · one merchant-loss class: RETURN_TO_SELLER (returns).**

CodGate is a working **real-data return-risk detector + defense-only release/checkout governance prototype**. The qualifying public accuracy claim is deliberately narrow: predict whether an order ends as `RETURN_TO_SELLER` versus `DELIVERED_TO_BUYER`, using only order-time fields, and measure that detector on a sealed held-out set.

> Detect risky returns, quantify false positives honestly, and never let weak or unverified risk evidence silently become a money-moving checkout action.

The product has six operator surfaces: **Control Room, Decision Desk, Customer Correctability, Risk Canary, Audit Terminal and Integration Simulator**.

## The Track 02 claim in one screen

| Requirement | CodGate evidence |
|---|---|
| One loss class | **Return-to-seller / returns** |
| Working detector | `POST /return-risk/score` uses the exact frozen model that produced the accepted held-out result |
| Held-out set | **5,726** sealed unique orders, assigned by stable SHA-256 order bucket before model selection |
| Precision | **11.21%** (95% bootstrap CI **9.00–13.44%**) |
| Recall | **23.20%** (95% bootstrap CI **18.79–27.61%**) |
| Base return rate | **6.32%** |
| Precision lift | **1.77×** over the holdout base rate |
| Confusion matrix | **TP 84 · FP 665 · FN 278 · TN 4,699** |
| False-positive exposure | **₹443,627 order GMV** was flagged among false positives; this is exposure, **not claimed merchant profit loss** |
| False-positive cost | Merchant can supply an approved `false_positive_cost_per_order_inr`; modeled held-out FP cost is **665 × merchant unit cost** |
| Defense-only | Detector is `advisory_only`; no offensive/bypass capability and no payment action from the model |

The model's weighted-logistic output is exposed as an **uncalibrated `risk_score`**, not as a return probability.

## Primary real-data detector

The primary evidence pipeline downloads the public Amazon India sales dataset `thedevastator/unlock-profits-with-e-commerce-sales-data` and uses `Amazon Sale Report.csv`. Raw third-party rows are not committed to CodGate; CI records source identity and regenerates derived evidence.

```text
Source ZIP SHA      2d174af66d3390f6bdd157fec4e29e076e3454ed6935f124510ccc66f85c459a
Raw member rows     128,975
Terminal orders      28,417
Returned to seller    1,851
Delivered to buyer   26,566
Sealed final test      5,726
Returns in test          362
```

### Leakage and test controls

- Item rows are aggregated to unique orders **before** splitting.
- Stable SHA-256 order buckets assign 20% to the final test before model selection.
- The remaining development population is split into train/validation.
- `Order ID`, final `Status`, courier outcome and other post-outcome fields are forbidden as features.
- Target encodings are learned from train only; training rows use leave-one-out target encoding.
- No SMOTE, synthetic rows or duplicate expansion.
- Model family, hyperparameters and threshold are selected on validation only.
- The final-test labels are not used for model/threshold selection.

### Untouched held-out result

```text
Holdout n               5,726
Returns                    362
Base return rate          6.32%
Precision                11.21%
Recall                   23.20%
Precision lift            1.77x
F1                        0.1512
PR-AUC                    0.0912
ROC-AUC                   0.5944
Balanced accuracy         0.5540
TP / FP / FN / TN         84 / 665 / 278 / 4,699
FP order-GMV exposure     ₹443,627
Missed-return order GMV   ₹187,138
```

This is not advertised as high production accuracy. It is **real, reproducible prototype evidence** with non-zero lift on a substantially larger sealed holdout.

## Exact COD/RTO evidence is separate

CodGate does **not** pretend the large Amazon dataset is COD-only; that source does not expose payment mode. COD/RTO is therefore treated as a domain/operational subset rather than the primary benchmark.

Two independent checks remain visible:

1. **Exact-COD audit** — a Boss Leathers Amazon seller source contains 47 terminal COD orders: 42 delivered and 5 returned-to-seller. It is explicitly too small to be the accuracy benchmark.
2. **Exact-RTO external validation** — public Meesho supplier data yields 138 terminal orders (28 RTO, 110 delivered). Its untouched 28-order test scores 23.08% precision / 37.50% recall / ROC-AUC 0.4313, so CodGate returns **`BLOCK_RELEASE`** instead of hiding the weak result.

This hierarchy is intentional:

```text
PRIMARY qualifying evidence       large RETURN_TO_SELLER detector
EXTERNAL exact-RTO validation     Meesho · weak · BLOCK_RELEASE
EXACT-COD domain audit            47 terminal COD rows · audit only
SOFTWARE regression fixture       handcrafted n=80 · never an accuracy claim
```

## Working runtime detector

`GET /return-risk/status` verifies model/evidence identity.

`POST /return-risk/score` runs the exact frozen logistic model locally from hash-checked model chunks. It returns:

- `FLAG_RETURN_RISK` → `RISK_REVIEW`, or
- `STANDARD_FLOW` → `NO_RISK_INTERVENTION`.

The detector is deliberately `advisory_only`. It never creates a Payment Link and never moves money.

Merchant economics are opt-in and transparent. If a merchant supplies `false_positive_cost_per_order_inr = C`, CodGate reports the held-out modeled FP cost as `665 × C`. With no approved merchant cost, CodGate reports no fabricated realized-loss number.

## Bounded COD checkout control

The deterministic `decide(order)` path is a separate, inspectable **response/control policy** around COD checkout. It is not presented as the qualifying learned detector. It returns:

- `ALLOW_COD`
- `FORCE_PREPAID`
- `STOP`

The HTTP layer performs any action only after that decision:

- `X-CodGate-Mode: shadow` records the decision and **never** creates a Payment Link.
- `X-CodGate-Mode: enforce` may use a Razorpay **test-mode** Payment Link after `FORCE_PREPAID` when valid `rzp_test_...` credentials exist.
- Otherwise the link is visibly simulated.
- Live Razorpay keys are not accepted in the public prototype.
- `Idempotency-Key` prevents duplicate side effects.

## Customer Correctability

Counterfactual Risk Repair distinguishes customer-correctable input quality from structural/history risk. It cannot rewrite prior RTO count, account age, order history, amount or pincode just to manufacture an allow. Every proof receives a deterministic receipt.

## Risk Canary

`POST /release/check` is the safety layer for paired CURRENT-vs-CANDIDATE COD decisions with observed fulfilment outcomes. It checks confusion matrices, uncertainty, blast radius, merchant-slice regressions, replay identity and cost deltas, then returns:

- `SHIP`
- `SHADOW`
- `BLOCK_RELEASE`

The `/release/demo/good|wide|bad` cases are **synthetic verifier regression fixtures only**, never accuracy evidence.

## Evidence and audit integrity

- Decision/outcome logs are append-only SHA-256 chained records.
- `/audit/verify` and `/outcomes/verify` detect chain tampering.
- Evidence source and frozen runtime-model SHA values are verified during preflight/CI.
- `/ready` fails closed if the frozen real detector cannot be loaded and verified.
- Raw downloaded marketplace data is not redistributed in the submission package.

## Main API

```text
GET  /health
GET  /ready
GET  /ops/status
GET  /return-risk/status
POST /return-risk/score

GET  /evidence/real-rto        # external Meesho validation
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

## Run it

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
python -m app.preflight
uvicorn app.repair_app:app --host 0.0.0.0 --port 8000
```

Rebuild all public evidence independently:

```bash
pip install -r requirements-evidence.txt
python -m evidence.amazon_return_risk_v2
python -m evidence.build_real_rto
python -m evidence.amazon_cod_audit
```

## CI qualification gates

The submission ZIP is produced only after:

1. Python 3.12 and 3.13 unit/integration tests pass.
2. Preflight verifies the real detector hierarchy and runtime model SHA.
3. Amazon India detector evidence is re-downloaded/rebuilt and compared with the accepted frozen result.
4. Meesho exact-RTO external validation is reproduced and remains fail-closed.
5. Exact-COD audit is reproduced and cannot be promoted to the benchmark.
6. JavaScript syntax checks pass.
7. Real Chromium desktop/mobile judge journey passes.
8. SHADOW flow proves no Payment Link is issued.
9. ZIP integrity succeeds.

## What we do not claim

- Not Razorpay production accuracy.
- Not COD-only accuracy for the 28,417-order primary dataset.
- Not a calibrated probability model.
- Not that ₹443,627 is realized merchant loss; it is false-positive order-GMV exposure.
- Not that the 47 exact-COD rows or 138 exact-RTO rows are large enough for a production benchmark.
- Not that synthetic fixture metrics prove real-world performance.

The submission claim is deliberately judge-verifiable:

> **CodGate detects one class of merchant loss—return-to-seller risk—on a real sealed held-out dataset, exposes honest precision/recall and false-positive economics, and wraps that signal in fail-closed Razorpay-style checkout/release controls instead of allowing unverified risk scores to move money.**
