# Track 02 — AI Risk Manager: problem-statement alignment

This checklist uses the supplied Track 02 wording as the acceptance bar and separates real evidence from deterministic regression fixtures.

## Supplied problem statement

> **AI Risk Manager**
>
> **Stop the merchant losing money to fraud, returns and chargebacks**
>
> Build a working detector, verifier or auto-responder for one class of loss, with measured precision and recall on a held-out test set.
>
> **the bar**
>
> Honest metrics including false-positive cost. Strictly defense-only: anything offense-capable is disqualifying.

## CodGate claim

**CodGate is a defense-only COD Return-to-Origin (RTO) evidence/release verifier with bounded checkout action. It reproduces held-out evidence, blocks weak candidates, verifies paired current-vs-candidate releases, and keeps the action trail auditable.**

## Requirement-by-requirement audit

| Requirement | CodGate evidence | Status |
|---|---|---|
| One class of loss | COD Return-to-Origin (RTO) only | MATCH |
| Working detector / verifier / auto-responder | Primary claim is **verifier + bounded executor**; `/evidence/real-rto` and `/release/check` gate releases | MATCH |
| Held-out test set | Public Meesho supplier-order data; chronological 60/20/20; untouched final test n=28, RTO=8 | MATCH, SMALL PUBLIC SAMPLE |
| Measured precision | **23.08%**, TP 3 / FP 10 on the untouched real-data holdout | MATCH |
| Measured recall | **37.50%**, TP 3 / FN 5 on the untouched real-data holdout | MATCH |
| Honest weak-result handling | ROC-AUC 0.4313; precision below 28.57% holdout prevalence; verdict **`BLOCK_RELEASE`** | MATCH |
| False-positive cost awareness | Paired Canary prices false blocks when an approved cost model is supplied; public ₹180/₹250 values are labeled scenario assumptions | MATCH WITH DISCLOSED ASSUMPTION |
| Defense-only | No offensive/bypass capability; outputs only restrict/verify COD risk | MATCH |
| Working intervention | Shadow mode cannot create Payment Links; enforce mode supports Razorpay test links after a governed `FORCE_PREPAID` decision | MATCH |
| Auditability | Decision/repair/evidence/release receipts plus chained decision/outcome ledgers | MATCH |

## Real held-out evidence

```text
Source             sahilr05/meesho-orders
Source ZIP SHA     bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e
Terminal outcomes  138
RTO / Delivered    28 / 110
Train / Val / Test 82 / 28 / 28
Test positives     8
Precision          23.08%
Recall             37.50%
F1                 0.2857
PR-AUC              0.3656
ROC-AUC             0.4313
TP / FP / FN / TN  3 / 10 / 5 / 10
Verdict             BLOCK_RELEASE
```

The pipeline uses terminal `RTO_COMPLETE` vs `DELIVERED`, excludes intermediate states and post-outcome fields, uses chronological splitting with no shuffle/SMOTE/synthetic expansion, and chooses the threshold on validation only.

The final test has already been observed. It is **not** repeatedly tuned against to manufacture a higher score.

## What is not submitted as real evidence

### Handcrafted n=80 policy fixture

`data/heldout.csv` is a handcrafted synthetic regression set. Its historical 74.2% precision / 60.5% recall remains in CI only so deterministic policy changes are detected. It is not the Track 02 real-world accuracy claim.

### Pincode severity bands

The frozen location bands are deterministic control fixtures. The runtime now exposes `pin_rto_rate = null`; no public empirical pincode RTO rate is claimed.

### Public ₹ error constants

₹180 false-block and ₹250 missed-RTO are deterministic scenario assumptions, not measured merchant economics. `/metrics/live` labels them as such. Production use requires an approved merchant/Razorpay cost model.

### Canary good/wide/bad cases

`/release/demo/good`, `/wide` and `/bad` are synthetic code-path regression fixtures. They prove that the verifier can return all three verdicts; they do not prove model accuracy.

## Why a weak model still demonstrates a working solution

The Track accepts a **verifier**. The public candidate is objectively weak, and CodGate's working behavior is to stop it:

1. reproduce the source and held-out metrics,
2. verify confusion-matrix consistency and provenance SHA,
3. compare ROC-AUC against the 0.5 ranking baseline,
4. compare flagged precision against holdout prevalence,
5. return `BLOCK_RELEASE`,
6. surface the reasons in the Risk Canary UI,
7. prevent standalone evidence from ever authorizing `SHIP`.

A strong future candidate would still be capped at `SHADOW` until a paired CURRENT-vs-CANDIDATE replay with observed fulfilment outcomes clears Risk Canary.

## One problem, six surfaces

- **Control Room** — operational state and release/evidence state.
- **Decision Desk** — bounded COD action path.
- **Customer Correctability** — legitimate correction vs structural risk.
- **Risk Canary** — real evidence gate + paired release verifier.
- **Audit Terminal** — decision/outcome integrity.
- **Integration Simulator** — end-to-end shadow path.

All six surfaces serve the same COD-RTO control; none is a separate fraud product.

## Judge-verifiable CI path

```bash
pytest -q
python -m app.preflight
pip install -r requirements-evidence.txt
python -m evidence.build_real_rto
```

GitHub Actions additionally:

1. tests Python 3.12 and 3.13,
2. checks JavaScript syntax,
3. downloads the external dataset and regenerates evidence,
4. compares regenerated counts/metrics/CIs/source SHA with the frozen runtime aggregate,
5. asserts the weak public candidate is `BLOCK_RELEASE`,
6. runs a real Chromium desktop/mobile judge journey,
7. builds the submission ZIP only after all gates pass.

## Final positioning

Do not pitch CodGate as “our public model predicts RTO accurately.” The measured evidence does not support that.

Pitch the implemented claim:

> **CodGate is the governance gate between RTO intelligence and checkout action. When real held-out evidence is weak, it blocks the release; when evidence improves, it still requires paired replay, rollout guardrails and an auditable receipt before checkout behavior can change.**
