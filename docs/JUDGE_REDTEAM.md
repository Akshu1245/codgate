# CodGate — judge red-team

This is the attack list we would use if we were reviewing CodGate for Razorpay. It is intentionally harsher than the README.

## 1. “Where is the AI?”

**Attack:** Policy v1.0 is deterministic. It is not a learned model.

**Answer:** Correct. CodGate does not claim that the policy is an ML model. Magic Checkout already has RTO intelligence. CodGate is the control plane after risk intelligence: versioned policy, reasons, economic evaluation, intervention and audit. A money-moving rule should be inspectable even when an upstream model changes.

**Remaining production work:** Bind the order contract to Razorpay’s internal RTO signal/features. This public prototype cannot claim access to that private interface.

## 2. “Hard-coded rules will drift.”

**Attack:** Pincode/address rules become stale.

**Answer:** v1.0 is deliberately frozen. CodGate supports `shadow` execution so a new policy can be measured before enforcement. A rule change should produce a new policy version and a new held-out evaluation; v1.0 is never silently mutated.

**Remaining production work:** policy registry, approval workflow, owner, expiry/review date and controlled v1.1 rollout.

## 3. “Your held-out set is only 80 rows.”

**Attack:** The metrics are too small to support a production claim.

**Answer:** Agree. The 80-row file is evidence that the evaluation is reproducible and honest, not evidence of production accuracy. The repo exposes its SHA-256 and named false positives/false negatives instead of claiming production lift.

**Remaining production work:** shadow-run on a merchant cohort, then evaluate by merchant, value band, pincode band and customer tenure before enforcement.

## 4. “FORCE_PREPAID can destroy conversion.”

**Attack:** A false block creates customer friction and lost margin.

**Answer:** The held-out report prices false blocks explicitly. `shadow` mode allows Razorpay to measure the counterfactual before issuing a link. `enforce` is a separate execution mode; the policy decision itself is unchanged.

**Additional mitigation built:** Counterfactual Risk Repair distinguishes blocks caused by customer-correctable input quality from structural/historical risk. A legitimate address completion can be proven against the same frozen policy before asking the customer to prepay.

**Remaining production work:** merchant-specific economics and holdout experimentation. Do not tune against the final test set.

## 5. “Is Risk Repair just gaming the score until COD passes?”

**Attack:** A counterfactual engine could manufacture an allow by editing customer history, amount, pincode or other risk fields.

**Mitigation built:** Risk Repair has an explicit mutable-field boundary. It never changes `prior_rto_count`, account age, order count, prepaid history, amount or the pincode risk band to cross the threshold. Its only scored repair class in this prototype is **address completion**. It runs the candidate back through the same `decide()` function; there is no second policy and no override path.

The canonical Siwan order proves the guardrail: 145 pts before address completion, 97 pts after the strongest legitimate address repair, still `FORCE_PREPAID`. CodGate says **NO SAFE REPAIR** instead of weakening history.

## 6. “What stops a customer lying about the corrected address?”

**Attack:** A client could submit a nicer-looking address solely to regain COD.

**Answer:** Nothing in this public prototype proves physical address truth, so Risk Repair does **not** automatically flip the original decision. It returns the criterion and asks the order to be re-scored after correction. The current endpoint is a decision-support proof, not a trusted-data oracle.

**Remaining production work:** only accept repair fields from trusted checkout/address-validation sources, bind evidence to the receipt, and log who/what verified the correction.

## 7. “Retries can issue two Payment Links.”

**Attack:** Checkout/network retries are normal. A non-idempotent risk service can duplicate side effects.

**Mitigation built:** `Idempotency-Key` is supported at the HTTP boundary. Same key + same request replays the original receipt/Payment Link metadata without appending a second decision row. Same key + changed request returns HTTP 409.

## 8. “Your JSONL audit can be edited.”

**Attack:** Append-only by convention is not enough.

**Mitigation built:** new decision rows are SHA-256 chained with `prev_hash` and `entry_hash`. `/audit/verify` recomputes the chain and reports the first failure. Existing pre-chain demo rows remain visible and are honestly counted as `legacy_rows`; the app does not rewrite history to pretend they were hashed.

**Remaining production work:** write the same receipt to Razorpay’s durable event/audit infrastructure. Local JSONL is an MVP sink, not a production ledger.

## 9. “Can anyone mark any simulated link paid?”

**Attack:** A forged `plink_SIMULATED_*` should not mutate state.

**Mitigation built:** the payment endpoint only accepts simulated IDs that are present in CodGate’s decision audit. Unknown simulated IDs return 404. Non-simulated IDs cannot be manually marked paid.

## 10. “Why Payment Links?”

**Answer:** Payment Links are the bounded intervention after a COD policy block: collect before shipping instead of cancelling the order. In test mode, the Razorpay Payment Link carries the CodGate receipt as `reference_id`/notes so the payment object can be traced back to the decision receipt.

## 11. “Why not just put this rule inside Magic Checkout?”

**Answer:** That may be the correct production destination. CodGate is not arguing for another customer-facing product. It demonstrates the missing governance contract: named/versioned policy, rollout mode, decision receipt, false-block economics, traceable enforcement and now a bounded method to repair customer-correctable false blocks without weakening structural risk. If Razorpay likes it, the best outcome could be absorbing this control-plane pattern into Magic Checkout/RTO operations rather than operating CodGate as a separate service.

## 12. “What would stop us shipping this tomorrow?”

- No private Magic Checkout/RTO signal integration in the public repo.
- No Razorpay service-to-service auth or merchant tenancy.
- n=80 evaluation is prototype evidence, not a production sample.
- Local JSONL/idempotency files should become durable infrastructure stores.
- Policy ownership/approval/rollback should use Razorpay’s internal controls.
- Real Payment Link webhooks should close the payment state loop; the public desk only marks simulated links locally.
- Risk Repair needs trusted field provenance/address validation before any corrected field can be used for enforcement.

Those are productionisation items. They are not hidden behind demo claims.
