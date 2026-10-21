/**
 * 沙箱管理页：策略 / 运行期状态 / 执行审计。
 *
 * 设计纪律（三条，都是有理由的）：
 *  1. **"强制"与"声明"必须分栏显示**。本沙箱不提供内核级隔离，如果把
 *     `allow_network` 这类声明字段和"进程树可终止"这类真强制项并列展示，
 *     读的人会以为它拦得住——那正是 ADR-0014 §D-3 要防的虚假安全感。
 *  2. **无数据的表必须显式说"没有"**，不能留空白（空白会被读成"加载失败"）。
 *  3. 所有文案与后端 `enforced_note` 一致，不在这里另编一套说法。
 */

import { api, el } from './modules-common.js';

const $ = el;

/** 严格度 → 人话。数值越小越紧（后端 `Severity` 的定义）。 */
const SEVERITY_LABEL = { 1: '最紧', 2: '默认', 3: '较松' };

function esc(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

function fmtTime(ms) {
  if (!ms) return '—';
  const d = new Date(Number(ms));
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtDuration(ms) {
  if (ms == null) return '—';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KiB`;
  return `${(n / 1048576).toFixed(1)} MiB`;
}

async function loadHealth() {
  const badge = $('sb-health');
  try {
    const data = await api('/api/sandbox/selfcheck');
    const label = data.ok ? '就绪' : '有问题';
    badge.textContent = `${label} · ${data.policies} 条策略`;
    badge.className = `status-badge ${data.ok ? 'ok' : 'err'}`;
    if (!data.ok) {
      badge.title = (data.problems || []).join('；');
      // 有问题必须在页面上说出来，不能只挂在 title 里（没人会去 hover）
      const box = $('sb-boundary');
      box.insertAdjacentHTML(
        'afterend',
        `<p class="hint" style="color:#f85149">自检未通过：${esc((data.problems || []).join('；'))}</p>`,
      );
    }
    return data;
  } catch (err) {
    badge.textContent = '不可用';
    badge.className = 'status-badge err';
    badge.title = String(err.message || err);
    return null;
  }
}

function renderBoundary(payload) {
  $('sb-boundary').textContent = payload.enforced_note || '（后端未提供边界说明——这本身是个问题）';
  const policies = payload.policies || [];
  const enforced = policies.length ? policies[0].enforced || [] : [];
  $('sb-enforced').innerHTML = enforced.map((x) => `<li>${esc(x)}</li>`).join('')
    || '<li class="hint">（未声明任何强制项）</li>';
  // 声明项：从策略字段里取"我们**没有**强制"的那两个，明确标出
  const declared = [
    '允许联网（allow_network）：仅记录意图，未做网络层强制',
    '允许派生进程（allow_subprocesses）：仅设进程数上限，未做系统调用级限制',
    '文件系统：只校验工作目录在工作区根内，未做内核级读写权限隔离',
    '只读模式：拒绝「声明会写」的策略，不是脚本真的写不了文件',
    '内存 / CPU：未限制',
  ];
  $('sb-declared').innerHTML = declared.map((x) => `<li>${esc(x)}</li>`).join('');
}

function renderPolicies(payload) {
  const tbody = document.querySelector('#sb-policies tbody');
  const rows = payload.policies || [];
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="hint">没有加载到任何策略——沙箱不可用</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((p) => `
    <tr>
      <td class="mono">${esc(p.name)}<br /><span class="hint">${esc(p.fingerprint)}</span></td>
      <td>${esc(SEVERITY_LABEL[p.severity] || p.severity)}</td>
      <td>${p.timeout_s}s</td>
      <td>${fmtBytes(p.max_output_bytes)}</td>
      <td>${p.allow_subprocesses ? '允许' : '限 '}</td>
      <td>${p.writes ? '声明会写' : '只读'}</td>
      <td>${p.allow_network ? '声明允许' : '声明禁止'}</td>
      <td class="hint">${esc(p.description)}</td>
    </tr>`).join('');
}

function renderStatus(status, health) {
  const dirs = status.layout || {};
  const workspace = status.workspace || {};
  const cards = [
    ['平台', status.platform === 'windows' ? 'Windows' : status.platform],
    ['Job Object', status.job?.job_api ? `可用（持有 ${status.job.active_jobs}）` : '不可用 → 走进程组'],
    ['工作区根', workspace.root || dirs.root || '—'],
    ['权限', `${workspace.mode === 'read-only' ? '只读' : '读写'} · `
      + `${workspace.allowNetwork ? '联网开' : '联网关'} · `
      + `${workspace.allowSubprocess ? '派生进程开' : '派生进程关'}`],
    ['工作区 / 已解包', `${dirs.run_count ?? '—'} / ${dirs.staged_count ?? '—'}`],
    ['允许的解释器', (status.executables_allowed || []).join(', ') || '—'],
    ['自检', health?.ok ? '通过' : '未通过'],
  ];
  $('sb-status').innerHTML = cards.map(([k, v]) => `
    <div class="mfield">
      <span class="hint">${esc(k)}</span>
      <div class="mono" style="word-break:break-all">${esc(String(v))}</div>
    </div>`).join('');
}

function renderWorkspace(data) {
  if (!data) return;
  $('sb-ws-root').value = data.root || '';
  $('sb-ws-mode').value = data.mode || 'read-write';
  $('sb-ws-network').checked = Boolean(data.allowNetwork);
  $('sb-ws-subprocess').checked = Boolean(data.allowSubprocess);
  const mode = data.mode === 'read-only' ? '只读' : '读写';
  $('sb-ws-meta').textContent =
    `当前：${mode} · ${data.allowNetwork ? '允许联网' : '禁止联网'} · `
    + `${data.allowSubprocess ? '允许派生进程' : '禁止派生进程'} · `
    + `配置文件 ${data.configPath || '—'}`;
  $('sb-ws-honest').textContent = data.honest_limit || '';
}

async function saveWorkspace() {
  const meta = $('sb-ws-meta');
  try {
    const data = await api('/api/sandbox/workspace', {
      method: 'PUT',
      body: {
        root: $('sb-ws-root').value.trim(),
        mode: $('sb-ws-mode').value,
        allowNetwork: $('sb-ws-network').checked,
        allowSubprocess: $('sb-ws-subprocess').checked,
      },
    });
    renderWorkspace(data);
    meta.textContent = '✓ 已保存。下一次沙箱执行起生效。';
  } catch (err) {
    meta.textContent = `✗ 保存失败：${err.message}`;
    meta.style.color = '#f85149';
    setTimeout(() => { meta.style.color = ''; }, 3200);
  }
}

async function resetWorkspace() {
  const meta = $('sb-ws-meta');
  try {
    const data = await api('/api/sandbox/workspace/reset', { method: 'POST' });
    renderWorkspace(data);
    await refresh();
    // `refresh()` 会再 renderWorkspace 一次；提示必须放在它之后，否则被"当前：…"覆盖。
    meta.textContent = '✓ 已恢复默认工作区与保守权限。';
  } catch (err) {
    meta.textContent = `✗ 重置失败：${err.message}`;
  }
}

async function loadWorkspace() {
  try {
    renderWorkspace(await api('/api/sandbox/workspace'));
  } catch (err) {
    $('sb-ws-meta').textContent = `读取失败：${err.message}`;
  }
}

function renderRuns(payload) {
  const tbody = document.querySelector('#sb-runs tbody');
  const rows = payload.runs || [];
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="hint">还没有任何沙箱执行记录</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) => {
    const verdict = r.timed_out ? '超时' : r.ok ? '成功' : `失败（code=${r.code}）`;
    const cls = r.ok ? 'ok' : r.timed_out ? 'warn' : 'err';
    const flags = [];
    if (r.output_truncated) flags.push('输出截断');
    return `
      <tr>
        <td>${fmtTime(r.created_at)}</td>
        <td>${esc(r.label) || '<span class="hint">—</span>'}</td>
        <td class="mono">${esc(r.policy)}<br /><span class="hint">${esc(r.policy_fingerprint)}</span></td>
        <td><span class="status-badge ${cls}">${esc(verdict)}</span>${flags.length ? `<br /><span class="hint">${esc(flags.join('、'))}</span>` : ''}</td>
        <td>${fmtDuration(r.duration_ms)}</td>
        <td class="hint">${esc(r.tree_kill) || '—'}</td>
        <td class="mono hint" style="word-break:break-all">${esc(r.argv_summary) || '—'}</td>
      </tr>`;
  }).join('');
}

async function refresh() {
  const health = await loadHealth();
  try {
    const [policies, status, runs] = await Promise.all([
      api('/api/sandbox/policies'),
      api('/api/sandbox/status'),
      api('/api/sandbox/runs?limit=100'),
    ]);
    renderBoundary(policies);
    renderPolicies(policies);
    renderStatus(status, health);
    renderWorkspace(status.workspace);
    renderRuns(runs);
  } catch (err) {
    const tbody = document.querySelector('#sb-runs tbody');
    tbody.innerHTML = `<tr><td colspan="7" class="hint" style="color:#f85149">加载失败：${esc(String(err.message || err))}</td></tr>`;
  }
}

$('sb-refresh')?.addEventListener('click', refresh);
$('sb-ws-save')?.addEventListener('click', saveWorkspace);
$('sb-ws-reset')?.addEventListener('click', resetWorkspace);
$('sb-ws-copy')?.addEventListener('click', async (event) => {
  const root = $('sb-ws-root').value.trim();
  try {
    await navigator.clipboard.writeText(root);
    event.currentTarget.textContent = '已复制';
  } catch {
    event.currentTarget.textContent = '复制失败';
  }
  setTimeout(() => { event.currentTarget.textContent = '复制路径'; }, 1200);
});
refresh();
