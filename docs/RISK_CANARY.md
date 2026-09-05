# Risk Canary — bounded COD response for return-to-seller risk

## Scope

CodGate's **qualifying Track 02 loss class is RETURN_TO_SELLER (returns)**. The large primary detector is evaluated on that label because its source does not expose payment mode.

Risk Canary is an operational **COD/RTO response verifier** inside the same returns problem: before a candidate risk model/policy is allowed to change COD checkout behavior, Canary verifies its consequences on paired CURRENT-vs-CANDIDATE decisions and observed fulfilment outcomes.

It is not a second classifier and it is not the primary accuracy benchmark.

## Input

`POST /release/check` receives paired decisions for the same orders:

```json
{
  "release_id": "return-risk-v42",
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

Canary consumes precomputed decisions; it never calls or hides the candidate model.

## What Canary checks

- CURRENT and CANDIDATE TP/FP/FN/TN,
- precision/recall with Wilson 95% intervals,
- false-positive/false-negative rates,
- decision blast radius,
- merchant-segment false-block deltas,
- sample sufficiency,
- paired cost delta and uncertainty,
- replay SHA-256,
- deterministic release receipt.

The public Canary's rupee unit constants are regression **scenario assumptions**, not measured merchant economics. Production use must inject an approved merchant/Razorpay cost model.

## Fail-closed release policy

A candidate can be `BLOCK_RELEASE` for worsening modeled loss, materially lower recall or a large evidence-qualified merchant-slice false-block regression.

A candidate stays `SHADOW` when the replay is too small, improvement uncertainty crosses zero, blast radius is too wide, or a merchant slice has a smaller but meaningful false-block regression.

Only a candidate clearing all configured evidence and safety gates can `SHIP`.

## Deterministic verifier fixtures

```text
GET /release/demo/good  -> SHIP
GET /release/demo/wide  -> SHADOW
GET /release/demo/bad   -> BLOCK_RELEASE
```

These are synthetic code-path tests only. They do not contribute to the Track 02 accuracy claim.

## How it complements the primary detector

```text
real RETURN_TO_SELLER detector
        ↓ advisory risk score
bounded review / candidate COD policy
        ↓ shadow decisions
observed fulfilment outcomes
        ↓
Risk Canary
        ↓
SHIP / SHADOW / BLOCK_RELEASE + receipt
```

The large detector establishes the held-out precision/recall requirement. Canary demonstrates the payments-platform safety behavior that should wrap any model before checkout actions are changed.

## Domain checks

- Meesho exact-RTO external validation is weak and receives `BLOCK_RELEASE`.
- Boss Leathers exact-COD terminal slice has 47 rows and is kept as an audit only.
- The handcrafted n=80 file and 200-row Canary scenarios are regression fixtures only.

## Production boundary

The public repository does not have Razorpay private risk features, model registry, production replay window, merchant margin model, service auth or production payment credentials. A production deployment should bind Canary to those governed internal systems rather than copy public prototype thresholds blindly.
