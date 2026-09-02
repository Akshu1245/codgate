# CodGate

Magic Checkout already scores RTO. We expose the gate — named rules, frozen metrics, Payment Link when we block COD.

One class: **will this COD RTO.** Policy v1.0 frozen 2026-09-02. `decide()` is a pure function: no network, no LLM. Submit as **AI Risk Manager**.

## Policy

STOP short-circuits. Else additive points. FORCE_PREPAID at ≥ 50.

| Id | Name | Pts | When |
|---|---|---|---|
| R1 | STOP_PINCODE_INVALID | STOP | Pincode is not 6 digits |
| R2 | STOP_AMOUNT_INVALID | STOP | Amount ≤ 0 |
| R3 | STOP_ADDRESS_EMPTY | STOP | Address is blank |
| R4 | LANDMARK_ONLY | +40 | near temple / mandir / mosque, no house number |
| R5 | HIGH_RTO_PIN | +25 | Pincode RTO ≥ 28% |
| R6 | NEW_CUSTOMER | +20 | Zero orders or account age < 21 days |
| R7 | HIGH_TICKET | +15 | COD ≥ ₹3,000 |
| R8 | PRIOR_RTO_PHONE | +35 | This phone already has an RTO |
| R9 | NO_PREPAID_HISTORY | +10 | Zero prepaid orders |
| R10 | SHORT_ADDRESS | +20 | Address under 12 characters |
| R11 | MID_RTO_PIN | +10 | Mid RTO band |
| R13 | PARTIAL_ADDRESS | +8 | House or locality, not both |
| C1 | PREPAID_VETERAN | −15 | ≥ 3 prepaid orders |
| C2 | OLD_CUSTOMER | −10 | Account age ≥ 180 days |
| C3 | LOW_RTO_PIN | −10 | Pincode RTO ≤ 8% |
| C4 | COMPLETE_ADDRESS | −8 | House number and street both present |

Canonical cases: good Indiranagar veteran → `ALLOW_COD`. `near temple` + Siwan + new + ₹3499 + prior RTO → `FORCE_PREPAID` (`plink_SIMULATED`). pincode `56` → `STOP`.

## Held-out (n=80, labels frozen)

SHA-256 `327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a`

```
CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
```

Confusion: TP 23 · FP 8 · FN 15 · TN 34.

## What broke

A new customer with a complete address on a high-RTO pin lands at 47 — under the frozen 50 cut — so Siwan with a real house number still ships COD (h42–h45, h80). Prepaid veterans on metro pins still RTO and we miss them (h13, h14); credits drive the score to zero. Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73). We over-block temple drops that happened to deliver (h25–h27, h34–h35) and a couple of short mid-pin addresses (h60, h62).

Razorpay keys are not configured; FORCE_PREPAID writes `plink_SIMULATED`.
