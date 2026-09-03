# CodGate Risk Canary — Razorpay-internal release verifier

## Why this exists

The checkout gate is merchant-facing. Risk Canary is different: it is intended for Razorpay's own risk/model release path. It does not require a new e-commerce company to integrate CodGate.

The question Canary answers is:

> **Should Razorpay ship this candidate RTO policy/model release to production?**

A candidate can improve aggregate precision/recall and still be dangerous if it changes too much traffic, increases modeled ₹ loss, or moves false blocks into a specific merchant segment. Canary turns those release risks into a deterministic gate.

## Input

Canary does not call or contain the current/candidate model. The owning risk system supplies precomputed decisions:

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

Production source inside Razorpay would typically be a replay/shadow window joined from:

- current production risk decision,
- candidate model/rule decision,
- fulfilment/RTO outcome,
- order amount,
- merchant cohort/segment.

## Output

`POST /release/check` returns:

- `SHIP`, `SHADOW`, or `BLOCK_RELEASE`,
- deterministic `cgrl_...` release receipt,
- current vs candidate TP/FP/FN/TN,
- precision/recall,
- false-block and missed-RTO modeled ₹ loss,
- blocked GMV / false-block GMV / missed-RTO GMV,
- decision blast radius,
- merchant-segment false-block deltas,
- up to eight changed-order examples,
- the governance rule that caused the verdict.

## Current prototype governance

These are release-safety thresholds, not transaction-risk weights:

- any increase in modeled ₹ loss -> `BLOCK_RELEASE`,
- merchant-segment false-block-rate worsening > 5 percentage points -> `BLOCK_RELEASE`,
- decision blast radius > 15% -> `SHADOW`,
- merchant-segment false-block-rate worsening > 2 percentage points -> `SHADOW`,
- otherwise -> `SHIP`.

The important behaviour is that **a candidate can be better and still be forced to shadow** if it changes too much production traffic at once.

## Judge fixtures

```text
GET /release/demo/good  -> SHIP
GET /release/demo/wide  -> SHADOW
GET /release/demo/bad   -> BLOCK_RELEASE
```

These rows are synthetic and exist only to demonstrate the release verifier. They are not evidence of Razorpay production performance.

## Direct benefit to Razorpay

This control protects a Razorpay-owned deployment decision. If a bad RTO model or rule release is pushed globally, the impact can propagate across merchants already using Razorpay. Canary can stop that release before it reaches production.

It therefore does not depend on convincing a new e-commerce company to install a product. The intended user is Razorpay Risk/ML/Payments Platform itself.

## Production integration

A production version should be called automatically from the model/policy release pipeline:

```text
candidate model/rule build
        ↓
internal replay / shadow outputs
        ↓
CodGate Risk Canary
        ↓
SHIP / SHADOW / BLOCK_RELEASE
        ↓
approval + rollout / shadow / stop
```

The release receipt should be stored with the internal model-registry/policy version and deployment record.

## What this does not claim

- The public repo does not have Razorpay's private replay data.
- Demo release windows are synthetic.
- Canary does not prove a candidate model is statistically valid on its own; the quality of the replay window matters.
- Production thresholds should be owned and calibrated by Razorpay risk leadership, not copied blindly from this prototype.
- Canary is not another RTO model. It verifies the consequences of a candidate produced elsewhere.
