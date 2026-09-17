/**
 * Skill 管理页：共享库（上传）→ 安装到 Agent → 一键卸载。
 *
 * 三条界面纪律（都有理由）：
 *  1. **"卸载"与"删除"必须视觉可分**：卸载只是不生效、库里还在；删除才销毁。
 *     两者若长得一样，用户会以为"卸载=删干净了"，然后在库里发现它还在，进而怀疑功能坏了。
 *  2. **高危扫描结果必须整条列出来给人看**，不能只给个"有风险"的红点——
 *     扫描的定位是"把要人看一眼的地方摊开"，不是自动判定。
 *  3. **核心工具与安装的能力分开两张表**：核心不能卸载，混在一起会让人以为能删。
 */

import { api, el } from './modules-common.js';

const $ = el;
let pendingUpload = null; // {file, findings} —— 高危包需二次确认

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t ?? '';
  return d.innerHTML;
}

function fmtTime(ms) {
  if (!ms) return '—';
  const d = new Date(Number(ms));
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

const ACTION_LABEL = {
  upload: '上传', upload_rejected: '上传被拒', upload_needs_confirm: '上传待确认',
  install: '安装', uninstall: '卸载', delete_from_library: '从库删除',
};

async function loadAll() {
  const badge = $('sk-health');
  try {
    const s = await api('/api/skills/summary');
    $('sk-boundary').textContent = s.boundary || '';
    $('sk-counts').innerHTML = [
      ['共享库', s.counts.library],
      ['已安装', s.counts.installed],
      ['核心工具 / 已装工具', `${s.counts.core_tools} / ${s.counts.installed_tools}`],
    ].map(([k, v]) => `<div class="mfield"><span class="hint">${esc(k)}</span>
        <div class="mono" style="font-size:18px">${esc(String(v))}</div></div>`).join('');
    $('sk-counts').insertAdjacentHTML('afterend', '');
    badge.textContent = `库 ${s.counts.library} · 装 ${s.counts.installed}`;
    badge.className = 'status-badge ok';
    renderLibrary(s.library);
    renderInstalled(s.installed);
    await Promise.all([loadCore(), loadAudit()]);
  } catch (err) {
    badge.textContent = '不可用';
    badge.className = 'status-badge err';
    badge.title = String(err.message || err);
  }
}

function renderLibrary(rows) {
  const tbody = document.querySelector('#sk-library tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="hint">共享库是空的——先在上面上传一个 Skill ZIP</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td><b>${esc(r.name)}</b><br /><span class="hint mono">${esc(r.capability_id)}</span>
        ${r.has_scan_findings ? '<br /><span class="status-badge warn">有扫描发现</span>' : ''}
        ${(r.script_tools || []).length
          ? `<br /><span class="status-badge warn" title="这些工具会执行包内 Python 脚本（在沙箱里；风险不低于 write，每次调用需确认）">含脚本 ${r.script_tools.length}</span>`
          : ''}</td>
      <td class="mono">${esc(r.latest_version)}${r.versions.length > 1 ? `<br /><span class="hint">共 ${r.versions.length} 版</span>` : ''}</td>
      <td class="mono hint">${r.tools.map((t) => (r.script_tools || []).includes(t)
        ? `${esc(t)} <span class="hint">(脚本)</span>` : esc(t)).join('<br />') || '—'}</td>
      <td class="hint">${(r.capabilities || []).map(esc).join('、') || '—'}</td>
      <td>${r.installed
        ? `<span class="status-badge ok">已安装 ${esc(r.installed_version)}</span>`
        : '<span class="hint">未安装</span>'}</td>
      <td>
        <button class="mbtn" data-install="${esc(r.capability_id)}">${r.installed ? '重新安装' : '安装到 Agent'}</button>
        <button class="mbtn ghost" data-detail="${esc(r.capability_id)}" data-version="${esc(r.latest_version)}">查看</button>
        ${r.installed ? `<button class="mbtn ghost" data-uninstall="${esc(r.capability_id)}">卸载</button>` : ''}
        <button class="mbtn ghost" data-delete="${esc(r.capability_id)}">从库删除</button>
      </td>
    </tr>`).join('');
}

function renderInstalled(rows) {
  const tbody = document.querySelector('#sk-installed tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="hint">还没有安装任何 Skill。装好后它的工具会立刻对 Agent 可见</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td><b>${esc(r.title)}</b><br /><span class="hint mono">${esc(r.capability_id)}</span></td>
      <td class="mono">${esc(r.installed_version || r.version)}</td>
      <td class="mono hint">${r.tools.map(esc).join('<br />') || '—'}</td>
      <td>${r.enabled ? '<span class="status-badge ok">生效中</span>'
                      : '<span class="status-badge warn">已禁用</span>'}</td>
      <td>
        <button class="mbtn" data-uninstall="${esc(r.capability_id)}">一键卸载</button>
        <button class="mbtn ghost" data-toggle="${esc(r.plugin_id)}" data-enabled="${r.enabled}">
          ${r.enabled ? '禁用' : '启用'}
        </button>
      </td>
    </tr>`).join('');
}

async function loadCore() {
  const tbody = document.querySelector('#sk-core tbody');
  try {
    const cat = await api('/api/agent/tools');
    const core = (cat.plugins || []).filter((p) => p.origin === 'core');
    tbody.innerHTML = core.map((p) => `
      <tr>
        <td><b>${esc(p.title)}</b><br /><span class="hint mono">${esc(p.id)}</span></td>
        <td>${p.tools.length}</td>
        <td>${p.enabled ? '<span class="status-badge ok">启用</span>'
                        : '<span class="status-badge warn">已禁用</span>'}</td>
        <td>
          <button class="mbtn ghost" data-toggle="${esc(p.id)}" data-enabled="${p.enabled}">
            ${p.enabled ? '禁用' : '启用'}
          </button>
          <span class="hint">（核心插件不可卸载）</span>
        </td>
      </tr>`).join('') || '<tr><td colspan="4" class="hint">没有核心插件</td></tr>';
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" class="hint" style="color:#f85149">加载失败：${esc(String(err.message || err))}</td></tr>`;
  }
}

async function loadAudit() {
  const tbody = document.querySelector('#sk-audit tbody');
  try {
    const { audit } = await api('/api/skills/audit?limit=50');
    if (!audit.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="hint">还没有操作记录</td></tr>';
      return;
    }
    tbody.innerHTML = audit.map((a) => `
      <tr>
        <td>${fmtTime(a.created_at)}</td>
        <td>${esc(ACTION_LABEL[a.action] || a.action)}</td>
        <td class="mono">${esc(a.capability_id) || '—'}</td>
        <td class="mono">${esc(a.version) || '—'}</td>
        <td class="hint">${esc(a.verdict) || (a.high_findings ? `高危 ${a.high_findings} 处` : '—')}</td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="hint" style="color:#f85149">加载失败：${esc(String(err.message || err))}</td></tr>`;
  }
}

function showScan(findings, verdict) {
  const box = $('sk-scan');
  if (!findings || !findings.length) {
    box.innerHTML = verdict === 'ok' ? '<p class="hint" style="color:var(--acc-success)">扫描未发现可疑模式。</p>' : '';
    return;
  }
  const rows = findings.map((f) => `
    <tr>
      <td><span class="status-badge ${f.severity === 'high' ? 'err' : 'warn'}">${esc(f.severity)}</span></td>
      <td>${esc(f.kind)}</td>
      <td class="mono hint">${esc(f.file)}:${f.line}</td>
      <td class="mono hint" style="word-break:break-all">${esc(f.excerpt)}</td>
    </tr>`).join('');
  box.innerHTML = `
    <p class="hint" style="color:#f0b429">
      扫描发现 ${findings.length} 处可疑模式。<b>扫描是准入闸门，不是安全边界</b>——
      它只把要看的地方摊开，不判定恶意。真正的强制在沙箱层。
    </p>
    <table class="mtable"><thead><tr><th>级别</th><th>类型</th><th>位置</th><th>片段</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div style="margin-top:10px">
      <button class="mbtn" id="btn-sk-confirm">我已看过，仍要收入共享库</button>
      <button class="mbtn ghost" id="btn-sk-cancel">取消</button>
    </div>`;
  $('btn-sk-confirm').onclick = () => doUpload(true);
  $('btn-sk-cancel').onclick = () => { pendingUpload = null; box.innerHTML = ''; };
}

async function doUpload(allowWarn) {
  const input = $('sk-file');
  const file = pendingUpload?.file || input.files?.[0];
  if (!file) {
    $('sk-upload-result').textContent = '先选一个 .zip 文件';
    return;
  }
  const form = new FormData();
  form.append('file', file);
  $('sk-upload-result').textContent = '上传中…';
  try {
    const res = await fetch(`/api/skills/upload?allow_warn=${allowWarn ? 'true' : 'false'}`, {
      method: 'POST', body: form,
    });
    const data = await res.json();
    const payload = res.ok ? data : (data.detail || {});
    if (res.ok) {
      pendingUpload = null;
      $('sk-upload-result').textContent = `✔ 已入库：${payload.capability_id}@${payload.version}`;
      showScan([], payload.verdict);
      input.value = '';
    } else {
      $('sk-upload-result').textContent = `✗ ${payload.reason || '上传失败'}`;
      if (payload.verdict === 'warn' && payload.findings?.length) {
        pendingUpload = { file };
        showScan(payload.findings, 'warn');
      } else {
        showScan([], 'reject');
      }
    }
  } catch (err) {
    $('sk-upload-result').textContent = `✗ 上传失败：${err.message || err}`;
  }
  await loadAll();
}

// ── 事件委托：表格是动态重建的，逐个绑定会随重渲染失效 ──
document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('button');
  if (!t) return;
  try {
    if (t.dataset.install) {
      t.disabled = true;
      await api('/api/skills/install', { method: 'POST', body: { capability_id: t.dataset.install } });
    } else if (t.dataset.uninstall) {
      t.disabled = true;
      await api('/api/skills/uninstall', { method: 'POST', body: { capability_id: t.dataset.uninstall } });
    } else if (t.dataset.delete) {
      if (!confirm(`从共享库彻底删除「${t.dataset.delete}」？\n\n这会删掉它的所有版本；若已安装会先卸载。\n此操作不可撤销。`)) return;
      t.disabled = true;
      await api('/api/skills/delete', { method: 'POST', body: { capability_id: t.dataset.delete } });
    } else if (t.dataset.toggle) {
      const next = t.dataset.enabled !== 'true';
      t.disabled = true;
      await api(`/api/skills/plugins/${encodeURIComponent(t.dataset.toggle)}/enabled?enabled=${next}`,
        { method: 'POST' });
    } else if (t.dataset.detail) {
      const d = await api(`/api/skills/library/raw/${encodeURIComponent(t.dataset.detail)}/${encodeURIComponent(t.dataset.version)}/SKILL.md`);
      alert((d.text || '').slice(0, 4000));
      return;
    } else {
      return;
    }
    await loadAll();
  } catch (err) {
    alert(`操作失败：${err.message || err}`);
    await loadAll();
  }
});

$('btn-sk-upload')?.addEventListener('click', () => doUpload(false));
$('sk-refresh')?.addEventListener('click', loadAll);
loadAll();
