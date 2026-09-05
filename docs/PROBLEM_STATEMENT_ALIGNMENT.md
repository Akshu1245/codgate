# Track 02 — AI Risk Manager: exact problem-statement alignment

This document is the acceptance checklist for CodGate. It uses the supplied Track 02 wording as the source of truth and separates what the public prototype proves from what it does not prove.

## Supplied problem statement

> **AI Risk Manager**
>
> **Stop the merchant losing money to fraud, returns and chargebacks**
>
> Build a working detector, verifier or auto-responder for one class of loss, with measured precision and recall on a held-out test set.
>
> **why now**
>
> AI-enabled fraud is hitting Indian BFSI while returns and chargebacks quietly eat margin. This track surfaces the risk and ML minded builders the others miss.
>
> **example directions**
>
> - Chargeback evidence responder
> - Return-risk scorer
> - Fraud-spike detector
> - Abuse-ring sentinel
>
> **the bar**
>
> Honest metrics including false-positive cost. Strictly defense-only: anything offense-capable is disqualifying.

## CodGate claim in one sentence

**CodGate is a defense-only COD Return-to-Origin (RTO) decision verifier and bounded policy executor that measures held-out decision quality and error cost, prevents unsafe risk releases from changing checkout, and can force prepaid only after the governed decision.**

## Requirement-by-requirement qualification

| Supplied requirement | CodGate implementation | Evidence | Status |
|---|---|---|---|
| Stop merchant loss | Targets COD RTO, where an undelivered COD order creates return/logistics loss and margin leakage | `README.md`, `app/policy.py`, held-out outcomes | MATCH |
| One class of loss | Only **COD Return-to-Origin (RTO)** is submitted | preflight reports `loss class COD_RTO` | MATCH |
| Working detector, verifier or auto-responder | Primary claim is **verifier + bounded executor**; transaction policy returns `ALLOW_COD / FORCE_PREPAID / STOP`; Risk Canary verifies candidate RTO releases | `/orders/score`, `/release/check`, browser judge journey | MATCH |
| Measured precision | Frozen held-out v1.0: **74.2%** | `python -m app.score`; TP 23, FP 8 | MATCH |
| Measured recall | Frozen held-out v1.0: **60.5%** | `python -m app.score`; TP 23, FN 15 | MATCH |
| Held-out test set | `data/heldout.csv`, frozen at n=80 and protected by exact SHA-256 | SHA `327f392d...e20a`; CI fails on drift | MATCH, PROTOTYPE-SCALE |
| Honest false-positive cost | **8 false blocks × ₹180 modeled unit cost = ₹1,440** | `app/score.py`, frozen metrics | MATCH WITH DISCLOSED ASSUMPTION |
| Honest false-negative cost | **15 missed RTO × ₹250 modeled unit cost = ₹3,750** | `app/score.py`, frozen metrics | EXTRA TRANSPARENCY |
| Strictly defense-only | No attack generation, credential abuse, bypass tooling, fraud simulation for exploitation, or offensive actions. Outputs only allow/restrict/stop COD and verify risk releases | code surface + preflight | MATCH |
| Working intervention | In `enforce`, only `FORCE_PREPAID` can create a Razorpay **test** Payment Link or explicit simulation. In `shadow`, no Payment Link is created | `/orders/score`, `app/payment.py`, browser E2E | MATCH |

## Critical honesty disclosures

### 1. The n=80 benchmark is prototype evidence

The public held-out file is intentionally small. Its precision and recall are real calculations over that frozen file, but they are **not production-accuracy claims** and must never be presented as Razorpay-scale model performance.

For a production decision, CodGate's Risk Canary expects a much larger Razorpay-owned replay/shadow window joined to trusted fulfilment outcomes.

### 2. ₹180 and ₹250 are modeled unit-cost assumptions

The frozen prototype uses:

- false-positive / false-block unit cost = **₹180**,
- false-negative / missed-RTO unit cost = **₹250**.

These are **evaluation assumptions**, not claims that every merchant actually loses exactly those amounts. A real merchant/Razorpay deployment must replace them with an approved internal cost model. The purpose of the public benchmark is to show that error cost is explicitly priced instead of hidden.

### 3. CodGate does not claim to replace Razorpay's private RTO model

The strongest product claim is governance: a candidate RTO signal should not change money-moving checkout behaviour until error cost, recall, merchant slices, uncertainty and blast radius clear a recorded release gate.

Risk Canary therefore consumes paired current/candidate decisions. It does not pretend that the public repository contains Razorpay's private model or traffic.

## Why this remains one problem, not feature sprawl

- **Decision Desk**: governs one COD-RTO transaction decision.
- **Customer Correctability**: reduces false blocks for the same COD-RTO control without altering structural history.
- **Risk Canary**: prevents a worse COD-RTO model/rules release from reaching checkout.
- **Audit Terminal**: proves what the COD-RTO control did.
- **Integration Simulator**: demonstrates the same COD-RTO path from checkout to governed action.

None of these are separate fraud products. All are safety layers around the same merchant-loss class.

## Disqualification check — defense only

CodGate must fail this submission bar if any future change adds offense-capable functionality. Specifically, the project must not add:

- instructions or tooling to commit payment fraud,
- methods to bypass fraud/RTO controls,
- stolen credential or payment-instrument workflows,
- attack automation against merchants, payment systems or customers,
- abuse-ring creation/evasion tooling,
- adversarial examples intended to defeat a live risk system.

Allowed scope is defensive detection/verification, safer rollout, customer-correctable remediation, audit and bounded checkout response.

## Judge-verifiable evidence

The CI acceptance path is:

```bash
pytest -q
python -m app.score
python -m app.preflight
```

It additionally runs a real Chromium journey that clicks through the rendered console and verifies:

1. operational readiness,
2. a canonical `FORCE_PREPAID` decision in **SHADOW** mode,
3. no Payment Link in shadow,
4. structural-risk correctability proof (`145 → 97`, still blocked),
5. Canary `SHIP / SHADOW / BLOCK_RELEASE`,
6. audit/outcome chain verification,
7. the complete Integration Simulator trace,
8. desktop and mobile rendering smoke checks,
9. zero browser page/console errors.

A submission ZIP is generated only after those gates pass.

## Final Track 02 positioning

Do not pitch CodGate as “an AI that predicts fraud.” That is broader and less defensible than what the repository proves.

Pitch it as:

> **CodGate is the governance gate between RTO intelligence and checkout action. It verifies one loss class—COD RTO—with frozen precision/recall, explicit false-positive cost, safe shadow-to-enforce rollout, customer-correctable false-block analysis, and auditable release receipts.**

That statement matches the supplied Track 02 bar without inventing production evidence.
