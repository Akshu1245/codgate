(() => {
  const originalFetch = window.fetch.bind(window);
  const validModes = new Set(["enforce", "shadow"]);
  let executionMode = localStorage.getItem("codgate:execution-mode") || "enforce";
  if (!validModes.has(executionMode)) executionMode = "enforce";
  let lastDecision = null;

  const short = value => String(value || "—").slice(0, 18);
  const pct = value => value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  const money = value => `₹${Number(value || 0).toLocaleString("en-IN")}`;
  const node = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = String(text);
    return el;
  };

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : String(input?.url || "");
    if (url === "/orders/score" || url.endsWith("/orders/score")) {
      const headers = new Headers(init.headers || {});
      headers.set("X-CodGate-Mode", executionMode);
      const response = await originalFetch(input, { ...init, headers });
      if (response.ok) {
        response.clone().json().then(data => {
          lastDecision = data;
          setTimeout(() => renderReceipt(data), 0);
        }).catch(() => {});
      }
      return response;
    }
    return originalFetch(input, init);
  };

  function renderReceipt(data) {
    const card = document.getElementById("result-card");
    if (!card || !data?.receipt_id) return;
    const existing = card.querySelector(`.ops-receipt[data-receipt="${CSS.escape(data.receipt_id)}"]`);
    if (existing) return;
    card.querySelectorAll(".ops-receipt").forEach(el => el.remove());

    const block = node("div", "featuredetails ops-receipt");
    block.dataset.receipt = data.receipt_id;
    block.appendChild(node("div", "kicker", "Operational receipt"));
    const lines = node("div", "sha");
    lines.append(
      `MODE ${String(data.execution_mode || "enforce").toUpperCase()} · ACTION ${data.action || data.decision}\n`,
      `RECEIPT ${data.receipt_id}\n`,
      `AUDIT ${data.audit_entry_hash || "—"}\n`,
      `POLICY ${short(data.policy_source_sha256)}…`
    );
    block.appendChild(lines);
    if (data.idempotent_replay) {
      block.appendChild(node("div", "status allow", "Idempotent replay — no second decision audit row was written."));
    } else if (data.execution_mode === "shadow" && data.decision === "FORCE_PREPAID") {
      block.appendChild(node("div", "status force-t", "Shadow only — FORCE_PREPAID recommended; no Payment Link was issued."));
    }
    card.appendChild(block);
  }

  function installModeControl() {
    const bars = [...document.querySelectorAll(".workbar")];
    const bar = bars.find(item => item.querySelector(".route")?.textContent.includes("/orders/score"));
    if (!bar || document.getElementById("execution-mode")) return;

    const right = node("div", "auditactions");
    const route = bar.querySelector(".route");
    if (route) right.appendChild(route);
    const button = node("button", "btn quiet", executionMode.toUpperCase());
    button.id = "execution-mode";
    button.type = "button";
    button.title = "ENFORCE issues a Payment Link on FORCE_PREPAID. SHADOW records the recommendation only.";
    button.addEventListener("click", () => {
      executionMode = executionMode === "enforce" ? "shadow" : "enforce";
      localStorage.setItem("codgate:execution-mode", executionMode);
      button.textContent = executionMode.toUpperCase();
      refreshOpsStatus();
    });
    right.appendChild(button);
    bar.appendChild(right);
  }

  function installAuditIntegrity() {
    const section = document.getElementById("audit");
    const head = section?.querySelector(".sectionhead");
    if (!section || !head || document.getElementById("audit-integrity")) return;
    const line = node("div", "workbar");
    line.id = "audit-integrity";
    line.appendChild(node("div", "kicker", "Integrity check"));
    line.appendChild(node("div", "route", "checking audit chain…"));
    head.insertAdjacentElement("afterend", line);
  }

  function installPolicyManifest() {
    const section = document.getElementById("policy");
    const table = section?.querySelector(".tablewrap");
    if (!section || !table || document.getElementById("policy-manifest")) return;
    const manifest = node("dl", "docket");
    manifest.id = "policy-manifest";
    manifest.style.marginTop = "22px";
    for (const [label, value] of [
      ["Source hash", "loading…"],
      ["Held-out hash", "loading…"],
      ["Rollout", "shadow → enforce"],
      ["Change rule", "new policy version + new frozen evaluation"],
    ]) {
      manifest.appendChild(node("dt", "", label));
      const dd = node("dd", label.includes("hash") ? "mono" : "", value);
      dd.dataset.field = label;
      manifest.appendChild(dd);
    }
    table.insertAdjacentElement("afterend", manifest);
  }

  function installLiveEvidence() {
    const section = document.getElementById("metrics");
    const body = section?.querySelector(".metricbody");
    if (!section || !body || document.getElementById("live-evidence")) return;

    const block = node("div", "note-sheet");
    block.id = "live-evidence";
    block.style.marginTop = "34px";
    block.style.paddingTop = "22px";
    block.style.borderTop = "1px solid var(--border)";
    block.appendChild(node("div", "kicker", "Observed outcomes · separate ledger"));
    block.appendChild(node("h2", "", "Shadow-to-enforce evidence"));
    const note = node("p", "muted", "POST /orders/:id/outcome with DELIVERED or RTO. These observations never modify data/heldout.csv.");
    note.style.maxWidth = "760px";
    block.appendChild(note);

    const docket = node("dl", "docket");
    docket.style.marginTop = "16px";
    for (const label of ["Coverage", "Observed P / R", "Observed cost", "Prepaid paid", "Shadow decisions"]) {
      docket.appendChild(node("dt", "", label));
      const dd = node("dd", label === "Observed cost" ? "mono" : "", "—");
      dd.dataset.live = label;
      docket.appendChild(dd);
    }
    block.appendChild(docket);
    body.insertAdjacentElement("afterend", block);
  }

  async function refreshAuditIntegrity() {
    const line = document.querySelector("#audit-integrity .route");
    if (!line) return;
    try {
      const response = await originalFetch("/audit/verify");
      const data = await response.json();
      line.textContent = data.verified
        ? `VERIFIED · ${data.hashed_rows} chained · ${data.legacy_rows} legacy · tail ${short(data.tail_hash)}…`
        : `FAILED · ${data.first_error || "chain mismatch"}`;
      line.className = `route ${data.verified ? "allow" : "stop"}`;
    } catch (_) {
      line.textContent = "audit verification unavailable";
      line.className = "route stop";
    }
  }

  async function refreshLiveEvidence() {
    const block = document.getElementById("live-evidence");
    if (!block) return;
    try {
      const response = await originalFetch("/metrics/live");
      const data = await response.json();
      const fields = Object.fromEntries([...block.querySelectorAll("dd")].map(el => [el.dataset.live, el]));
      if (fields["Coverage"]) fields["Coverage"].textContent = `${data.observed_outcomes}/${data.eligible_decisions} · ${pct(data.coverage)}`;
      if (fields["Observed P / R"]) fields["Observed P / R"].textContent = `${pct(data.precision)} / ${pct(data.recall)}`;
      if (fields["Observed cost"]) fields["Observed cost"].textContent = `false-block ${money(data.false_block_inr)} · missed-RTO ${money(data.missed_rto_inr)}`;
      if (fields["Prepaid paid"]) fields["Prepaid paid"].textContent = `${data.prepaid_paid}/${data.enforced_force_prepaid} · ${pct(data.prepaid_conversion_rate)}`;
      if (fields["Shadow decisions"]) fields["Shadow decisions"].textContent = String(data.shadow_decisions || 0);
    } catch (_) {}
  }

  async function refreshOpsStatus() {
    try {
      const response = await originalFetch("/ops/status");
      const data = await response.json();
      let strip = document.getElementById("ops-status-strip");
      if (!strip) {
        const intro = document.querySelector("#gate .intro");
        strip = node("div", "workbar");
        strip.id = "ops-status-strip";
        intro?.insertAdjacentElement("afterend", strip);
      }
      if (strip) {
        strip.replaceChildren(
          node("div", "kicker", "Rollout controls"),
          node(
            "div",
            "route",
            `${executionMode.toUpperCase()} · audit ${data.audit?.verified ? "verified" : "failed"} · outcomes ${data.outcomes?.verified ? "verified" : "failed"} · ${data.payment_provider?.mode || "simulated"} · policy ${short(data.policy?.policy_source_sha256)}…`
          )
        );
      }

      const manifest = document.getElementById("policy-manifest");
      if (manifest) {
        const fields = Object.fromEntries([...manifest.querySelectorAll("dd")].map(el => [el.dataset.field, el]));
        if (fields["Source hash"]) fields["Source hash"].textContent = data.policy?.policy_source_sha256 || "—";
        if (fields["Held-out hash"]) fields["Held-out hash"].textContent = data.policy?.heldout_sha256 || "—";
      }
    } catch (_) {}
  }

  installModeControl();
  installAuditIntegrity();
  installPolicyManifest();
  installLiveEvidence();

  const card = document.getElementById("result-card");
  if (card) {
    new MutationObserver(() => {
      if (lastDecision) renderReceipt(lastDecision);
    }).observe(card, { childList: true });
  }

  document.querySelector('[data-tab="audit"]')?.addEventListener("click", () => setTimeout(refreshAuditIntegrity, 0));
  document.querySelector('[data-tab="policy"]')?.addEventListener("click", () => setTimeout(refreshOpsStatus, 0));
  document.querySelector('[data-tab="metrics"]')?.addEventListener("click", () => setTimeout(refreshLiveEvidence, 0));
  refreshOpsStatus();
  refreshAuditIntegrity();
  refreshLiveEvidence();
})();
