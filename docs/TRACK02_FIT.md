# Track 02 fit — qualification audit

CodGate submits one loss class only: **COD Return-to-Origin (RTO)**.

The primary product claim is a **working RTO verifier/control layer**, not that a small public classifier should replace Razorpay's private risk intelligence.

| Track 02 requirement | CodGate evidence |
|---|---|
| One class of merchant loss | COD RTO only |
| Working detector / verifier / auto-responder | Working RTO evidence gate + paired Risk Canary + bounded checkout executor |
| Held-out test set | Public Meesho supplier-order dataset described by its Kaggle page as real; chronological final holdout n=28 |
| Measured precision | **23.08%** on untouched real-data holdout (TP 3, FP 10) |
| Measured recall | **37.50%** on untouched real-data holdout (TP 3, FN 5) |
| Additional held-out quality | ROC-AUC **0.4313**, PR-AUC **0.3656**, balanced accuracy **0.4375** |
| Reproducible provenance | Kaggle slug `sahilr05/meesho-orders`; source ZIP SHA `bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e` |
| Leakage control | Terminal `RTO_COMPLETE` vs `DELIVERED` only; post-outcome fields forbidden; chronological 60/20/20; threshold chosen on validation only |
| Correct response to weak model | Evidence gate returns **`BLOCK_RELEASE`** because ROC-AUC <0.5 and precision < holdout prevalence |
| Working intervention | Shadow mode never creates a Payment Link; enforce mode can use Razorpay test Payment Links only after a policy decision |
| Defense-only | No offensive tooling; controls only restrict/verify COD risk |
| Auditability | Deterministic decision/repair/evidence/release receipts + chained decision/outcome ledgers |
| Bounded uncertainty | Bootstrap CI, sample-size gate, paired CI, slice and blast-radius gates |
| Real-world learning loop | Observed `DELIVERED / RTO` endpoint and `/metrics/live` |

## Evidence classes are explicit

**Primary public evidence**
- 138 terminal real-data outcomes after filtering/deduplication,
- 28 RTO / 110 delivered,
- chronological train/validation/test = 82 / 28 / 28,
- final holdout = 28 rows / 8 RTO,
- precision 23.08%, recall 37.50%, ROC-AUC 0.4313,
- verdict `BLOCK_RELEASE`.

**Regression fixtures only**
- `data/heldout.csv` n=80 handcrafted synthetic policy fixture,
- canonical ALLOW/FORCE/STOP orders,
- `/release/demo/good|wide|bad` synthetic paired-canary fixtures,
- pincode severity bands,
- ₹180/₹250 scenario error-cost constants.

The n=80 fixture's 74.2% / 60.5% values are retained only to detect software/policy drift and are **not** submitted as real-world accuracy.

## Why the verifier is the strongest claim

The public real-data candidate performs poorly. CodGate does not tune against the observed final test until the number looks good. Instead it demonstrates the control that matters: a weak candidate is measured, its failure is visible, and it is prevented from reaching checkout enforcement.

Standalone held-out evidence can never produce `SHIP`; even a strong standalone result is capped at `SHADOW` until a paired CURRENT-vs-CANDIDATE replay with observed outcomes also clears Risk Canary.

## Reviewer path

```bash
pytest -q
python -m app.preflight
pip install -r requirements-evidence.txt
python -m evidence.build_real_rto
```

Then open the six-surface console:

1. **Control Room** — operational health and evidence status.
2. **Decision Desk** — deterministic checkout/action wiring; use shadow mode for no side effects.
3. **Customer Correctability** — verify legitimate correction vs structural risk.
4. **Risk Canary** — inspect the real public-data `BLOCK_RELEASE` card first; demo buttons are labeled regression fixtures.
5. **Audit Terminal** — verify hash chains.
6. **Integration Simulator** — verify the end-to-end shadow flow and that no Payment Link is created.

The important proof is not a flattering score. It is that the system reproduces the real result and **fails closed when the result is not good enough**.
