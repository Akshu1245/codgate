# Judge scorecard — CodGate Track 02

## 30-second decision

- **Problem:** merchant loss from returns.
- **One measured loss class:** `RETURN_TO_SELLER`.
- **Working detector:** yes, frozen local runtime model.
- **Real heldout:** 5,726 unique orders / 362 returns.
- **Precision / Recall:** 11.21% / 23.20%.
- **Base rate / lift:** 6.32% / 1.77× precision lift.
- **False positives:** 665; ₹443,627 legitimate-order GMV exposed to intervention.
- **Cost honesty:** actual margin loss unavailable; cost becomes `665 × merchant-approved unit cost`.
- **Safety:** model is advisory-only; SHADOW creates no Payment Link.
- **Reproducibility:** source SHA + model SHA + CI rebuild + Chromium journey.
- **Exact-domain checks:** Meesho exact-RTO fails closed; 47-row exact-COD slice is audit-only.

## What to click

1. Control Room → verify real detector evidence.
2. Score an order → see `risk_score`, threshold and advisory decision.
3. Risk Canary → inspect exact-RTO `BLOCK_RELEASE`.
4. Decision Desk in SHADOW → verify no Payment Link.
5. Audit Terminal → verify chained ledgers.

## What not to claim

Do not say 11.21% is production accuracy, do not call the large benchmark COD-only, do not call the risk score a probability, and do not call ₹443,627 realized merchant loss.
