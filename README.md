# CodGate

Magic Checkout already scores RTO. We expose the gate — named rules, frozen metrics, Payment Link when we block COD.

**Track 02 — AI Risk Manager.** One class: **will this COD RTO.** Policy v1.0 is frozen on 2026-09-02. `decide(order)` is pure: no network, no LLM, no I/O. The HTTP layer alone issues a Razorpay **test** Payment Link when test keys are present; otherwise it writes `plink_SIMULATED_{order_id}` and says so in the result stamp.

CodGate is not another RTO model. It is the control plane after risk intelligence: a versioned merchant action, named reasons, reproducible economics, a rollout mode and a decision receipt that can be traced to the payment intervention. The public prototype does **not** claim access to Razorpay's private Magic Checkout score feed; inside Razorpay, this pattern belongs beside that internal signal rather than competing with it.

## Working prototype

| Control | What ships |
|---|---|
| Pure policy | `decide(order)` remains the single v1.0 policy and returns no Payment Link |
| Shadow rollout | `X-CodGate-Mode: shadow` records the real decision but never issues a link |
| Enforcement | `X-CodGate-Mode: enforce` turns `FORCE_PREPAID` into a Razorpay test link or explicit simulation |
| Counterfactual Risk Repair | Proves whether a **legitimate customer correction** can move the same frozen policy below threshold; never edits history to manufacture an allow |
| Retry safety | `Idempotency-Key` replays the same receipt/link metadata and prevents duplicate decision audit rows |
| Decision receipt | Deterministic `cgr_...` receipt binds order fingerprint + policy source + decision + rules |
| Repair receipt | Deterministic `cgrr_...` receipt binds the before/after policy proof |
| Audit integrity | New `audit.jsonl` rows are SHA-256 chained; `/audit/verify` detects edits or chain breaks |
| Outcome feedback | `POST /orders/{order_id}/outcome` records `DELIVERED` or `RTO` in a separate chained ledger |
| Live economics | `/metrics/live` joins observed outcomes to real decisions without editing the frozen benchmark |
| Policy provenance | `/policy/manifest` hashes `policy.py`, `features.py` and `pincodes.py` without duplicating policy logic |
| Readiness | `/ready` fails if the held-out CSV hash, decision chain or outcome chain is broken |
| Payment trace | Razorpay test Payment Links use the CodGate receipt as `reference_id` and in notes |
| Safe simulation | Unknown/fabricated `plink_SIMULATED_*` ids cannot be marked paid |

The desk keeps the same four surfaces: **Gate / Policy / Metrics / Audit**. The Gate exposes **ENFORCE / SHADOW** without adding a second policy. The result carries an operational receipt plus a Risk Repair proof; Policy shows source provenance; Metrics keeps the frozen block and separately shows observed traffic; Audit shows chain verification.

## Counterfactual Risk Repair

Most risk systems stop at **why was this blocked?** CodGate asks a stricter operational question: **what is the smallest legitimate correction that would make COD safe under the exact same frozen policy?**

Risk Repair is deliberately **not an override engine**. It cannot decrease `prior_rto_count`, make an account older, invent prepaid history, lower the order amount, or change the pincode risk band just to cross the threshold. Today it proves one bounded repair class: **address completion**. Invalid required fields are returned as correction requirements rather than guessed values.

Example repairable order:

```text
560038 · near temple · ₹899 · new customer · no prepaid history
Policy v1.0: FORCE_PREPAID · 60 pts
Complete the real address and re-score
Same Policy v1.0: 12 pts · ALLOW_COD
```

The canonical Siwan case is intentionally different:

```text
841226 · near temple · ₹3499 · new · prior RTO ×2 · no prepaid history
Policy v1.0: FORCE_PREPAID · 145 pts
Strongest legitimate address repair: 97 pts
Result: NO SAFE REPAIR — keep prepaid
```

That distinction is the feature. A false block caused by fixable checkout data can be repaired; structural/historical risk cannot be edited away. `POST /orders/repair` exposes the same proof as an API, and every successful `/orders/score` response also includes `risk_repair`.

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

