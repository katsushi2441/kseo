// Kurage SEO の画面。
// ローカルでは同一オリジンの /api/* を直接叩き、公開時は heteml の kseo.php
// (?api=<path>) を経由する。誰がログインしているかはこのファイルは知らない
// ——X認証はゲートウェイだけが持つ(認証を2か所に置かないため)。
// ここが知るのは、状態を変える操作にCSRFトークンを添えることだけ。

const $ = (id) => document.getElementById(id);
const state = { sites: [], busy: false };

const SEVERITY_LABEL = { critical: "重大", warning: "警告", info: "改善余地" };

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// 公開時は kseo.php が window.KSEO を埋め込む。無ければローカル直叩き。
// 公開URLに内部ポートを出さないため、経路の違いはここ1か所に閉じる。
const GATEWAY = (typeof window !== "undefined" && window.KSEO) || null;

async function api(path, options = {}) {
  const url = GATEWAY ? GATEWAY.base + encodeURIComponent(path) : path;
  const headers = { "Content-Type": "application/json" };
  // 状態を変える操作にはCSRFトークンが要る。付け忘れると本番で403になる。
  if (GATEWAY && options.method && options.method !== "GET") {
    headers["X-CSRF-Token"] = GATEWAY.csrf;
  }
  const response = await fetch(url, { ...options, headers });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = null; }
  if (!response.ok) {
    throw new Error((body && body.detail) || `エラー (${response.status})`);
  }
  return body;
}

function scoreClass(score) {
  if (score >= 90) return "good";
  if (score >= 70) return "mid";
  return "bad";
}

function verdict(score) {
  if (score >= 90) return "大きな問題は見つかりませんでした。";
  if (score >= 70) return "検索結果での見え方に直接効く改善点が残っています。";
  if (score >= 50) return "検索評価を損なう問題が複数あります。上から順に対処してください。";
  return "検索結果に出ない、または大きく損をしている状態です。";
}

async function loadUsage() {
  try {
    const usage = await api("/api/usage");
    const audits = usage.audits_limit == null
      ? `監査 ${usage.audits_used}回（無制限）`
      : `今月の監査 ${usage.audits_used}/${usage.audits_limit}回`;
    $("usage").textContent = `${usage.owner} · ${audits}`;
  } catch (error) {
    $("usage").textContent = "";
  }
}

async function loadSites() {
  const list = $("siteList");
  try {
    state.sites = await api("/api/sites");
  } catch (error) {
    list.innerHTML = `<p class="note error">${escapeHtml(error.message)}</p>`;
    return;
  }
  if (!state.sites.length) {
    list.innerHTML = '<p class="muted">まだ登録がありません。上のフォームから追加してください。</p>';
    return;
  }
  list.innerHTML = state.sites.map((site) => `
    <div class="site-card">
      <div class="meta">
        <strong>${escapeHtml(site.name)}</strong>
        <a href="${escapeHtml(site.url)}" target="_blank" rel="noopener">${escapeHtml(site.url)}</a>
      </div>
      <div class="score-badge ${site.latest_score == null ? "" : scoreClass(site.latest_score)}">
        ${site.latest_score == null ? "—" : site.latest_score}
      </div>
      <div class="actions">
        <button class="primary" data-audit="${escapeHtml(site.id)}">診断する</button>
        ${site.latest_audit_id ? `<button data-show="${escapeHtml(site.latest_audit_id)}">前回の結果</button>` : ""}
        <button data-delete="${escapeHtml(site.id)}">削除</button>
      </div>
    </div>
  `).join("");
}

