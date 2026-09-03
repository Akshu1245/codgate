# CodGate Risk Canary — Razorpay-internal COD RTO verifier

## One problem only

**Loss class: COD Return-to-Origin (RTO).**

CodGate does not claim a second fraud problem here. The order gate, Risk Repair and Risk Canary are three controls around the same loss class:

1. **Order gate** — execute the currently approved COD policy.
2. **Risk Repair** — prove whether customer-correctable data can safely restore COD under that same policy.
3. **Risk Canary** — verify a candidate RTO model/policy release before Razorpay changes production checkout behaviour.

The public prototype does not contain Razorpay's private RTO model. Inside Razorpay, the upstream system produces the risk outputs; Canary verifies the consequences.

## Why this exists

A candidate RTO model can improve one aggregate metric and still be unsafe. It can false-block a merchant cohort, lower recall, change too much traffic at once, or show an apparent rupee improvement that is not supported by enough observations.

The question Canary answers is therefore:

> **Has this candidate earned the right to change production COD decisions?**

This is a Razorpay-internal control. No new e-commerce company has to install it for Razorpay to benefit.

## Input contract

`POST /release/check` consumes **paired** current/candidate decisions on the same orders, joined to observed outcomes:

```json
{
  "release_id": "magic-rto-v42",
  "rows": [
    {
      "order_id": "ord_123",
      "merchant_segment": "enterprise",
      "amount": 3499,
      "actual_rto": true,
      "current_decision": "ALLOW_COD",
      "candidate_decision": "FORCE_PREPAID"
    }
  ]
}
```

Production sources inside Razorpay would be:

- current production RTO decision,
- candidate shadow/replay decision,
- observed fulfilment outcome (`RTO` / delivered),
- order amount,
- merchant cohort/segment.

Canary never calls a candidate model and never hides model code inside the verifier.

## Evidence it computes

For **current**, **candidate**, **always allow** and **always force prepaid** it reports:

- TP / FP / FN / TN,
- precision and Wilson 95% confidence interval,
- recall and Wilson 95% confidence interval,
- false-positive and false-negative rates,
- false-block ₹ cost,
- missed-RTO ₹ cost,
- total modeled ₹ loss,
- blocked / false-block / missed-RTO GMV.

It additionally reports:

- paired 95% interval for current→candidate cost delta,
- decision blast radius and direction of flips,
- merchant-segment false-block deltas,
- whether each segment has enough delivered observations to enforce a slice guardrail,
- deterministic SHA-256 of the exact replay window,
- deterministic `cgrl_...` release receipt bound to the data identity, governance version and verdict.

## Fail-closed governance v2

These are **release** thresholds, not transaction-risk weights.

A candidate is `BLOCK_RELEASE` when:

- modeled ₹ loss increases at all,
- recall drops by more than 2 percentage points, or
- an evidence-qualified merchant segment's false-block rate worsens by more than 5 percentage points.

A candidate stays `SHADOW` when:

- the replay has fewer than 100 rows, 20 observed RTOs or 50 delivered orders,
- modeled loss looks better but the paired 95% cost-delta interval still crosses zero,
- more than 15% of decisions change, or
- an evidence-qualified merchant segment's false-block rate worsens by more than 2 percentage points.

Only a candidate that clears all of those checks can `SHIP`.

A segment needs at least 20 delivered examples before its false-block delta is allowed to govern a release. Small slices are shown as **LOW N** instead of being converted into a confident-looking percentage.

## Judge fixtures

The product exposes three deterministic 200-row fixtures:

```text
GET /release/demo/good  -> SHIP
GET /release/demo/wide  -> SHADOW
GET /release/demo/bad   -> BLOCK_RELEASE
```

- **good** repairs a bounded subset of current false blocks and misses and has a paired improvement interval below zero.
- **wide** is perfect on the synthetic replay but changes 20% of decisions, so it is deliberately not shipped immediately.
- **bad** adds false blocks/misses and is blocked.

The fixtures are synthetic and demonstrate verifier behaviour only. They are **not** claims about Razorpay production accuracy.

## Direct benefit to Razorpay

The order-level RTO model may already be excellent. That does not eliminate release risk.

A bad threshold/model/rule release can propagate across merchants already on Razorpay. Risk Canary gives the Risk/ML/Payments Platform team a deterministic pre-production check that answers:

- Does the candidate actually reduce the priced loss?
- Is the apparent win statistically supported on this window?
- Did recall get worse?
- Is a merchant cohort paying for the aggregate improvement?
- How much checkout behaviour changes on day one?
- Which exact replay and governance version authorized the release?

That is direct platform value: **verify the risk system before the risk system is allowed to change money-moving behaviour.**

## How this maps to Track 02

Track 02 allows a **detector, verifier or auto-responder** for one class of merchant loss and asks for held-out precision/recall plus honest false-positive cost.

CodGate chooses **verifier + bounded enforcement** for one class: COD RTO. The frozen `data/heldout.csv` remains the public v1.0 transaction-policy benchmark. Risk Canary is the production-style verifier that Razorpay could put around a stronger internal RTO model without replacing it.

## Production integration

```text
candidate RTO model / rule
          ↓
current + candidate shadow outputs
          ↓
observed courier outcomes
          ↓
CodGate Risk Canary
          ↓
SHIP / SHADOW / BLOCK_RELEASE
          ↓
model registry + approval + rollout record
```

A production implementation should use Razorpay durable storage, service authentication, approved cohort definitions, real outcome joins, a governed cost model, and an internal model/policy registry. The public release thresholds are prototype defaults and should not be copied blindly into production.

## What this does not claim

- No access to Razorpay private replay data.
- No claim that the synthetic 200-row fixtures represent production performance.
- No claim that the current public 80-row RTO benchmark is sufficient for a global rollout.
- No claim that Canary replaces model validation; it is a **release consequence verifier** on top of model validation.
- No second loss class. No offensive capability. No automatic global deployment path.
