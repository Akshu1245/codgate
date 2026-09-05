"""Real-browser smoke test for the complete judge-facing Track 02 journey.

Run against a local CodGate server. This intentionally checks the rendered UI,
actual button wiring, API round-trips, real-data evidence and shadow-mode safety
rather than only HTML strings or backend unit tests.
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
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: browser_errors.append(f"console: {msg.text}")
            if msg.type == "error"
            else None,
        )

        page.goto(BASE_URL, wait_until="networkidle")
        expect(page.locator("#control-room")).to_be_visible()
        expect(page.locator("#cr-ready")).to_have_text("READY", timeout=10_000)
        expect(page.locator("#ready-label")).to_have_text("ready")

        # 1. Decision Desk — canonical policy regression case in SHADOW mode.
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

        # 2. Customer Correctability — structural policy risk cannot be edited away.
        page.locator('button[data-tab="correctability"]').click()
        page.locator("#load-structural").click()
        page.locator("#run-repair").click()
        expect(page.locator("#repair-result .headline")).to_have_text("STRUCTURAL RISK")
        expect(page.locator("#repair-result")).to_contain_text("145 → 97 pts")
        expect(page.locator("#repair-result")).to_contain_text("REPAIR RECEIPT")

        # 3. Risk Canary — real public-data evidence is primary and fails closed.
        page.locator('button[data-tab="risk-canary"]').click()
        real = page.locator("#real-evidence-card")
        expect(real).to_be_visible()
        expect(real.locator(".re-verdict")).to_have_text("BLOCK RELEASE", timeout=10_000)
        expect(real).to_contain_text("Terminal outcomes")
        expect(real).to_contain_text("138")
        expect(real).to_contain_text("28")
        expect(real).to_contain_text("23.1%")
        expect(real).to_contain_text("37.5%")
        expect(real).to_contain_text("0.431")
        expect(real).to_contain_text("TP 3 · FP 10 · FN 5 · TN 10")
        expect(real).to_contain_text("sahilr05/meesho-orders")
        expect(real).to_contain_text("bd8dc168d218c403")
        expect(page.locator(".fixture-note")).to_contain_text("Regression fixtures only")

        # Deterministic governance fixtures still prove the three paired-canary paths,
        # but are explicitly not presented as accuracy evidence.
        page.locator('button[data-canary="good"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("SHIP")
        page.locator('button[data-canary="wide"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("SHADOW")
        page.locator('button[data-canary="bad"]').click()
        expect(page.locator("#canary-output .headline")).to_have_text("BLOCK RELEASE")
        expect(page.locator("#canary-output")).to_contain_text("RELEASE RECEIPT")
        expect(page.locator("#canary-output")).to_contain_text("DATASET SHA")

        # 4. Audit Terminal — decision/outcome ledgers remain verifiable.
        page.locator('button[data-tab="audit-terminal"]').click()
        expect(page.locator("#audit-status")).to_have_text("VERIFIED", timeout=10_000)
        expect(page.locator("#outcome-status")).to_have_text("VERIFIED")

        # 5. Integration Simulator — complete checkout trace, still SHADOW-safe.
        page.locator('button[data-tab="integration-simulator"]').click()
        page.locator("#run-simulator").click()
        expect(page.locator("#sim-log")).to_contain_text("Flow complete.", timeout=10_000)
        expect(page.locator("#sim-log")).to_contain_text(
            "Shadow mode creates no Payment Link."
        )
        expect(page.locator('[data-step="4"]')).to_have_class("step done")

        page.screenshot(path=ARTIFACT_DIR / "desktop-judge-journey.png", full_page=True)

        # 6. Responsive smoke check — mobile layout and evidence remain usable.
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('button[data-tab="risk-canary"]').click()
        expect(page.locator("#real-evidence-card")).to_be_visible()
        expect(page.locator("#real-evidence-card .re-verdict")).to_have_text("BLOCK RELEASE")
        page.screenshot(path=ARTIFACT_DIR / "mobile-real-evidence.png", full_page=True)

        page.locator('button[data-tab="control-room"]').click()
        expect(page.locator("#control-room")).to_be_visible()
        expect(page.locator('button[data-tab="decision-desk"]')).to_be_visible()

        browser.close()

    if browser_errors:
        raise SystemExit("Browser console/page errors:\n" + "\n".join(browser_errors))

    print("CodGate browser judge journey · PASS")
    print("desktop + mobile smoke verified")
    print("real public-data candidate is visible and BLOCK_RELEASE")
    print("SHADOW integration flow issued no Payment Link")


if __name__ == "__main__":
    main()