function renderAudit(audit) {
  const panel = $("resultPanel");
  panel.hidden = false;
  const cats = Object.values(audit.categories || {}).map((cat) => `
    <div class="cat">
      <div class="label">${escapeHtml(cat.label)}</div>
      <div class="value">${cat.score}点</div>
      <div class="bar"><span style="width:${Math.max(0, Math.min(100, cat.score))}%"></span></div>
    </div>
  `).join("");

  const findings = (audit.findings || []).map((finding) => `
    <div class="finding ${escapeHtml(finding.severity)}">
      <span class="sev">${escapeHtml(SEVERITY_LABEL[finding.severity] || finding.severity)}</span>
      <div class="msg">${escapeHtml(finding.message)}</div>
      <div class="ev"><b>実測:</b> ${escapeHtml(finding.evidence)}</div>
      <div class="act"><b>対応:</b> ${escapeHtml(finding.action)}</div>
      <div class="url">${escapeHtml(finding.url)}</div>
    </div>
  `).join("") || '<p class="muted">指摘はありません。</p>';

  const counts = audit.counts || {};
  $("result").innerHTML = `
    <div class="summary">
      <div class="score-badge ${scoreClass(audit.overall)}">${audit.overall}</div>
      <div>
        <div class="verdict">${escapeHtml(verdict(audit.overall))}</div>
        <div class="muted">
          ${audit.page_count}ページを監査 ·
          重大 ${counts.critical || 0} / 警告 ${counts.warning || 0} / 改善余地 ${counts.info || 0}
        </div>
      </div>
    </div>
    <div class="cats">${cats}</div>
    <h3>指摘</h3>
    ${findings}
    <div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
      <button class="primary" data-advice="${escapeHtml(audit.id)}">改善案をAIに書かせる</button>
      <button data-report="${escapeHtml(audit.id)}">診断書をダウンロード</button>
    </div>
    <div id="adviceBox">${audit.advice ? `<div class="advice"><h3>改善案</h3>${escapeHtml(audit.advice)}</div>` : ""}</div>
  `;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runAudit(siteId, button) {
  if (state.busy) return;
  state.busy = true;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "診断中…";
  try {
    const audit = await api(`/api/sites/${siteId}/audits`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    renderAudit(audit);
    await Promise.all([loadSites(), loadUsage()]);
  } catch (error) {
    $("formNote").textContent = error.message;
    $("formNote").className = "note error";
  } finally {
    state.busy = false;
    button.disabled = false;
    button.textContent = original;
  }
}

async function showAudit(auditId) {
  try {
    renderAudit(await api(`/api/audits/${auditId}`));
  } catch (error) {
    $("formNote").textContent = error.message;
    $("formNote").className = "note error";
  }
}

async function makeAdvice(auditId, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "生成中…（1分ほどかかります）";
  try {
    const advice = await api(`/api/audits/${auditId}/advice`, { method: "POST" });
    $("adviceBox").innerHTML =
      `<div class="advice"><h3>改善案</h3>${escapeHtml(advice.body)}</div>`;
    await loadUsage();
  } catch (error) {
    $("adviceBox").innerHTML = `<p class="note error">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.audit) runAudit(target.dataset.audit, target);
  else if (target.dataset.show) showAudit(target.dataset.show);
  else if (target.dataset.advice) makeAdvice(target.dataset.advice, target);
  else if (target.dataset.report) {
    const path = `/api/audits/${target.dataset.report}/report.md`;
    window.open(GATEWAY ? GATEWAY.base + encodeURIComponent(path) : path, "_blank");
  }
  else if (target.dataset.delete) {
    if (!confirm("このサイトと診断履歴を削除します。よろしいですか？")) return;
    api(`/api/sites/${target.dataset.delete}`, { method: "DELETE" }).then(loadSites);
  }
});

$("siteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const note = $("formNote");
  note.textContent = "";
  note.className = "note";
  try {
    await api("/api/sites", {
      method: "POST",
      body: JSON.stringify({ name: $("siteName").value, url: $("siteUrl").value }),
    });
    $("siteName").value = "";
    $("siteUrl").value = "";
    note.textContent = "登録しました。";
    await Promise.all([loadSites(), loadUsage()]);
  } catch (error) {
    note.textContent = error.message;
    note.className = "note error";
  }
});

loadUsage();
loadSites();
