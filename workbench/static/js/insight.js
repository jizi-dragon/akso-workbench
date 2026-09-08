import { api, el, logLine, postSSE, fmtBytes, badgeFor } from './modules-common.js';

const log = el('ins-log');

async function loadAccounts() {
  // 分配池语义：默认只列配置池账号；池为空时回退全量并提示
  let data = await api('/api/accounts?pool=config');
  if (!data.accounts.length) {
    data = await api('/api/accounts');
  }
  const sel = el('ins-account');
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
    opt.textContent = `${a.username} · ${a.env_name}${a.env_base_url ? ` (${a.env_base_url})` : ''}`;
    sel.appendChild(opt);
  }
}

async function checkHealth() {
  const badge = el('module-health');
  try {
    const data = await api('/api/modules/akso-cc');
    badge.className = `status-badge ${badgeFor(data.status)}`;
    const fail = (data.checks || []).filter((c) => !c.ok);
    badge.textContent = fail.length
      ? `akso-cc ${data.status}：${fail.map((c) => c.name).join('、')} 未就绪`
      : `akso-cc 就绪（${data.status}）`;
  } catch (e) {
    badge.className = 'status-badge err';
    badge.textContent = `体检失败：${e.message}`;
  }
}

function setRunning(running) {
  el('btn-insight-run').disabled = running;
}

async function run() {
  const command = el('ins-command').value;
  const account_id = el('ins-account').value;
  if (!account_id) { logLine(log, '✗ 请先在统一账号库添加账号', 'err'); return; }
  if (command in { understand: 1, spider: 1 } && !el('ins-objects').value.trim()) {
    logLine(log, `✗ ${command} 需要填写对象编码`, 'err');
    return;
  }
  log.innerHTML = '';
  logLine(log, `$ akso-cc ${command}${command === 'understand' || command === 'spider' ? ` --objects=${el('ins-objects').value}` : ''}`, 'sys');
  setRunning(true);
  try {
    await postSSE('/api/insight/run-sse', {
      command,
      account_id,
      objects: el('ins-objects').value.trim(),
      known_objects: el('ins-known').value.trim(),
      llm: el('ins-llm').checked,
    }, {
      onLog: (line) => logLine(log, line, line.includes('失败') || line.includes('错误') ? 'err' : ''),
      onDone: (evt) => {
        const cls = evt.status === 'succeeded' ? 'ok' : 'err';
        logLine(log, `■ 结束：${evt.status}（exit=${evt.result?.code}，${evt.result?.duration_s}s）`, cls);
        loadRuns();
        if (evt.status === 'succeeded') loadArtifacts(evt.job_id);
      },
      onError: (e) => logLine(log, `✗ ${e.message}`, 'err'),
    });
  } finally {
    setRunning(false);
  }
}

async function loadArtifacts(jobId) {
  const box = el('ins-artifacts');
  try {
    const data = await api(`/api/insight/artifacts/${jobId}`);
    if (!data.files.length) { box.className = 'empty'; box.textContent = '任务完成但无产物文件'; return; }
    box.className = '';
    box.innerHTML = '';
    const table = document.createElement('table');
    table.className = 'mtable';
    table.innerHTML = '<thead><tr><th>文件</th><th>大小</th><th></th></tr></thead>';
    const tbody = document.createElement('tbody');
    for (const f of data.files) {
      const tr = document.createElement('tr');
      const viewable = ['.md', '.json', '.txt', '.log'].includes(f.suffix);
      tr.innerHTML = `<td>${f.name}</td><td>${fmtBytes(f.size)}</td><td></td>`;
      const td = tr.children[2];
      const link = document.createElement('a');
      link.textContent = viewable ? '预览' : '下载';
      link.href = `/api/insight/artifacts/${jobId}/file?name=${encodeURIComponent(f.name)}${viewable ? '' : '&download=1'}`;
      link.target = '_blank';
      link.style.color = '#4d7cfe';
      td.appendChild(link);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);
  } catch (e) {
    box.className = 'empty';
    box.textContent = `产物读取失败：${e.message}`;
  }
}

async function loadRuns() {
  const data = await api('/api/insight/runs?limit=15');
  const tbody = el('ins-runs').querySelector('tbody');
  tbody.innerHTML = '';
  for (const r of data.runs) {
    const tr = document.createElement('tr');
    const t = new Date(r.created_at).toLocaleString();
    tr.innerHTML = `<td>${t}</td><td>${r.command}</td><td>${r.objects || '—'}</td>` +
      `<td><span class="status-badge ${badgeFor(r.status)}">${r.status}</span></td><td></td>`;
    const td = tr.children[4];
    const link = document.createElement('a');
    link.textContent = '产物';
    link.href = 'javascript:void(0)';
    link.style.color = '#4d7cfe';
    link.onclick = () => loadArtifacts(r.id);
    td.appendChild(link);
    tbody.appendChild(tr);
  }
}

el('btn-insight-run').onclick = run;
el('ins-command').onchange = () => {
  const need = ['understand', 'spider'].includes(el('ins-command').value);
  el('ins-objects').disabled = !need;
  el('ins-llm').disabled = el('ins-command').value !== 'understand';
};
el('ins-command').onchange();

loadAccounts().then(checkHealth).then(loadRuns).catch((e) => logLine(log, `✗ 初始化失败：${e.message}`, 'err'));
