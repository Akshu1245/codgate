# CodGate — judge red-team

This is the attack list we would use if we were reviewing CodGate for Razorpay. It is intentionally harsher than the README.

## 1. “Where is the AI?”

**Attack:** Policy v1.0 is deterministic. It is not a learned model.

**Answer:** Correct. CodGate does not claim that the policy is an ML model. Magic Checkout already has RTO intelligence. CodGate is the **verifier/control plane after risk intelligence**: versioned policy, held-out economics, bounded intervention, repair proof and release governance. A money-moving rule should remain inspectable even when the upstream model changes.

**Remaining production work:** bind the order contract and Risk Canary replay contract to Razorpay’s private RTO signals/model registry. The public repo cannot claim access to those interfaces.

## 2. “Doesn’t this benefit Razorpay only if another e-commerce company integrates CodGate?”

**Attack:** A merchant-side COD gate creates indirect value. If merchants do not integrate it, Razorpay gets no new benefit.

**Mitigation built:** **Risk Canary is Razorpay-internal.** It sits in the RTO model/policy release path. Razorpay supplies paired current-vs-candidate decisions and observed fulfilment outcomes. Canary returns `SHIP / SHADOW / BLOCK_RELEASE`, ₹ error cost, 95% uncertainty, decision blast radius, merchant-slice regressions and a release receipt. No new e-commerce integration is required.

**Direct company value:** a bad RTO release can false-block legitimate checkout traffic or miss RTO across merchants already on Razorpay. Canary can stop or shadow that release before rollout.

## 3. “Why not just look at precision/recall and ship?”

**Attack:** The ML team already has model metrics. A release verifier is redundant.

**Answer:** Aggregate precision/recall do not answer whether the candidate changes 30% of production decisions, whether one merchant cohort absorbs the false blocks, whether the ₹ error cost is actually lower, or whether the apparent improvement is supported by enough outcomes.

**Built in v2:**
- candidate/current TP/FP/FN/TN,
- precision and Wilson 95% CI,
- recall and Wilson 95% CI,
- false-positive/false-negative rates,
- false-block ₹ and missed-RTO ₹,
- paired 95% interval for current→candidate cost delta,
- `always allow` / `always force prepaid` trivial baselines,
- merchant-segment false-block deltas,
- low-sample slice warnings,
- decision blast radius,
- sealed replay SHA + release receipt.

A candidate can look better and still be forced to `SHADOW`.

## 4. “Can a tiny lucky sample authorize a global rollout?”

**Attack:** A verifier is dangerous if five lucky orders can produce `SHIP`.

**Mitigation built:** fail-closed evidence gates. `SHIP` requires at least **100 rows, 20 observed RTOs and 50 delivered orders**. If the candidate looks cheaper but the paired 95% cost interval crosses zero, it remains `SHADOW`.

Merchant-segment false-block guardrails require at least **20 delivered examples in that segment**. Smaller cohorts are printed as **LOW N** instead of converting noise into a confident percentage.

## 5. “Is Canary secretly another RTO model?”

**Attack:** You claim one loss class, but perhaps Canary contains a second classifier.

**Answer:** No. Canary accepts only **precomputed** `current_decision` and `candidate_decision` plus observed outcomes. It never computes either risk decision. The candidate can be a model, threshold or ruleset owned by Razorpay. Canary verifies the release consequence.

There is still one public transaction policy: `app/policy.py` v1.0.

## 6. “Hard-coded rules will drift.”

**Attack:** Pincode/address rules become stale.

**Answer:** v1.0 is deliberately frozen because the public held-out evidence must be reproducible. A future policy/model should be a new version, first measured in shadow/replay and then passed through Risk Canary. v1.0 is not silently mutated to make metrics look better.

**Remaining production work:** internal policy/model registry, owner, approval, expiry/review date and rollback controls.

## 7. “Your Track 02 held-out set is only 80 rows.”

**Attack:** 80 rows are too small for a production accuracy claim.

**Answer:** Agree. The 80-row file is public prototype evidence for reproducibility and honest false-positive/false-negative economics. It is **not** presented as Razorpay production accuracy. Its labels are frozen and identified by SHA-256.

**Production distinction:** Risk Canary should run on a much larger Razorpay-owned paired replay/shadow window. It refuses small windows from authorizing `SHIP`.

