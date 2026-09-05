"""Real-browser judge journey for the Track 02 submission.

This checks rendered evidence, the exact frozen learned model, bounded execution,
customer-correctability, release governance, audit integrity and mobile layout.
Browser console/page errors are failures.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.getenv("CODGATE_BASE_URL", "http://127.0.0.1:8000")
ARTIFACT_DIR = Path(os.getenv("CODGATE_BROWSER_ARTIFACT_DIR", "browser-artifacts"))


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    browser_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="networkidle")
        expect(page.locator("#control-room")).to_be_visible()
        expect(page.locator("#cr-ready")).to_have_text("READY", timeout=10_000)
        expect(page.locator("#ready-label")).to_have_text("ready")

        # 1 — primary real detector must be visible, hash-verified and measurable.
        detector = page.locator("#real-detector-panel")
        expect(detector).to_be_visible()
        expect(page.locator("#rd-ready")).to_have_text("MODEL SHA VERIFIED", timeout=10_000)
        expect(page.locator("#rd-orders")).to_have_text("28,417")
        expect(page.locator("#rd-holdout")).to_have_text("5,726 / 362")
        expect(page.locator("#rd-precision")).to_have_text("11.21%")
        expect(page.locator("#rd-recall")).to_have_text("23.20%")
        expect(page.locator("#rd-lift")).to_have_text("1.77×")
        expect(page.locator("#rd-fp-gmv")).to_have_text("₹4,43,627")

        page.locator("#rr-cost").fill("250")
        page.locator("#rr-score").click()
        score = page.locator("#rr-result")
        expect(score).to_contain_text("execution = advisory_only", timeout=10_000)
        expect(score).to_contain_text("calibrated_probability = false")
        expect(score).to_contain_text("heldout precision 11.21% · recall 23.20% · lift 1.77×")
        expect(score).to_contain_text("heldout modeled FP cost ₹1,66,250")
        expect(score).to_contain_text("model SHA ced7e510515cc54ab874f598c4999c6c407d76fce36dccc007d114f128ccd754")
        expect(score).to_contain_text("source SHA 2d174af66d3390f6bdd157fec4e29e076e3454ed6935f124510ccc66f85c459a")
        expect(score).to_contain_text("No payment action executed.")

        # 2 — deterministic policy remains the bounded checkout control layer.
        page.locator('button[data-tab="decision-desk"]').click()
        expect(page.locator("#decision-desk")).to_be_visible()
        page.locator('button[data-case="force"]').click()
        page.locator("#execution_mode").select_option("shadow")
        page.locator("#score-order").click()
        expect(page.locator("#decision-result .big")).to_have_text("FORCE PREPAID")
        expect(page.locator("#decision-result")).to_contain_text("SHADOW · OBSERVE_ONLY")
        expect(page.locator("#decision-result")).not_to_contain_text("PAYMENT LINK")
        expect(page.locator("#decision-result")).to_contain_text("DECISION RECEIPT")
        expect(page.locator("#decision-result")).to_contain_text("AUDIT HASH")

        # 3 — customer-correctability shows bounded remediation instead of blind blocking.
        page.locator('button[data-tab="correctability"]').click()
        page.locator("#load-structural").click()
        page.locator("#run-repair").click()
        expect(page.locator("#repair-result .headline")).to_have_text("STRUCTURAL RISK")
        expect(page.locator("#repair-result")).to_contain_text("145 → 97 pts")
        expect(page.locator("#repair-result")).to_contain_text("REPAIR RECEIPT")

        # 4 — independent exact-RTO validation remains visible and honestly fails closed.
        page.locator('button[data-tab="risk-canary"]').click()
        external = page.locator("#real-evidence-card")
        expect(external).to_be_visible()
        expect(external.locator(".re-verdict")).to_have_text("BLOCK RELEASE", timeout=10_000)
        expect(external).to_contain_text("External validation")
        expect(external).to_contain_text("138")
        expect(external).to_contain_text("23.1%")
        expect(external).to_contain_text("37.5%")
        expect(external).to_contain_text("TP 3 · FP 10 · FN 5 · TN 10")
        expect(external).to_contain_text("sahilr05/meesho-orders")

        # Paired release fixtures exercise the release-governance code paths only.
        page.locator('button[data-canary="good"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("SHIP")
        page.locator('button[data-canary="wide"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("SHADOW")
        page.locator('button[data-canary="bad"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("BLOCK RELEASE")
        expect(page.locator("#canary-output")).to_contain_text("RELEASE RECEIPT")
        expect(page.locator("#canary-output")).to_contain_text("DATASET SHA")

        # 5 — audit chains are verifiable.
        page.locator('button[data-tab="audit-terminal"]').click()
        expect(page.locator("#audit-status")).to_have_text("VERIFIED", timeout=10_000)
        expect(page.locator("#outcome-status")).to_have_text("VERIFIED")

        # 6 — integration trace remains explicitly SHADOW-safe.
        page.locator('button[data-tab="integration-simulator"]').click()
        page.locator("#run-simulator").click()
        expect(page.locator("#sim-log")).to_contain_text("Flow complete.", timeout=10_000)
        expect(page.locator("#sim-log")).to_contain_text("Shadow mode creates no Payment Link.")
        expect(page.locator('[data-step="4"]')).to_have_class("step done")

        page.screenshot(path=ARTIFACT_DIR / "desktop-judge-journey.png", full_page=True)

        # 7 — mobile judge view keeps the primary evidence usable.
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('button[data-tab="control-room"]').click()
        expect(page.locator("#real-detector-panel")).to_be_visible()
        expect(page.locator("#rd-ready")).to_have_text("MODEL SHA VERIFIED")
        expect(page.locator("#rd-precision")).to_have_text("11.21%")
        page.screenshot(path=ARTIFACT_DIR / "mobile-control-room-real-detector.png", full_page=True)

        browser.close()

    if browser_errors:
        raise SystemExit("Browser console/page errors:\n" + "\n".join(browser_errors))

    print("CodGate browser judge journey · PASS")
    print("frozen real detector · rendered and scored")
    print("external exact-RTO validation · visible and fail-closed")
    print("desktop + mobile smoke · PASS")
    print("SHADOW integration flow · no Payment Link")


if __name__ == "__main__":
    main()
