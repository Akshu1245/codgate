(() => {
  const page = document.getElementById("risk-canary");
  if (!page || document.getElementById("real-evidence-card")) return;

  const style = document.createElement("style");
  style.textContent = `
    #real-evidence-card{margin:18px 0;border:1px solid #cbd5e1;border-left:4px solid #b91c1c;background:#fff;border-radius:12px;padding:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
    #real-evidence-card .re-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
    #real-evidence-card .re-kicker{font-size:11px;font-weight:800;letter-spacing:.1em;color:#475569;text-transform:uppercase}
    #real-evidence-card h3{margin:4px 0 6px;font-size:19px;color:#0f172a}
    #real-evidence-card .re-copy{margin:0;max-width:850px;color:#475569;font-size:13px;line-height:1.55}
    #real-evidence-card .re-verdict{font-size:12px;font-weight:800;letter-spacing:.05em;padding:7px 10px;border-radius:999px;border:1px solid #fecaca;background:#fef2f2;color:#991b1b;white-space:nowrap}
    #real-evidence-card .re-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px;margin:15px 0}
    #real-evidence-card .re-metric{border:1px solid #e2e8f0;border-radius:9px;padding:10px;background:#f8fafc}
    #real-evidence-card .re-metric span{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#64748b;font-weight:700}
    #real-evidence-card .re-metric strong{display:block;margin-top:4px;font-size:16px;color:#0f172a}
    #real-evidence-card .re-reasons{margin:0;padding-left:18px;color:#334155;font-size:12px;line-height:1.55}
    #real-evidence-card .re-source{margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b;overflow-wrap:anywhere}
    #real-evidence-card .re-error{color:#991b1b;font-size:13px}
    .fixture-note{font-size:11px;color:#64748b;margin-top:7px;max-width:520px;line-height:1.4}
    @media(max-width:1000px){#real-evidence-card .re-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
    @media(max-width:600px){#real-evidence-card .re-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
  `;
  document.head.appendChild(style);

  const card = document.createElement("div");
  card.id = "real-evidence-card";
  card.innerHTML = `
    <div class="re-head">
      <div>
        <div class="re-kicker">Real public data · release evidence</div>
        <h3>Loading externally reproduced RTO holdout…</h3>
        <p class="re-copy">This card is generated from the frozen aggregate evidence file. Raw third-party rows are not redistributed.</p>
      </div>
      <div class="re-verdict">CHECKING</div>
    </div>`;

  const canaryOutput = document.getElementById("canary-output");
  if (canaryOutput) page.insertBefore(card, canaryOutput);
  else page.appendChild(card);

  const fixtureActions = page.querySelector(".hero .actions");
  if (fixtureActions && !fixtureActions.querySelector(".fixture-note")) {
    const note = document.createElement("div");
    note.className = "fixture-note";
    note.textContent = "Regression fixtures only — Safe / Wide / Bad are deterministic test cases, not accuracy evidence.";
    fixtureActions.appendChild(note);
  }

  const pct = value => `${(Number(value) * 100).toFixed(1)}%`;
  const esc = value => String(value ?? "").replace(/[&<>\"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[ch]));

  fetch("/evidence/real-rto", {headers: {"Accept": "application/json"}})
    .then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      return body;
    })
    .then(data => {
      const test = data.heldout_test || {};
      const dataset = data.dataset || {};
      const source = data.provenance || {};
      const ci = data.heldout_ci95_bootstrap || {};
      const precisionCi = ci.precision || [];
      const recallCi = ci.recall || [];
      const reasons = Array.isArray(data.reasons) ? data.reasons : [];
      const limitations = Array.isArray(data.limitations) ? data.limitations : [];
      const verdict = String(data.verdict || "UNKNOWN").replaceAll("_", " ");

      card.innerHTML = `
        <div class="re-head">
          <div>
            <div class="re-kicker">Real public data · release evidence</div>
            <h3>Candidate model is measured, then governed — not trusted by default.</h3>
            <p class="re-copy">Public Meesho supplier-order data described by its Kaggle page as real. Terminal labels only: DELIVERED vs RTO_COMPLETE. Chronological 60/20/20 split; threshold selected on validation only.</p>
          </div>
          <div class="re-verdict">${esc(verdict)}</div>
        </div>
        <div class="re-grid">
          <div class="re-metric"><span>Terminal outcomes</span><strong>${esc(dataset.terminal_orders)}</strong></div>
          <div class="re-metric"><span>Observed RTO</span><strong>${esc(dataset.terminal_rto)}</strong></div>
          <div class="re-metric"><span>Holdout n / RTO</span><strong>${esc(test.n)} / ${esc(test.positives)}</strong></div>
          <div class="re-metric"><span>Precision</span><strong>${pct(test.precision)}</strong></div>
          <div class="re-metric"><span>Recall</span><strong>${pct(test.recall)}</strong></div>
          <div class="re-metric"><span>ROC-AUC</span><strong>${Number(test.roc_auc).toFixed(3)}</strong></div>
        </div>
        <div class="re-copy"><strong>Confusion matrix:</strong> TP ${esc(test.tp)} · FP ${esc(test.fp)} · FN ${esc(test.fn)} · TN ${esc(test.tn)}. ${precisionCi.length === 2 ? `Precision 95% bootstrap ${pct(precisionCi[0])}–${pct(precisionCi[1])}.` : ""} ${recallCi.length === 2 ? `Recall 95% bootstrap ${pct(recallCi[0])}–${pct(recallCi[1])}.` : ""}</div>
        <ul class="re-reasons">${reasons.map(reason => `<li>${esc(reason)}</li>`).join("")}</ul>
        <div class="re-source"><strong>Source:</strong> ${esc(source.dataset_slug)} · ${esc(source.csv_member)} · source ZIP SHA-256 ${esc(source.zip_sha256)}<br><strong>Scope:</strong> ${esc(data.claim)}${limitations.length ? ` · ${esc(limitations[0])}` : ""}</div>`;
    })
    .catch(error => {
      card.innerHTML = `<div class="re-kicker">Real public data · release evidence</div><h3>Evidence unavailable</h3><p class="re-error">${esc(error.message)}</p>`;
    });
})();