These are prototype economics on the frozen 80-row file, not a claim about Razorpay production performance. A real rollout should start in shadow on a merchant cohort and re-evaluate by merchant/value/pincode/customer tenure before enforcement.

## Observed outcomes — separate from the frozen set

A real risk control is incomplete until it learns what happened after fulfilment. CodGate therefore has a second, append-only outcome ledger:

```text
POST /orders/ord_123/outcome
{"outcome":"DELIVERED","source":"courier"}
```

or

```text
POST /orders/ord_123/outcome
{"outcome":"RTO","source":"courier"}
```

`/metrics/live` joins the latest decision per order with the observed fulfilment outcome and reports coverage, precision/recall, ₹ false-block/missed-RTO cost, shadow volume and prepaid conversion for enforced FORCE_PREPAID decisions. Outcome rows are SHA-256 chained and are **never** written into `data/heldout.csv`; the frozen benchmark remains frozen.

## What broke

- Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).
- Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).
- Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).

Risk Repair does not hide those failures. It only distinguishes the subset caused by legitimate customer-correctable input defects from failures that remain structural under v1.0.

## Canonical cases

`ALLOW_COD`: `ord_blr_vet_01`, `560038`, complete Indiranagar address, ₹899, age 640, prepaid 11, prior RTO 0, orders 24 → **0 pts**, C4+C3+C2+C1.

`FORCE_PREPAID`: `ord_siwan_temple_01`, `841226`, `near temple`, ₹3499, age 2, prepaid 0, prior RTO 2, orders 0 → **145 pts**, R4+R5+R6+R7+R8+R9. `decide()` still returns `payment_link=None`; the HTTP layer issues the link in enforce mode. Risk Repair proves that even a complete address would still score **97**, so there is no safe correction path to COD.

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

For a real **test-mode** Payment Link only, set `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET=...`. Live keys are ignored by design. In Razorpay test mode the decision receipt is sent as the Payment Link `reference_id` and notes so the payment object can be traced back to the risk receipt.

## Integration contract

Recommended checkout call:

```text
POST /orders/score
X-CodGate-Mode: shadow | enforce
Idempotency-Key: <checkout retry key>
```

- **shadow**: returns the same `ALLOW_COD / FORCE_PREPAID / STOP` policy decision, but a FORCE decision becomes `action: OBSERVE_ONLY` and no link is created.
- **enforce**: a FORCE decision issues the Payment Link at the HTTP layer.
- Every successful score response includes a read-only `risk_repair` proof. It never changes the decision in that response.
- `POST /orders/repair` can be called before enforcement to ask whether legitimate correction can restore COD.
- Same idempotency key + same request returns the original receipt/link metadata with `idempotent_replay: true` and no second decision row.
- Same idempotency key + changed request returns HTTP 409.

Operational endpoints: `/health`, `/ready`, `/ops/status`, `/policy/manifest`, `/orders/repair`, `/audit/verify`, `/outcomes/verify`, `/metrics`, `/metrics/live`.

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) for the request contract and [`docs/JUDGE_REDTEAM.md`](docs/JUDGE_REDTEAM.md) for the explicit production gaps and judge attacks.

## What would still be required inside Razorpay

This repo is a working prototype, not a claim that a public FastAPI service should be dropped into production unchanged. Productionisation would replace local JSONL/idempotency storage with Razorpay durable infrastructure, add service auth and merchant tenancy, bind the input contract to internal Magic Checkout/RTO signals, ingest real courier/fulfilment events instead of the public outcome endpoint, close real Payment Link state with signed webhooks, and put policy version approval/rollback under internal controls.

Risk Repair would also need a governed catalogue of **verified mutable fields**. A production system must prove that a corrected address/pincode came from a trusted checkout/address-validation flow rather than accept arbitrary edits from an untrusted client.

The part CodGate is asking Razorpay to value is the **governance contract**: risk intelligence should end in a named, versioned, measurable and traceable merchant action — and when customer-correctable data caused the block, the system should be able to prove the smallest safe path back to COD without weakening real risk.
