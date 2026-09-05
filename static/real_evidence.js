(() => {
  const root = document.getElementById("risk-canary");
  if (!root || document.getElementById("real-evidence-card")) return;

  const style = document.createElement("style");
  style.textContent = `
    #real-evidence-card{margin:14px 0;border:1px solid #e0e5eb;border-left:4px solid #d92d20;background:#fff;border-radius:8px;padding:15px}
    #real-evidence-card .re-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
    #real-evidence-card .re-kicker{font:800 9px/1.3 var(--mono);text-transform:uppercase;letter-spacing:.07em;color:#667085}
    #real-evidence-card .re-title{margin:4px 0 5px;font-size:17px;letter-spacing:-.02em}
    #real-evidence-card .re-copy{margin:0;color:#667085;font-size:11px;line-height:1.55;max-width:850px}
    #real-evidence-card .re-verdict{font:800 9px/1 var(--mono);border:1px solid #f1b4ae;background:#fdecec;color:#b42318;border-radius:999px;padding:6px 9px;white-space:nowrap}
    #real-evidence-card .re-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin:12px 0}
    #real-evidence-card .re-metric{background:#f8fafc;border:1px solid #e0e5eb;border-radius:6px;padding:9px}
    #real-evidence-card .re-metric span{display:block;color:#667085;font:700 8px/1.3 var(--mono);text-transform:uppercase}
    #real-evidence-card .re-metric strong{display:block;margin-top:4px;font-size:14px}
    #real-evidence-card .re-meta{font:500 9px/1.55 var(--mono);color:#475467;white-space:pre-wrap;word-break:break-word}
    #real-evidence-card .re-warning{margin-top:9px;padding:8px 10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;color:#9a3412;font-size:10px;line-height:1.5}
    @media(max-width:900px){#real-evidence-card .re-grid{grid-template-columns:repeat(3,1fr)}}
    @media(max-width:520px){#real-evidence-card .re-grid{grid-template-columns:repeat(2,1fr)}}
  `;
  document.head.appendChild(style);

  const card = document.createElement("section");
  card.id = "real-evidence-card";
  card.innerHTML = `
    <div class="re-head">
      <div>
        <div class="re-kicker">External validation · exact-RTO public data</div>
        <h3 class="re-title">Independent Meesho RTO check</h3>
        <p class="re-copy">This is an independent domain check, not CodGate's primary benchmark. The candidate performs weakly on this small exact-RTO sample, so the evidence gate deliberately blocks release instead of hiding or relabeling the result.</p>
      </div>
      <div class="re-verdict">VERIFYING</div>
    </div>
    <div class="re-grid">
      <div class="re-metric"><span>Terminal outcomes</span><strong data-re="orders">—</strong></div>
      <div class="re-metric"><span>RTO outcomes</span><strong data-re="rto">—</strong></div>
      <div class="re-metric"><span>Precision</span><strong data-re="precision">—</strong></div>
      <div class="re-metric"><span>Recall</span><strong data-re="recall">—</strong></div>
      <div class="re-metric"><span>ROC-AUC</span><strong data-re="auc">—</strong></div>
      <div class="re-metric"><span>Confusion</span><strong data-re="confusion">—</strong></div>
    </div>
    <div class="re-meta" data-re="meta">Loading provenance…</div>
    <div class="re-warning">Why keep a failed external result? Because a risk system that only displays favorable tests is not trustworthy. CodGate treats this Meesho result as an independent exact-RTO warning while the large Amazon India return-to-seller detector remains the primary measured model.</div>`;

  const anchor = root.querySelector(".fixture-note") || root.querySelector(".scenario-strip") || root.firstElementChild;
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(card, anchor);
  else root.appendChild(card);

  const pct1 = value => `${(Number(value) * 100).toFixed(1)}%`;
  const set = (key, value) => {
    const el = card.querySelector(`[data-re="${key}"]`);
    if (el) el.textContent = value;
  };

  async function load() {
    try {
      const response = await fetch("/evidence/real-rto", {headers: {Accept: "application/json"}});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      const test = body.heldout_test || {};
      const dataset = body.dataset || {};
      const provenance = body.provenance || {};
      const verdict = card.querySelector(".re-verdict");
      verdict.textContent = String(body.verdict || "UNKNOWN").replaceAll("_", " ");
      set("orders", Number(dataset.terminal_orders || 0).toLocaleString("en-IN"));
      set("rto", Number(dataset.terminal_rto || 0).toLocaleString("en-IN"));
      set("precision", pct1(test.precision));
      set("recall", pct1(test.recall));
      set("auc", Number(test.roc_auc || 0).toFixed(3));
      set("confusion", `TP ${test.tp} · FP ${test.fp} · FN ${test.fn} · TN ${test.tn}`);
      set("meta", [
        `source ${provenance.dataset_slug || "unknown"}`,
        `source SHA ${(provenance.zip_sha256 || "missing")}`,
        `scope external_validation_only · primary_benchmark = false`,
        `receipt ${body.release_receipt || "missing"}`
      ].join("\n"));
    } catch (error) {
      card.querySelector(".re-verdict").textContent = "EVIDENCE ERROR";
      set("meta", `External validation unavailable: ${error.message}`);
    }
  }

  load();
})();
