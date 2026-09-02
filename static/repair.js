(() => {
  const priorFetch = window.fetch.bind(window);
  let lastRepair = null;

  const node = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = String(text);
    return el;
  };

  const tone = status => {
    if (status === "REPAIRABLE" || status === "ALREADY_SAFE") return "allow";
    if (status === "NEEDS_CORRECTION") return "stop";
    return "force-t";
  };

  function renderRepair(repair) {
    const card = document.getElementById("result-card");
    if (!card || !repair?.repair_receipt) return;

    card.querySelectorAll(".risk-repair").forEach(el => el.remove());

    const block = node("div", "featuredetails risk-repair");
    block.dataset.repair = repair.repair_receipt;
    block.style.marginTop = "16px";
    block.style.paddingTop = "14px";
    block.style.borderTop = "1px solid var(--border)";

    block.appendChild(node("div", "kicker", "Counterfactual Risk Repair"));

    const headline = node("div", `status ${tone(repair.status)}`);
    headline.style.marginTop = "7px";
    headline.style.fontSize = "12px";
    headline.textContent = repair.status === "REPAIRABLE"
      ? `REPAIRABLE · ${repair.base_points} → ${repair.best_points} pts · COD can be restored`
      : repair.status === "STRUCTURAL_RISK"
        ? `NO SAFE REPAIR · best legitimate score ${repair.best_points} pts`
        : repair.status === "ALREADY_SAFE"
          ? `ALREADY SAFE · ${repair.base_points} pts`
          : `CORRECTION REQUIRED · ${(repair.required_fields || []).join(", ") || "input"}`;
    block.appendChild(headline);

    if (repair.customer_action) {
      const action = node("p", "note", repair.customer_action);
      action.style.color = "var(--fg)";
      block.appendChild(action);
    }

    const proof = node("p", "note", repair.proof || "");
    proof.style.marginTop = "7px";
    block.appendChild(proof);

    if (repair.criterion) {
      block.appendChild(node("div", "sha", `CRITERION ${repair.criterion}`));
    }

    const locked = Array.isArray(repair.locked_signals) ? repair.locked_signals.join(" · ") : "";
    if (locked) {
      const guard = node("div", "sha", `LOCKED ${locked}`);
      guard.title = "Risk Repair is not an override: historical and structural signals are never edited merely to cross the threshold.";
      block.appendChild(guard);
    }

    block.appendChild(node("div", "sha", `REPAIR RECEIPT ${repair.repair_receipt}`));
    card.appendChild(block);
  }

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : String(input?.url || "");
    const response = await priorFetch(input, init);
    if ((url === "/orders/score" || url.endsWith("/orders/score")) && response.ok) {
      response.clone().json().then(data => {
        if (data?.risk_repair) {
          lastRepair = data.risk_repair;
          setTimeout(() => renderRepair(lastRepair), 0);
        }
      }).catch(() => {});
    }
    return response;
  };

  const card = document.getElementById("result-card");
  if (card) {
    new MutationObserver(() => {
      if (lastRepair) renderRepair(lastRepair);
    }).observe(card, { childList: true });
  }
})();