## 8. “Why don’t you train a model and beat the public RTO repos?”

**Attack:** Public competitors report stronger headline precision/recall from much larger synthetic/Kaggle-derived datasets.

**Answer:** Inventing a larger synthetic dataset to claim a higher score would not make this a stronger Razorpay submission. Razorpay already has RTO intelligence and private data that a public team cannot reproduce. CodGate competes on **verification and trustworthy execution**, not on pretending its 80-row rule policy is a superior production classifier.

The public benchmark is intentionally modest and auditable; the production integration is designed to verify Razorpay’s stronger upstream candidate rather than replace it.

## 9. “FORCE_PREPAID can destroy conversion.”

**Attack:** A false block creates customer friction and lost margin.

**Answer:** The held-out report explicitly prices false blocks. `shadow` mode can measure the counterfactual without issuing a link. `enforce` is separate from the pure decision.

**Additional mitigation:** Counterfactual Risk Repair distinguishes blocks caused by customer-correctable input quality from structural/historical risk.

## 10. “Is Risk Repair just gaming the score?”

**Attack:** A repair engine could edit history until the order passes.

**Mitigation built:** Risk Repair never changes `prior_rto_count`, account age, order count, prepaid history, amount or pincode risk band to cross the threshold. Its only scored repair class is **address completion**, passed back through the same `decide()`.

The canonical Siwan order is the proof: **145 → 97**, still `FORCE_PREPAID`, status `STRUCTURAL_RISK`.

## 11. “What stops a customer lying about a corrected address?”

**Answer:** Nothing in this public prototype proves physical address truth, so Risk Repair **does not automatically flip the original decision**. It returns the criterion and requires a re-score after correction.

**Remaining production work:** accept repaired fields only from trusted checkout/address-validation sources and bind provenance to the receipt.

## 12. “Retries can issue two Payment Links.”

**Mitigation built:** `Idempotency-Key`. Same key + same request replays original receipt/link metadata with no duplicate decision row. Same key + changed request returns HTTP 409.

## 13. “Your JSONL audit can be edited.”

**Mitigation built:** new decision/outcome rows are SHA-256 chained. `/audit/verify` and `/outcomes/verify` recompute the chains. Existing pre-chain demo rows remain honestly marked as legacy.

**Remaining production work:** use Razorpay’s durable internal event/audit infrastructure. Local JSONL is an MVP sink.

## 14. “Can anyone forge a simulated paid link?”

**Mitigation built:** only simulated IDs actually issued in CodGate’s audit can be marked paid. Unknown simulated IDs return 404; non-simulated IDs cannot be manually marked paid.

## 15. “Why Payment Links?”

**Answer:** It is the bounded intervention after a COD policy block: collect before shipping rather than cancel the customer. In real Razorpay test mode, the decision receipt is placed in Payment Link reference/notes for traceability.

## 16. “Why not put this inside Magic Checkout?”

**Answer:** That may be the correct production destination. CodGate is not asking Razorpay to operate another customer-facing dashboard forever. It demonstrates a control-plane pattern: versioned action policy, held-out economics, bounded repair, release verification and evidence receipts. If accepted, the natural destination is Magic Checkout/RTO operations and the internal model-release path.

## 17. “Can you reproduce all of your claims without the UI?”

**Mitigation built:**

```bash
pytest -q
python -m app.score
python -m app.preflight
```

`app.preflight` fails if the canonical ALLOW/FORCE/STOP cases, exact frozen metric block/SHA, canonical 145→97 structural repair, or `SHIP / SHADOW / BLOCK_RELEASE` verifier cases drift.

## 18. “What would stop us shipping this tomorrow?”

- No private Magic Checkout/RTO feature or model-registry integration in the public repo.
- n=80 transaction evaluation is prototype evidence, not production performance.
- Canary’s 200-row judge fixtures are synthetic behavior tests, not production accuracy evidence.
- Real Canary rollout needs a large, correctly joined Razorpay-owned replay/shadow window.
- No Razorpay service-to-service auth or tenancy.
- Local JSONL/idempotency stores should become durable infrastructure.
- Policy/model ownership, approvals and rollback should use internal controls.
- Real Payment Link webhooks should close payment state.
- Risk Repair needs trusted corrected-field provenance.

Those limitations are disclosed because Track 02 asks for **honest** risk evidence, not a production claim manufactured from demo data.
