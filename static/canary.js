(() => {
  const policy = document.querySelector('#policy');
  if (!policy || document.querySelector('#release-canary')) return;

  const style = document.createElement('style');
  style.textContent = `
    .canary-wrap{margin-top:34px;border-top:1px solid var(--border);padding-top:22px}
    .canary-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:22px;align-items:end;padding-bottom:14px;border-bottom:1px solid var(--border)}
    .canary-head h3{font:400 27px/1.05 "Instrument Serif",Georgia,serif;letter-spacing:-.02em;margin:3px 0 0}
    .canary-head p{margin:7px 0 0;max-width:720px;color:var(--muted);font-size:12px}
    .canary-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
    .canary-btn{min-height:44px;border:1px solid var(--border);border-radius:8px;background:transparent;color:var(--fg);padding:0 12px;font:500 10px/1 "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase}
    .canary-btn:hover{border-color:var(--muted)}
    .canary-result{margin-top:18px;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
    .canary-verdict{display:grid;grid-template-columns:180px minmax(0,1fr);gap:20px;padding:18px 0;border-bottom:1px solid var(--border)}
    .canary-stamp{font:400 30px/1 "Instrument Serif",Georgia,serif;letter-spacing:-.02em}
    .canary-stamp.ship{color:var(--allow)}.canary-stamp.shadow{color:var(--force)}.canary-stamp.block{color:var(--stop)}
    .canary-reason{font-size:12px;color:var(--muted)}
    .canary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}
    .canary-cell{padding:14px 14px 15px 0;min-width:0}.canary-cell+.canary-cell{border-left:1px solid var(--border);padding-left:14px}
    .canary-cell b{display:block;font:400 26px/1 "Instrument Serif",Georgia,serif;margin-top:6px}
    .canary-cell small{display:block;font:400 9px/1.45 "IBM Plex Mono",monospace;color:var(--muted);margin-top:5px}
    .canary-segments{width:100%;border-collapse:collapse;margin-top:18px}.canary-segments th,.canary-segments td{padding:9px 8px;border-bottom:1px solid var(--border);text-align:left}.canary-segments th{font:500 9px/1.3 "IBM Plex Mono",monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}.canary-segments td{font:400 10px/1.4 "IBM Plex Mono",monospace}
    .canary-foot{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,.6fr);gap:24px;padding-top:16px}.canary-foot p{margin:0;color:var(--muted);font-size:11px}.canary-receipt{font:400 10px/1.55 "IBM Plex Mono",monospace;color:var(--muted);word-break:break-all}
    @media(max-width:760px){.canary-head,.canary-verdict,.canary-foot{grid-template-columns:1fr}.canary-actions{justify-content:flex-start}.canary-grid{grid-template-columns:repeat(2,1fr)}.canary-cell:nth-child(3){border-left:0;border-top:1px solid var(--border);padding-left:0}.canary-cell:nth-child(4){border-top:1px solid var(--border)}.canary-segments{min-width:620px}.canary-table{overflow:auto}}
  `;
  document.head.appendChild(style);

  const shell = document.createElement('section');
  shell.id = 'release-canary';
  shell.className = 'canary-wrap';
  shell.innerHTML = `
    <div class="canary-head">
      <div>
        <div class="kicker">Razorpay internal · Risk Policy Release Gate</div>
        <h3>Canary before production</h3>
        <p>Replay a candidate Magic Checkout RTO release against Razorpay-owned outcomes. Canary does not score transactions; it decides whether the candidate is safe to ship.</p>
      </div>
      <div class="canary-actions">
        <button class="canary-btn" data-canary="good">Safe candidate</button>
        <button class="canary-btn" data-canary="wide">Wide change</button>
        <button class="canary-btn" data-canary="bad">Bad release</button>
      </div>
    </div>
    <div id="canary-output" class="canary-result" hidden></div>
  `;
  policy.appendChild(shell);

  const output = shell.querySelector('#canary-output');
  const money = value => `₹${Math.abs(Number(value || 0)).toLocaleString('en-IN')}`;
  const pct = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
  const signedMoney = value => `${Number(value) > 0 ? '+' : Number(value) < 0 ? '−' : ''}${money(value)}`;
  const fmtMetric = value => value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;

  function cell(label, value, note) {
    return `<div class="canary-cell"><div class="kicker">${label}</div><b>${value}</b><small>${note}</small></div>`;
  }

  function render(data) {
    const cls = data.verdict === 'SHIP' ? 'ship' : data.verdict === 'SHADOW' ? 'shadow' : 'block';
    const segmentRows = (data.segments || []).map(row => `
      <tr>
        <td>${row.segment}</td>
        <td>${row.rows}</td>
        <td>${row.changed}</td>
        <td>${pct(row.change_rate)}</td>
        <td>${pct(row.current_false_block_rate)}</td>
        <td>${pct(row.candidate_false_block_rate)}</td>
        <td>${row.false_block_rate_delta >= 0 ? '+' : ''}${(row.false_block_rate_delta * 100).toFixed(1)} pp</td>
      </tr>`).join('');

    output.innerHTML = `
      <div class="canary-verdict">
        <div>
          <div class="kicker">Release verdict</div>
          <div class="canary-stamp ${cls}">${data.verdict.replace('_', ' ')}</div>
        </div>
        <div class="canary-reason">${(data.reasons || []).join(' · ')}</div>
      </div>
      <div class="canary-grid">
        ${cell('Modeled loss · current', money(data.current.modeled_loss_inr), `FP ${data.current.fp} · FN ${data.current.fn}`)}
        ${cell('Modeled loss · candidate', money(data.candidate.modeled_loss_inr), `FP ${data.candidate.fp} · FN ${data.candidate.fn}`)}
        ${cell('₹ delta', signedMoney(data.delta.modeled_loss_inr), 'negative is better')}
        ${cell('Blast radius', pct(data.blast_radius), `${data.changed_decisions} / ${data.rows} decisions change`)}
      </div>
      <div class="canary-table">
        <table class="canary-segments">
          <thead><tr><th>Merchant segment</th><th>Rows</th><th>Changed</th><th>Change rate</th><th>FP current</th><th>FP candidate</th><th>FP delta</th></tr></thead>
          <tbody>${segmentRows}</tbody>
        </table>
      </div>
      <div class="canary-foot">
        <p><strong>Why Razorpay benefits directly:</strong> a bad risk release can affect every merchant already on Razorpay. Canary blocks the release before rollout; no new e-commerce integration is required. Production input would be Razorpay's own current/candidate shadow decisions plus observed RTO outcomes.</p>
        <div class="canary-receipt"><span class="kicker">Release receipt</span><br>${data.release_receipt}<br>current P ${fmtMetric(data.current.precision)} / R ${fmtMetric(data.current.recall)}<br>candidate P ${fmtMetric(data.candidate.precision)} / R ${fmtMetric(data.candidate.recall)}</div>
      </div>
    `;
    output.hidden = false;
  }

  shell.querySelectorAll('[data-canary]').forEach(button => {
    button.addEventListener('click', async () => {
      const scenario = button.dataset.canary;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Checking…';
      try {
        const response = await fetch(`/release/demo/${scenario}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        render(await response.json());
      } catch (error) {
        output.hidden = false;
        output.textContent = `Release check failed: ${error.message}`;
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    });
  });
})();
