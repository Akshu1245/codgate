# Track 02 fit — qualification audit

CodGate submits one loss class only: **COD Return-to-Origin (RTO)**.

This document maps the public prototype to the Track 02 bar without treating extra engineering as extra problem statements.

| Track 02 requirement | CodGate evidence |
|---|---|
| One class of merchant loss | COD RTO only |
| Working detector / verifier / auto-responder | RTO decision verifier + bounded policy executor |
| Held-out test set | `data/heldout.csv`, frozen n=80 |
| Precision | 74.2% on frozen v1.0 benchmark |
| Recall | 60.5% on frozen v1.0 benchmark |
| Honest false-positive cost | FP 8 × ₹180 = ₹1,440 |
| Honest false-negative cost | FN 15 × ₹250 = ₹3,750 |
| Reproducible evidence | exact CSV SHA-256; `python -m app.score` |
| Defense-only | no offensive tooling; decisions only restrict/verify COD risk |
| Working intervention | `FORCE_PREPAID` attaches Razorpay test Payment Link at HTTP layer; pure `decide()` has no side effect |
| Failure disclosure | exact false-positive/false-negative row groups in README / Metrics |
| Auditability | deterministic decision/repair/release receipts + chained audit/outcome ledgers |
| Bounded uncertainty | shadow mode; Risk Canary sample-size, CI, slice and blast-radius gates |
| Real-world learning loop | observed `DELIVERED / RTO` endpoint and separate live economics |

## Why the verifier is the primary product claim

A public team cannot credibly claim that an 80-row deterministic rule policy outperforms Razorpay's private RTO intelligence or every public RTO model trained on larger synthetic/Kaggle-derived data.

CodGate therefore makes the narrower claim that is actually implemented and testable:

> **An RTO decision should not change money-moving checkout behaviour until its error cost, merchant-slice impact, evidence quality and rollout blast radius are verified and recorded.**

That is why Risk Canary consumes precomputed current/candidate decisions rather than embedding another classifier.

## What is prototype evidence vs production evidence

**Prototype evidence**
- frozen n=80 transaction benchmark,
- deterministic policy v1.0,
- three canonical order cases,
- deterministic 200-row Canary behavior fixtures,
- local/simulated Payment Link fallback when Razorpay test credentials are absent.

**Production evidence required inside Razorpay**
- large paired replay/shadow window from Razorpay-owned traffic,
- trusted fulfilment outcomes,
- approved merchant cohort definitions,
- internal cost model,
- service authentication and durable audit storage,
- internal model/policy registry and rollout approvals.

The repo deliberately keeps those categories separate. Synthetic fixtures are never presented as Razorpay production accuracy.

## One-command reviewer path

```bash
pytest -q
python -m app.score
python -m app.preflight
```

Then use the desk:

1. ALLOW canonical order.
2. FORCE canonical order and inspect the Payment Link.
3. STOP bad pincode.
4. Inspect Counterfactual Risk Repair: canonical Siwan remains `STRUCTURAL_RISK`, 145 → 97.
5. In Policy, run Risk Canary: safe candidate → `SHIP`; wide candidate → `SHADOW`; bad candidate → `BLOCK_RELEASE`.
6. Read the frozen Metrics and Audit evidence.

The reviewer should be able to verify the core claims in five minutes without trusting a marketing statement.
