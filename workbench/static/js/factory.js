import { api, el, logLine, fmtBytes, badgeFor } from './modules-common.js';

const log = el('fac-log');
let currentBlueprintId = '';

async function checkHealth() {
  const badge = el('module-health');
  try {
    const data = await api('/api/modules/akso-auto');
    badge.className = `status-badge ${badgeFor(data.status)}`;
    const fail = (data.checks || []).filter((c) => !c.ok);
    badge.textContent = fail.length
      ? `akso-auto ${data.status}：${fail.map((c) => c.name).join('、')} 未就绪`
      : `akso-auto 就绪（${data.status}）`;
  } catch (e) {
    badge.className = 'status-badge err';
    badge.textContent = `体检失败：${e.message}`;
  }
}

async function loadAccounts() {
  // 分配池语义：默认只列配置池账号；池为空时回退全量并提示
  let data = await api('/api/accounts?pool=config');
  if (!data.accounts.length) {
    data = await api('/api/accounts');
  }
  const sel = el('fac-account');
  sel.innerHTML = '';
  if (!data.accounts.length) {
    sel.innerHTML = '<option value="">（请先在账号中心添加账号并加入配置池）</option>';
    return;
  }
  const note = data.accounts[0].pool ? '' : '（提示：分配池为空，已回退显示全部账号）';
  if (note) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = note;
    opt.disabled = true;
    sel.appendChild(opt);
  }
  for (const a of data.accounts) {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = `${a.username} · ${a.env_name}`;
    sel.appendChild(opt);
  }
}

async function loadBlueprints() {
  const data = await api('/api/factory/blueprints');
  const box = el('fac-blueprints');
  const sel = el('fac-blueprint');
  sel.innerHTML = '';
  if (!data.blueprints.length) {
    box.innerHTML = '<div class="empty">暂无暂存蓝图</div>';
    return;
  }
  box.innerHTML = `<table class="mtable"><thead><tr><th>蓝图</th><th>时间</th><th>id</th></tr></thead><tbody></tbody></table>`;
  const tbody = box.querySelector('tbody');
  for (const b of data.blueprints) {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.innerHTML = `<td>${b.name}</td><td>${new Date(b.mtime).toLocaleString()}</td><td class="mono">${b.blueprint_id}</td>`;
    tr.onclick = () => { sel.value = b.blueprint_id; };
    tbody.appendChild(tr);
    const opt = document.createElement('option');
    opt.value = b.blueprint_id;
    opt.textContent = `${b.name}（${b.blueprint_id}）`;
    sel.appendChild(opt);
  }
  if (currentBlueprintId) sel.value = currentBlueprintId;
}

async function upload() {
  const input = el('fac-file');
  if (!input.files?.length) { logLine(log, '✗ 请选择 blueprint.json', 'err'); return; }
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const resp = await fetch('/api/factory/blueprint', { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    currentBlueprintId = data.blueprint_id;
    el('fac-upload-result').textContent = `已暂存：${data.blueprint_id}（顶层键：${data.top_keys.join(', ') || '—'}）`;
    await loadBlueprints();
  } catch (e) {
    el('fac-upload-result').textContent = `上传失败：${e.message}`;
  }
}

async function run() {
  const blueprint_id = el('fac-blueprint').value;
  const account_id = el('fac-account').value;
  if (!blueprint_id) { logLine(log, '✗ 请先上传/选择蓝图', 'err'); return; }
  if (!account_id) { logLine(log, '✗ 请先在统一账号库添加账号', 'err'); return; }
  if (!el('fac-confirmed').checked) {
    logLine(log, '✗ 违反 akso-auto「环境强制确认」原则：请先勾选确认框', 'err');
    return;
  }
  log.innerHTML = '';
  logLine(log, `$ akso-auto ${el('fac-command').value} <blueprint.json>`, 'sys');
  el('btn-fac-run').disabled = true;
  try {
    const result = await api('/api/factory/run', {
      method: 'POST',
      body: {
        blueprint_id, account_id,
        command: el('fac-command').value,
        confirmed: true,
      },
    });
    for (const line of result.stdout_tail || []) logLine(log, line);
    for (const line of result.stderr_tail || []) logLine(log, line, 'err');
    logLine(log, `■ 结束：${result.status}（exit=${result.code}，${result.duration_s}s）`,
      result.status === 'succeeded' ? 'ok' : 'err');
  } catch (e) {
    logLine(log, `✗ ${e.message}`, 'err');
  } finally {
    el('btn-fac-run').disabled = false;
    await loadJobs();
  }
}

async function loadJobs() {
  const data = await api('/api/factory/jobs?limit=15');
  const tbody = el('fac-jobs').querySelector('tbody');
  tbody.innerHTML = '';
  for (const j of data.jobs) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${new Date(j.created_at).toLocaleString()}</td><td>${j.command}</td>` +
      `<td><span class="status-badge ${badgeFor(j.status)}">${j.status}</span></td><td></td>`;
    const td = tr.children[3];
    const link = document.createElement('a');
    link.textContent = '日志/断点';
    link.style.color = '#4d7cfe';
    link.href = 'javascript:void(0)';
    link.onclick = async () => {
      const detail = await api(`/api/factory/jobs/${j.id}`);
      log.innerHTML = '';
      logLine(log, `— 任务 ${j.id} · ${j.command} · ${j.status} —`, 'sys');
      if (detail.checkpoint && Object.keys(detail.checkpoint).length) {
        logLine(log, `checkpoint：${JSON.stringify(detail.checkpoint)}`, 'sys');
      }
      for (const line of detail.log_tail || []) logLine(log, line);
    };
    td.appendChild(link);
    tbody.appendChild(tr);
  }
}

el('btn-fac-upload').onclick = upload;
el('btn-fac-run').onclick = run;
loadAccounts().then(loadBlueprints).then(loadJobs).then(checkHealth)
  .catch((e) => logLine(log, `✗ 初始化失败：${e.message}`, 'err'));
