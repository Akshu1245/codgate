# CodGate — judge red-team

This is the attack list we would use if we were reviewing CodGate for Razorpay. It is intentionally harsher than the README.

## 1. “Where is the AI?”

**Attack:** Policy v1.0 is deterministic. It is not a learned model.

**Answer:** Correct. CodGate does not claim that the policy is an ML model. Magic Checkout already has RTO intelligence. CodGate is the control plane after risk intelligence: versioned policy, reasons, economic evaluation, intervention and audit. A money-moving rule should be inspectable even when an upstream model changes.

**Remaining production work:** Bind the order contract to Razorpay’s internal RTO signal/features. This public prototype cannot claim access to that private interface.

## 2. “Doesn’t this benefit Razorpay only if another e-commerce company integrates CodGate?”

**Attack:** A merchant-side COD gate creates indirect value. If merchants do not integrate it, Razorpay gets no new benefit.

**Mitigation built:** **Risk Canary is Razorpay-internal.** It sits in the risk-model/policy release path, not in merchant checkout. Before a new Magic Checkout RTO model/rule release reaches production, Razorpay feeds Canary precomputed current-vs-candidate decisions plus Razorpay-owned observed outcomes. Canary returns `SHIP / SHADOW / BLOCK_RELEASE`, ₹ impact, decision blast radius, segment regressions and a release receipt. No new e-commerce integration is required.

**Direct company value:** a bad risk release can false-block legitimate checkout traffic or miss RTO across merchants already on Razorpay. Canary can stop that release before rollout.

## 3. “Why not let the ML team look at precision/recall and ship?”

**Attack:** Model metrics already exist. A separate release gate looks redundant.

**Answer:** Aggregate precision/recall do not tell a risk lead whether a candidate changes 30% of production traffic, whether one merchant segment absorbs the new false blocks, or whether the modeled ₹ loss actually gets worse. Canary adds release governance around those outputs. It can force a better-looking model into `SHADOW` when blast radius is too large.

**Built guardrails:** any modeled ₹ loss increase blocks release; >15% decision blast radius prevents direct ship; merchant-segment false-block regressions can force `SHADOW` or `BLOCK_RELEASE`.

## 4. “Is Canary secretly another hard-coded RTO policy?”

**Attack:** You said one class/one policy, but now there may be two competing policies.

**Answer:** No. Canary accepts **precomputed** `current_decision` and `candidate_decision`. It never calculates either one. The candidate can be an ML model, rule release or threshold change owned by Razorpay’s existing risk system. Canary verifies whether releasing those outputs is safe. There is still only one transaction policy in this public CodGate repo: v1.0.

## 5. “Hard-coded rules will drift.”

**Attack:** Pincode/address rules become stale.

**Answer:** v1.0 is deliberately frozen. CodGate supports `shadow` execution so a new policy can be measured before enforcement. A rule change should produce a new policy version and a new held-out evaluation; v1.0 is never silently mutated. Canary is the control that can then verify the candidate release against a larger internal replay window.

**Remaining production work:** policy registry, approval workflow, owner, expiry/review date and controlled v1.1 rollout.

## 6. “Your held-out set is only 80 rows.”

**Attack:** The metrics are too small to support a production claim.

**Answer:** Agree. The 80-row file is evidence that v1.0 evaluation is reproducible and honest, not evidence of production accuracy. It exposes its SHA-256 and named false positives/false negatives.

**Canary distinction:** production Risk Canary is not supposed to run on those 80 rows. Its intended input is a much larger Razorpay-owned shadow/replay window containing current decisions, candidate decisions and observed outcomes.

## 7. “FORCE_PREPAID can destroy conversion.”

**Attack:** A false block creates customer friction and lost margin.

**Answer:** The held-out report prices false blocks explicitly. `shadow` mode allows Razorpay to measure the counterfactual before issuing a link. `enforce` is separate from the policy decision.

**Additional mitigation built:** Counterfactual Risk Repair distinguishes blocks caused by customer-correctable input quality from structural/historical risk. A legitimate address completion can be proven against the same frozen policy before asking the customer to prepay.

## 8. “Is Risk Repair just gaming the score until COD passes?”

**Attack:** A counterfactual engine could manufacture an allow by editing customer history, amount, pincode or other risk fields.

**Mitigation built:** Risk Repair never changes `prior_rto_count`, account age, order count, prepaid history, amount or the pincode risk band to cross the threshold. Its only scored repair class in this prototype is **address completion**. It runs the candidate back through the same `decide()` function; there is no second policy and no override path.

The canonical Siwan order proves the guardrail: 145 pts before address completion, 97 pts after the strongest legitimate address repair, still `FORCE_PREPAID`.

## 9. “What stops a customer lying about the corrected address?”

**Attack:** A client could submit a nicer-looking address solely to regain COD.

**Answer:** Nothing in this public prototype proves physical address truth, so Risk Repair does **not** automatically flip the original decision. It returns the criterion and asks the order to be re-scored after correction.

**Remaining production work:** only accept repair fields from trusted checkout/address-validation sources, bind evidence to the receipt, and log who/what verified the correction.

## 10. “Retries can issue two Payment Links.”

**Mitigation built:** `Idempotency-Key` is supported at the HTTP boundary. Same key + same request replays the original receipt/Payment Link metadata without appending a second decision row. Same key + changed request returns HTTP 409.

## 11. “Your JSONL audit can be edited.”

**Mitigation built:** new decision rows are SHA-256 chained with `prev_hash` and `entry_hash`. `/audit/verify` recomputes the chain and reports the first failure. Existing pre-chain demo rows remain visible and are counted as legacy rows.

**Remaining production work:** write the same receipt to Razorpay’s durable event/audit infrastructure. Local JSONL is an MVP sink, not a production ledger.

## 12. “Can anyone mark any simulated link paid?”

**Mitigation built:** the payment endpoint only accepts simulated IDs that are present in CodGate’s decision audit. Unknown simulated IDs return 404. Non-simulated IDs cannot be manually marked paid.

## 13. “Why Payment Links?”

**Answer:** Payment Links are the bounded intervention after a COD policy block: collect before shipping instead of cancelling the order. In test mode, the Razorpay Payment Link carries the CodGate receipt as `reference_id`/notes so the payment object can be traced back to the decision receipt.

## 14. “Why not just put all of this inside Magic Checkout?”

**Answer:** That may be the correct production destination. CodGate is not arguing for another customer-facing product. It demonstrates a control-plane pattern: named/versioned policy, rollout mode, decision receipt, false-block economics, bounded repair and **release verification**. If Razorpay likes it, Risk Canary belongs in the internal risk-release path and the transaction controls can be absorbed into Magic Checkout/RTO operations.

## 15. “What would stop us shipping this tomorrow?”

- No private Magic Checkout/RTO signal integration in the public repo.
- No internal model-registry/release-pipeline integration for Risk Canary.
- No Razorpay service-to-service auth or merchant tenancy.
- n=80 transaction evaluation is prototype evidence, not a production sample.
- Canary demo windows are synthetic; production Canary must use Razorpay-owned replay/shadow data.
- Local JSONL/idempotency files should become durable infrastructure stores.
- Policy ownership/approval/rollback should use Razorpay’s internal controls.
- Real Payment Link webhooks should close the payment state loop.
- Risk Repair needs trusted field provenance/address validation before corrected fields can be enforced.

Those are productionisation items. They are not hidden behind demo claims.
