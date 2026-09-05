# CodGate — judge red-team

This is the review we would apply before asking a Razorpay judge to trust the submission.

## 1. “What is the single qualifying loss class?”

**Answer:** `RETURN_TO_SELLER` / returns. That is the label supported by the large public dataset. We do not call the 28,417-order benchmark COD-only because payment mode is absent. COD/RTO is a bounded checkout response/domain subset, supported separately by exact-COD and exact-RTO audit data.

## 2. “Where is the AI/ML?”

**Answer:** `/return-risk/score` runs the frozen logistic model selected on development/validation data. The exact learned encoding/scaler/coefficients are stored as hash-verified runtime chunks. The deterministic COD policy is a response/control layer, not the claimed model.

## 3. “Did you tune on the final test until the number looked good?”

**Answer:** No. The first chronological probe exposed a bad selection method and was rejected. v2 created a new final population using stable SHA-256 order buckets before model selection. Model family, hyperparameters and threshold were selected from train/validation; the v2 final labels were not used for selection.

## 4. “Are the metrics flattering?”

**Answer:** No. Held-out precision is 11.21% and recall 23.20%. The relevant context is the 6.32% base return rate, giving 1.77× precision lift. We report confidence intervals and do not call this production accuracy.

## 5. “How big is the final evidence?”

**Answer:** 28,417 terminal unique orders total; sealed final holdout n=5,726 with 362 returns. Confusion matrix: TP 84, FP 665, FN 278, TN 4,699.

## 6. “Your model score says 0.0088. Is that a 0.88% probability?”

**Answer:** No. The selected logistic model uses balanced class weights, so the runtime calls the output `risk_score` and explicitly returns `score_is_calibrated_probability=false`. The threshold is a frozen decision score, not a customer-facing probability claim.

## 7. “Where is the false-positive cost?”

**Answer:** The source directly supports **₹443,627 of false-positive order GMV exposed to intervention** on the holdout. That is not the same as lost profit. For actual cost, the endpoint accepts a merchant-approved `false_positive_cost_per_order_inr`; modeled held-out cost is `665 × unit cost`. With no merchant cost parameter, we refuse to invent a ₹ loss.

## 8. “Are you secretly using post-outcome leakage?”

**Answer:** No. Order ID, final Status and courier outcome are forbidden features. Item rows are aggregated before splitting. Target encodings come from train only; training rows use leave-one-out encoding. There is no SMOTE or synthetic training expansion.

## 9. “Why is there still COD/RTO everywhere?”

**Answer:** It is the payment/control use case. A risky return can be routed to review or a governed COD policy; Risk Canary verifies candidate COD decision releases. But the qualifying detector's accuracy claim remains RETURN_TO_SELLER because that is what the primary dataset actually labels.

## 10. “Do you have any exact-COD data?”

**Answer:** Yes, but only 47 terminal COD seller orders (42 delivered, 5 returned). We explicitly prevent that small slice from becoming the accuracy benchmark.

## 11. “Do you have any exact-RTO external validation?”

**Answer:** Yes. The Meesho source yields 138 terminal rows. Its held-out ROC-AUC is 0.4313 and precision is below its holdout prevalence, so the evidence gate returns `BLOCK_RELEASE`. Weak evidence is visible rather than massaged.

## 12. “Can the ML model move money?”

**Answer:** No. It is `advisory_only`. It returns `FLAG_RETURN_RISK` or `STANDARD_FLOW`. Any COD checkout action is a separate deterministic path. SHADOW never creates a Payment Link; enforce mode is limited to Razorpay test keys/simulation in the public repo.

## 13. “Can retries create duplicate side effects?”

**Answer:** `Idempotency-Key` replays the original result for the same request and rejects a changed request with the same key.

## 14. “Can a tiny replay authorize SHIP?”

**Answer:** Risk Canary has minimum sample gates and fails closed to SHADOW. Slice guardrails also require minimum delivered examples before a slice can govern release.

## 15. “Why not just ship based on precision/recall?”

**Answer:** A model can improve aggregate metrics while changing too much checkout traffic or harming a merchant cohort. Canary measures paired current/candidate effects, uncertainty, blast radius, slice regressions and replay identity before returning SHIP/SHADOW/BLOCK_RELEASE.

## 16. “Is the audit real?”

**Answer:** Decision/outcome JSONL rows are SHA-256 chained and independently reverified. Local JSONL is an MVP sink; production should use Razorpay durable internal event/audit infrastructure.

## 17. “What is synthetic?”

- `data/heldout.csv` n=80: deterministic software regression fixture.
- canonical ALLOW/FORCE/STOP orders: behavior fixtures.
- Canary good/wide/bad: release-path fixtures.
- pincode severity bands: deterministic control fixture, no claimed empirical rate.
- ₹180/₹250: scenario constants retained for regression paths, not merchant economics.

None of those is the primary Track 02 metric claim.

## 18. “What would prevent production deployment tomorrow?”

- Public data is not Razorpay private traffic.
- Detector lift is useful for a prototype, not enough to claim production readiness.
- No private Magic Checkout/RTO model-registry integration.
- Merchant-specific margin/conversion economics are not available publicly.
- Local audit/idempotency stores need durable infrastructure.
- Service auth, tenancy, signed webhooks, model ownership/approvals and rollback controls remain production work.

## 19. “Why should this qualify instead of a model with a prettier headline?”

Because Track 02 explicitly asks for **honest** metrics and false-positive cost. CodGate provides a real detector with a sealed 5,726-order holdout, base-rate lift, confidence intervals, source/model hashes and reproducible CI; it then demonstrates the hard payments behavior—weak external evidence is blocked and no unverified model is allowed to move money.

## 20. Reproduce it

```bash
pytest -q
python -m app.preflight
pip install -r requirements-evidence.txt
python -m evidence.amazon_return_risk_v2
python -m evidence.build_real_rto
python -m evidence.amazon_cod_audit
```

The submission package is built only after backend, evidence, browser and ZIP-integrity gates pass.
