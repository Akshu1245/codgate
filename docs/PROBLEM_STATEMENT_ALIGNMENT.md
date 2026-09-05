# Track 02 — AI Risk Manager: problem-statement alignment

## Supplied acceptance bar

> Stop the merchant losing money to fraud, returns and chargebacks. Build a working detector, verifier or auto-responder for one class of loss, with measured precision and recall on a held-out test set. Honest metrics including false-positive cost. Strictly defense-only.

## CodGate qualifying claim

**One loss class: `RETURN_TO_SELLER` (returns).** CodGate ships a real-data detector plus defense-only release/checkout governance. COD/RTO is a validated operational subset; it is not falsely used as the large-dataset accuracy label because the primary dataset does not expose payment mode.

| Requirement | Evidence | Verdict |
|---|---|---|
| One class of loss | Return-to-seller / returns | MATCH |
| Working detector | `/return-risk/score`, exact frozen hash-verified model | MATCH |
| Held-out test | Stable SHA-256 order-level sealed holdout, n=5,726 | MATCH |
| Precision | **11.21%**, 95% CI 9.00–13.44% | MATCH |
| Recall | **23.20%**, 95% CI 18.79–27.61% | MATCH |
| Honest baseline | 6.32% return prevalence; precision lift **1.77×** | MATCH |
| Confusion matrix | TP 84 / FP 665 / FN 278 / TN 4,699 | MATCH |
| False-positive cost | Real FP order-GMV exposure ₹443,627; modeled cost = 665 × merchant-approved unit cost | MATCH, NO FABRICATED MARGIN LOSS |
| Defense-only | Advisory detector; no bypass/offensive function; no model-triggered payment action | MATCH |
| Working response | Bounded COD review/control path, SHADOW no Payment Link, test-mode only enforcement | MATCH |
| Reproducibility | Source SHA, model SHA, evidence CI rebuild, preflight, Chromium journey | MATCH |

## Primary evidence

```text
Dataset slug         thedevastator/unlock-profits-with-e-commerce-sales-data
Member               Amazon Sale Report.csv
Source ZIP SHA        2d174af66d3390f6bdd157fec4e29e076e3454ed6935f124510ccc66f85c459a
Terminal orders       28,417
Returned to seller     1,851
Delivered             26,566
Final heldout           5,726
Returns in heldout        362
Precision              11.21%
Recall                 23.20%
Precision lift          1.77x
ROC-AUC                 0.5944
PR-AUC                  0.0912
TP / FP / FN / TN       84 / 665 / 278 / 4,699
FP order-GMV exposure   ₹443,627
```

The final set is assigned before model selection. Order ID, final status and courier outcome are excluded from features. Target encodings are learned from train only; training rows use leave-one-out encoding. There is no SMOTE or synthetic row expansion. Model and threshold selection use validation only.

## COD/RTO domain evidence

- **Exact COD audit:** 47 terminal COD seller orders (42 delivered, 5 returned). Domain check only; not benchmark eligible.
- **Exact RTO external validation:** Meesho 138 terminal orders. Its 28-order test gives 23.08% precision, 37.50% recall, ROC-AUC 0.4313 and is correctly `BLOCK_RELEASE`.
- **Handcrafted n=80 file:** synthetic software regression fixture only.

This separation prevents a judge from interpreting return-to-seller accuracy as COD-only accuracy.

## False-positive economics

The source lets us measure the amount of legitimate delivered orders that would have been flagged: **₹443,627 in the held-out set**. That is exposure, not realized merchant profit loss. The runtime optionally accepts the merchant's approved false-positive unit cost; with unit cost `C`, held-out modeled false-positive cost is **₹(665 × C)**. Without that merchant parameter, CodGate does not invent a ₹ loss.

## Defense-only boundary

CodGate provides risk detection, evidence verification, safer rollout, customer-correctability analysis, audit receipts and bounded checkout response. It does not contain payment-fraud instructions, bypass tooling, stolen credential handling, attack automation, abuse-ring creation/evasion or adversarial instructions for defeating a risk system.

## Judge-verifiable path

```bash
pytest -q
python -m app.preflight
pip install -r requirements-evidence.txt
python -m evidence.amazon_return_risk_v2
python -m evidence.build_real_rto
python -m evidence.amazon_cod_audit
uvicorn app.repair_app:app --host 0.0.0.0 --port 8000
```

CI additionally runs Chromium desktop/mobile and only builds the ZIP when every evidence/test/browser gate succeeds.

## Pitch

> **CodGate is a defense-only return-risk detector and governance gate. On a sealed 5,726-order real-data holdout it achieves 11.21% precision and 23.20% recall against a 6.32% base return rate (1.77× precision lift), reports false-positive exposure honestly, and prevents unverified risk signals from silently becoming checkout actions.**
