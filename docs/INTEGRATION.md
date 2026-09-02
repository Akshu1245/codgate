# CodGate integration contract

CodGate is meant to sit after an RTO signal and before COD fulfilment. The public prototype uses only the frozen v1.0 order fields; it does not pretend to call a private Magic Checkout scoring API.

## Rollout

1. **Shadow** — send real checkout-shaped orders with `X-CodGate-Mode: shadow`. CodGate records the decision and receipt but never issues a Payment Link.
2. **Repair** — for a FORCE decision, inspect the attached `risk_repair` proof. If it says `REPAIRABLE`, collect the legitimate correction through a trusted checkout flow and re-score. If it says `STRUCTURAL_RISK`, do not weaken historical/location risk.
3. **Observe** — feed final `DELIVERED` / `RTO` fulfilment outcomes into the separate outcome ledger and inspect `/metrics/live`. Do not mutate the frozen v1.0 held-out labels.
4. **Enforce** — switch the same request path to `X-CodGate-Mode: enforce`. Only `FORCE_PREPAID` can issue a Payment Link. `decide()` itself is unchanged.
5. **Version** — any rule/weight change becomes a new policy version with a new evaluation and provenance hash.

## Score one order

```bash
curl -X POST http://localhost:8000/orders/score \
  -H 'Content-Type: application/json' \
  -H 'X-CodGate-Mode: shadow' \
  -H 'Idempotency-Key: checkout-ord-841226-001' \
  -d '{
    "order_id":"ord_siwan_temple_01",
    "pincode":"841226",
    "address":"near temple",
    "amount":3499,
    "account_age_days":2,
    "prepaid_orders":0,
    "prior_rto_count":2,
    "orders_count":0,
    "phone":"9123456780",
    "customer_name":"Ravi Kumar"
  }'
```

Shadow returns the real policy decision (`FORCE_PREPAID`) plus `action: OBSERVE_ONLY`, `would_issue_payment_link: true`, a deterministic `receipt_id`, request hash, policy source hash, chained audit hash and a read-only `risk_repair` proof. No link is issued.

Change only the mode header to `enforce` to execute the same decision. With Razorpay test keys present, the HTTP layer creates a Razorpay test Payment Link. Without them, it returns an explicit `plink_SIMULATED_*` link.

## Risk Repair contract

`POST /orders/repair` accepts the same order shape and never writes an audit decision or issues a Payment Link. It is a counterfactual proof against the same Policy v1.0.

Possible statuses:

- `ALREADY_SAFE` — Policy v1.0 already allows COD.
- `REPAIRABLE` — a legitimate customer-correctable defect can move the same policy below 50; the response states the criterion and before/after points.
- `STRUCTURAL_RISK` — even the strongest allowed repair remains at or above 50; keep prepaid.
- `NEEDS_CORRECTION` — a required field such as pincode/address/amount is invalid; CodGate refuses to invent a replacement value.

The prototype's only scored repair class is **address completion**. Risk Repair never edits `prior_rto_count`, `account_age_days`, `orders_count`, `prepaid_orders`, `amount` or the pincode risk band merely to cross the threshold. The response includes a deterministic `cgrr_...` repair receipt.

Production integration should accept corrected fields only from a trusted checkout/address-validation source. A customer-supplied prettier string is not proof that the physical address is true.

## Retry contract

`Idempotency-Key` is optional for the desk and recommended for checkout integrations.

- Same key + same request → HTTP 200, original receipt/link metadata, `idempotent_replay: true`, no second decision audit row.
- Same key + different request → HTTP 409.
- No key → every POST is treated as a new scoring event.

The idempotency store hashes the key and stores replay metadata only. It does not duplicate customer name/phone/address.

## Fulfilment outcome contract

The frozen benchmark is not a live-learning database. Real rollout evidence is kept separately.

```bash
curl -X POST http://localhost:8000/orders/ord_siwan_temple_01/outcome \
  -H 'Content-Type: application/json' \
  -d '{"outcome":"RTO","source":"courier"}'
```

`outcome` is exactly `DELIVERED` or `RTO`.

- An outcome is accepted only when that `order_id` already has a CodGate decision.
- Repeating the same outcome is idempotent.
- Trying to overwrite an observed outcome with the opposite result returns HTTP 409 rather than rewriting history.
- Outcome rows are stored in their own SHA-256-chained `outcomes.jsonl` ledger.
- `GET /metrics/live` joins the latest decision per order to its observed outcome and reports coverage, TP/FP/FN/TN, observed precision/recall, ₹ false-block/missed-RTO cost, shadow volume and prepaid conversion for enforced FORCE decisions.
- None of these writes touch `data/heldout.csv` or its frozen SHA.

In a Razorpay production integration this endpoint would normally be replaced by signed/internal courier or fulfilment events; the contract is here so the public prototype can close the loop end-to-end.

## Provenance and readiness

- `GET /health` — liveness, policy/source hash and default execution mode.
- `GET /ready` — frozen held-out SHA + decision-chain + outcome-chain verification; returns 503 if any integrity check fails.
- `GET /policy/manifest` — policy version, frozen date, threshold and source-file SHA-256s.
- `POST /orders/repair` — counterfactual repair proof under the unchanged frozen policy.
- `GET /audit/verify` — recomputes the decision hash chain.
- `GET /outcomes/verify` — recomputes the observed-outcome hash chain.
- `GET /metrics/live` — observed traffic evidence, kept separate from frozen metrics.
- `GET /ops/status` — rollout mode, Payment Link provider state, policy provenance, integrity state and live-evidence summary without exposing credentials.

## Payment Link trace

When Razorpay test keys are present, CodGate sends the decision receipt as the Payment Link `reference_id` and includes `order_id`, `policy_version` and `codgate_receipt` in notes. This gives a direct trace from risk receipt → Razorpay Payment Link.

Live keys are intentionally rejected by this public prototype.
