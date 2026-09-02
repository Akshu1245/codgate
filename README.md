# CodGate

Magic Checkout already scores RTO. We expose the gate — named rules, frozen metrics, Payment Link when we block COD.

**Track 02 — AI Risk Manager.** One class: **will this COD RTO.** Policy v1.0 is frozen on 2026-09-02. `decide(order)` is pure: no network, no LLM, no I/O. The HTTP layer alone issues a Razorpay **test** Payment Link when test keys are present; otherwise it writes `plink_SIMULATED_{order_id}` and says so in the result stamp.

## Policy v1.0

STOP short-circuits. Else points are additive, floored at 0. `FORCE_PREPAID` iff points ≥ 50; otherwise `ALLOW_COD`.

| ID | Rule | Points | When |
|---|---|---:|---|
| R1 | PINCODE_INVALID | STOP | Pincode is not 6 digits |
| R2 | AMOUNT_INVALID | STOP | Amount ≤ 0 |
| R3 | ADDRESS_EMPTY | STOP | Address is empty |
| R4 | LANDMARK_ONLY | +40 | near/opp/beside temple, mandir, mosque, masjid, church, dargah or gurudwara; no house number |
| R5 | HIGH_RTO_PIN | +25 | Pin RTO ≥ 28%; frozen Bihar / east-UP / NE belt and known high pins |
| R6 | NEW_CUSTOMER | +20 | `orders_count == 0` or `account_age_days < 21` |
| R7 | HIGH_TICKET | +15 | Amount ≥ ₹3,000 |
| R8 | PRIOR_RTO_PHONE | +35 | `prior_rto_count ≥ 1` |
| R9 | NO_PREPAID_HISTORY | +10 | `prepaid_orders == 0` |
| R10 | SHORT_ADDRESS | +20 | Address < 12 chars, after landmark check |
| R11 | MID_RTO_PIN | +10 | Frozen mid pin band |
| R13 | PARTIAL_ADDRESS | +8 | House or locality, not both / otherwise incomplete |
| C1 | PREPAID_VETERAN | −15 | `prepaid_orders ≥ 3` |
| C2 | OLD_CUSTOMER | −10 | `account_age_days ≥ 180` and customer is not new |
| C3 | LOW_RTO_PIN | −10 | Pin RTO ≤ 8% |
| C4 | COMPLETE_ADDRESS | −8 | House number and street/locality |

Known high pins: `841226`, `848101`, `854301`, `846004`, `277001`, `271001`, `786001`, `795001`. Known low pins include `560038`, `560001`, `400001`, `400050`, `110001`, `110017`, `600001`, `500001`, `411001`, `380001`, `560034`. Known pins are resolved before the frozen prefix table; unknown prefixes fall to mid.

## Frozen metrics

```text
CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a
```

## What broke

- Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).
- Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).
- Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).

## Canonical cases

`ALLOW_COD`: `ord_blr_vet_01`, `560038`, complete Indiranagar address, ₹899, age 640, prepaid 11, prior RTO 0, orders 24 → **0 pts**, C4+C3+C2+C1.

`FORCE_PREPAID`: `ord_siwan_temple_01`, `841226`, `near temple`, ₹3499, age 2, prepaid 0, prior RTO 2, orders 0 → **145 pts**, R4+R5+R6+R7+R8+R9. `decide()` still returns `payment_link=None`; the HTTP layer issues the link.

`STOP`: `ord_bad_pin_01`, pincode `56` → **R1**.

## Run

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pytest -q
python -m app.score
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The desk has Gate / Policy / Metrics / Audit, the three canonical buttons, a custom-order form, simulated or Razorpay-test Payment Link handling, held-out CSV download, the tape, and append-only `audit.jsonl`.

For a real **test-mode** Payment Link only, set `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET=...`. Live keys are ignored by design.
