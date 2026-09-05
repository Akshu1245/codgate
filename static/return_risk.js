(() => {
  const page = document.getElementById("control-room");
  if (!page || document.getElementById("real-detector-panel")) return;

  const style = document.createElement("style");
  style.textContent = `
    #real-detector-panel{margin:14px 0 0;border:1px solid #b8c7e5;border-left:4px solid #2b6df3;background:#fff;border-radius:9px;padding:17px}
    #real-detector-panel .rd-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}
    #real-detector-panel .rd-title{margin:4px 0 6px;font-size:19px;letter-spacing:-.025em}
    #real-detector-panel .rd-copy{margin:0;color:#667085;font-size:12px;line-height:1.55;max-width:830px}
    #real-detector-panel .rd-badge{border:1px solid #b6e4cf;background:#e9f7f1;color:#147a52;border-radius:999px;padding:6px 9px;font:800 9px/1 var(--mono);text-transform:uppercase;white-space:nowrap}
    #real-detector-panel .rd-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px;margin:14px 0}
    #real-detector-panel .rd-metric{border:1px solid #e0e5eb;background:#f8fafc;border-radius:7px;padding:10px}
    #real-detector-panel .rd-metric span{display:block;color:#667085;font:700 8px/1.35 var(--mono);text-transform:uppercase;letter-spacing:.06em}
    #real-detector-panel .rd-metric strong{display:block;margin-top:5px;font-size:16px;letter-spacing:-.02em}
    #real-detector-panel .rd-two{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(300px,.95fr);gap:12px}
    #real-detector-panel .rd-box{border-top:1px solid #e0e5eb;padding-top:12px}
    #real-detector-panel .rd-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
    #real-detector-panel .rd-field label{display:block;margin-bottom:4px;color:#667085;font:700 8px/1.3 var(--mono);text-transform:uppercase}
    #real-detector-panel .rd-field input{width:100%;min-height:34px;border:1px solid #c8d0da;border-radius:5px;padding:6px 8px;font-size:11px}
    #real-detector-panel .rd-result{min-height:178px;background:#0d1525;color:#c8d6ee;border-radius:7px;padding:12px;font:500 10px/1.58 var(--mono);white-space:pre-wrap;overflow:auto}
    #real-detector-panel .rd-foot{margin-top:10px;color:#667085;font-size:10px;line-height:1.5}
    #real-detector-panel .rd-error{color:#b42318}
    @media(max-width:1050px){#real-detector-panel .rd-metrics{grid-template-columns:repeat(3,1fr)}#real-detector-panel .rd-two{grid-template-columns:1fr}}
    @media(max-width:650px){#real-detector-panel .rd-metrics{grid-template-columns:repeat(2,1fr)}#real-detector-panel .rd-fields{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  const panel = document.createElement("section");
  panel.id = "real-detector-panel";
  panel.innerHTML = `
    <div class="rd-head">
      <div>
        <div class="eyebrow">Primary real-data evidence · frozen runtime model</div>
        <h3 class="rd-title">Real return-to-seller detector</h3>
        <p class="rd-copy">The same frozen logistic model used for the sealed held-out evaluation is loaded locally by the API. It is advisory only: the learned model can flag an order for risk review, but it cannot create a Payment Link or move money.</p>
      </div>
      <div class="rd-badge" id="rd-ready">VERIFYING MODEL</div>
    </div>
    <div class="rd-metrics">
      <div class="rd-metric"><span>Real terminal orders</span><strong id="rd-orders">—</strong></div>
      <div class="rd-metric"><span>Sealed holdout / returns</span><strong id="rd-holdout">—</strong></div>
      <div class="rd-metric"><span>Precision</span><strong id="rd-precision">—</strong></div>
      <div class="rd-metric"><span>Recall</span><strong id="rd-recall">—</strong></div>
      <div class="rd-metric"><span>Precision lift</span><strong id="rd-lift">—</strong></div>
      <div class="rd-metric"><span>FP order GMV exposure</span><strong id="rd-fp-gmv">—</strong></div>
    </div>
    <div class="rd-two">
      <div class="rd-box">
        <h3>Score a real order-time feature vector</h3>
        <div class="rd-fields">
          <div class="rd-field"><label>Order date</label><input id="rr-date" value="2022-06-10" /></div>
          <div class="rd-field"><label>Fulfilment</label><input id="rr-fulfilment" value="Merchant" /></div>
          <div class="rd-field"><label>Sales channel</label><input id="rr-channel" value="Amazon.in" /></div>
          <div class="rd-field"><label>Service level</label><input id="rr-service" value="Standard" /></div>
          <div class="rd-field"><label>Category</label><input id="rr-category" value="kurta" /></div>
          <div class="rd-field"><label>Size</label><input id="rr-size" value="M" /></div>
          <div class="rd-field"><label>City</label><input id="rr-city" value="Bengaluru" /></div>
          <div class="rd-field"><label>State</label><input id="rr-state" value="Karnataka" /></div>
          <div class="rd-field"><label>Postal code</label><input id="rr-pin" value="560038" /></div>
          <div class="rd-field"><label>Amount ₹</label><input id="rr-amount" type="number" min="0" value="899" /></div>
          <div class="rd-field"><label>Quantity</label><input id="rr-quantity" type="number" min="0" value="1" /></div>
          <div class="rd-field"><label>Merchant FP cost ₹ (optional)</label><input id="rr-cost" type="number" min="0" placeholder="e.g. 250" /></div>
        </div>
        <div class="formrow"><button class="btn primary" id="rr-score">Score with frozen v2</button><span class="muted mono" id="rr-threshold"></span></div>
        <div class="rd-foot">The score is intentionally <strong>not described as a calibrated probability</strong> because the selected logistic model used balanced class weights. The frozen threshold is used for the advisory decision.</div>
      </div>
      <div class="rd-box">
        <h3>Detector output</h3>
        <div class="rd-result" id="rr-result">Run the frozen model to see the risk score, threshold, bounded action and evidence receipt.</div>
        <div class="rd-foot"><strong>Evidence hierarchy:</strong> large Amazon India return-to-seller data = primary detector; 47 exact-COD terminal orders = domain audit only; Meesho exact-RTO = independent external validation and currently fails closed.</div>
      </div>
    </div>`;

  const notice = document.getElementById("control-notice");
  if (notice && notice.parentNode) notice.parentNode.insertBefore(panel, notice.nextSibling);
  else page.appendChild(panel);

  const sidefoot = document.querySelector(".sidefoot");
  if (sidefoot) sidefoot.innerHTML = "Track 02 · AI Risk Manager<br>Primary loss: return-to-seller<br>COD RTO · governed checkout action";

  const pct = value => `${(Number(value) * 100).toFixed(2)}%`;
  const money = value => `₹${Number(value || 0).toLocaleString("en-IN", {maximumFractionDigits: 0})}`;
  const text = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };

  async function loadStatus() {
    try {
      const response = await fetch("/return-risk/status", {headers: {Accept: "application/json"}});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      const test = body.heldout_test || {};
      const dataset = body.dataset || {};
      text("rd-ready", "MODEL SHA VERIFIED");
      text("rd-orders", Number(dataset.terminal_orders || 0).toLocaleString("en-IN"));
      text("rd-holdout", `${Number(test.n || 0).toLocaleString("en-IN")} / ${Number(test.positives || 0).toLocaleString("en-IN")}`);
      text("rd-precision", pct(test.precision));
      text("rd-recall", pct(test.recall));
      text("rd-lift", `${Number(test.precision_lift_vs_prevalence || 0).toFixed(2)}×`);
      text("rd-fp-gmv", money(test.false_positive_order_gmv_at_risk_inr));
      text("rr-threshold", `threshold ${Number(body.threshold).toPrecision(5)} · score uncalibrated`);
    } catch (error) {
      const badge = document.getElementById("rd-ready");
      if (badge) {
        badge.textContent = "MODEL NOT READY";
        badge.style.color = "#b42318";
        badge.style.background = "#fdecec";
        badge.style.borderColor = "#f1b4ae";
      }
      text("rr-result", `MODEL INTEGRITY FAILURE\n${error.message}`);
    }
  }

  async function scoreOrder() {
    const button = document.getElementById("rr-score");
    const result = document.getElementById("rr-result");
    if (!button || !result) return;
    button.disabled = true;
    result.textContent = "Scoring with frozen amazon-return-risk-v2…";
    const optionalCost = document.getElementById("rr-cost").value.trim();
    const payload = {
      order_date: document.getElementById("rr-date").value,
      fulfilment: document.getElementById("rr-fulfilment").value,
      sales_channel: document.getElementById("rr-channel").value,
      service_level: document.getElementById("rr-service").value,
      style: "",
      sku: "",
      category: document.getElementById("rr-category").value,
      size: document.getElementById("rr-size").value,
      ship_city: document.getElementById("rr-city").value,
      ship_state: document.getElementById("rr-state").value,
      postal_code: document.getElementById("rr-pin").value,
      b2b: false,
      quantity: Number(document.getElementById("rr-quantity").value || 1),
      amount: Number(document.getElementById("rr-amount").value || 0),
      item_rows: 1
    };
    if (optionalCost !== "") payload.false_positive_cost_per_order_inr = Number(optionalCost);
    try {
      const response = await fetch("/return-risk/score", {
        method: "POST",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        body: JSON.stringify(payload)
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      const evidence = body.evidence || {};
      result.textContent = [
        `${body.decision} · ${body.action}`,
        `risk_score ${Number(body.risk_score).toFixed(6)} · threshold ${Number(body.threshold).toFixed(6)}`,
        `calibrated_probability = ${body.score_is_calibrated_probability}`,
        `execution = ${body.execution}`,
        `heldout precision ${pct(evidence.heldout_precision)} · recall ${pct(evidence.heldout_recall)} · lift ${Number(evidence.precision_lift || 0).toFixed(2)}×`,
        optionalCost === "" ? "false-positive ₹ cost = not supplied by merchant" : `merchant FP unit cost ₹${Number(optionalCost).toLocaleString("en-IN")} · heldout modeled FP cost ${money(body.heldout_modeled_false_positive_cost_inr)}`,
        `model SHA ${body.runtime_model_sha256}`,
        `source SHA ${body.source_zip_sha256}`,
        "No payment action executed."
      ].join("\n");
    } catch (error) {
      result.textContent = `SCORING FAILED\n${error.message}`;
      result.classList.add("rd-error");
    } finally {
      button.disabled = false;
    }
  }

  document.getElementById("rr-score")?.addEventListener("click", scoreOrder);
  loadStatus();
})();
