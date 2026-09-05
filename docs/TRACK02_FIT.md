# Track 02 fit — qualification audit

## One qualifying loss class

CodGate submits **RETURN_TO_SELLER (returns)** as its single measured loss class. That exactly fits the Track 02 option to stop merchant losses from returns.

The large public dataset does not expose payment mode, so CodGate does **not** mislabel its 28,417-order benchmark as COD-only. COD/RTO remains the bounded checkout use case and is independently audited on smaller exact-domain datasets.

## Qualification evidence

| Track 02 bar | CodGate |
|---|---|
| Working detector/verifier/auto-responder | Real detector + release verifier + bounded response |
| Real heldout | 5,726 sealed unique orders / 362 returns |
| Precision | **11.21%** (95% CI 9.00–13.44%) |
| Recall | **23.20%** (95% CI 18.79–27.61%) |
| Base rate | 6.32% |
| Precision lift | **1.77×** |
| TP/FP/FN/TN | 84 / 665 / 278 / 4,699 |
| FP exposure | ₹443,627 order GMV subjected to intervention |
| FP cost | `665 × merchant-approved unit cost`; no fabricated default merchant loss |
| Defense-only | advisory model; no offensive/bypass capability; model never creates payments |

## Evidence hierarchy

**Primary detector evidence**
- Amazon India public sales data.
- 28,417 terminal unique orders.
- stable hash-sealed 20% final holdout.
- final n=5,726; precision 11.21%; recall 23.20%; ROC-AUC 0.5944.
- runtime uses the exact frozen model and verifies its SHA.

**External exact-RTO validation**
- Meesho: 138 terminal orders.
- final n=28; precision 23.08%; recall 37.50%; ROC-AUC 0.4313.
- verdict `BLOCK_RELEASE`; weak evidence is not hidden.

**Exact-COD audit**
- 47 terminal COD orders: 42 delivered / 5 returned.
- explicitly not benchmark-eligible.

**Regression fixtures only**
- handcrafted `data/heldout.csv` n=80,
- canonical ALLOW/FORCE/STOP cases,
- `/release/demo/good|wide|bad`,
- pincode bands and ₹180/₹250 scenario assumptions.

## Why a judge should care

Most risk demos stop at a score. CodGate proves the harder payments-platform behavior:

1. the detector has reproducible held-out evidence,
2. uncertainty and base-rate lift are visible,
3. false positives have explicit economic exposure/cost semantics,
4. weak external RTO evidence fails closed,
5. the learned model itself is advisory only,
6. rollout can stay in SHADOW,
7. checkout actions are idempotent/auditable,
8. release decisions receive reproducible receipts.

## Reviewer path

1. Control Room — see the 28,417-order primary evidence card.
2. Score a live order — see `risk_score`, threshold and advisory decision.
3. Risk Canary — inspect the weak Meesho exact-RTO external validation and its `BLOCK_RELEASE` verdict.
4. Decision Desk — run COD control in SHADOW; verify no Payment Link.
5. Customer Correctability — verify legitimate repair versus structural risk.
6. Audit Terminal — verify hash chains.
7. Integration Simulator — verify complete shadow checkout path.

The qualification claim is **not** “11% is amazing.” It is: the detector shows measurable real-data lift, the evidence is sealed and reproducible, the false-positive economics are honest, and the product refuses to turn weak/unverified risk evidence into unsafe payment behavior.
