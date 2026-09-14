import { api, el, postSSE, fmtBytes, badgeFor } from './modules-common.js';
import { bindPreview, previewArtifact } from './artifact-preview.js';

/** 能在应用内预览的后缀（其余只能下载）。drawio 与 markdown 都可就地预览。 */
const PREVIEWABLE = ['.md', '.drawio', '.json', '.txt', '.log', '.csv'];

const PAGE_SIZE = 10;   // 用户定稿：一页 10 条 + 翻页
let runPage = 0;
let runTotal = 0;
let currentJob = '';

/** 单行运行反馈（用户定稿：删掉常驻日志区，但保留日志能力——每行任务的「日志」看 proc.log）。 */
function setStatus(text) {
  const box = el('ins-run-status');
  if (box) box.textContent = text;
}

function link(text, onClick) {
  const node = document.createElement('a');
  node.textContent = text;
  node.href = 'javascript:void(0)';
  node.style.color = '#4d7cfe';
  node.onclick = onClick;
  return node;
}

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
    // 模块 id = `insight`（2026-09-14 由上游项目名 `akso-cc` 改来）：能力已是本工作台自己的，
    // 界面上与接口上都不该再出现上游项目名。体检内容是本工作台自己的依赖自检。
    const data = await api('/api/modules/insight');
    badge.className = `status-badge ${badgeFor(data.status)}`;
    const fail = (data.checks || []).filter((c) => !c.ok);
    badge.textContent = fail.length
      ? `依赖体检 ${data.status}：${fail.map((c) => c.name).join('、')} 未就绪`
      : `依赖就绪（${data.status}）`;
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
  const objects = el('ins-objects').value.trim();
  if (!account_id) { setStatus('✗ 请先在统一账号库添加账号'); return; }
  if (command === 'spider' && !objects) { setStatus('✗ spider 需要填写对象编码'); return; }

  setStatus(`执行中：insight ${command}${objects ? ` --objects=${objects}` : ''} …`);
  setRunning(true);
  try {
    await postSSE('/api/insight/run-sse', { command, account_id, objects }, {
      // 不再有常驻日志区：SSE 的日志行只用来刷新这一行状态（完整日志仍在 proc.log 里）
      onLog: (line) => setStatus(line),
      onDone: (evt) => {
        const stats = evt.stats || evt.result?.stats || {};
        const summary = Object.keys(stats).length
          ? `｜${stats.objectNodes ?? 0} 对象 / ${stats.stateNodes ?? 0} 状态 / `
            + `${stats.attrEdges ?? 0} 归因边 / ${stats.relationEdges ?? 0} 关系边`
          : '';
        setStatus(`■ ${evt.status}${summary}`);
        runPage = 0;
        loadRuns();
        loadArtifacts(evt.job_id);
      },
      onError: (e) => setStatus(`✗ ${e.message}`),
    });
  } finally {
    setRunning(false);
  }
}

async function loadArtifacts(jobId, autoPreview = '') {
  const box = el('ins-artifacts');
  currentJob = jobId;
  try {
    const data = await api(`/api/insight/artifacts/${jobId}`);
    if (!data.files.length) {
      box.className = 'empty';
      box.textContent = '任务完成但无产物文件（读失败会写进 manifest.failures）';
      return;
    }
    box.className = '';
    box.innerHTML = '';
    const table = document.createElement('table');
    table.className = 'mtable';
    table.innerHTML = '<thead><tr><th>文件</th><th>大小</th><th>操作</th></tr></thead>';
    const tbody = document.createElement('tbody');
    for (const f of data.files) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${f.name}</td><td>${fmtBytes(f.size)}</td><td></td>`;
      const td = tr.children[2];
      if (PREVIEWABLE.includes(f.suffix)) {
        td.appendChild(link('预览', () => previewArtifact({ jobId, name: f.name, size: f.size })));
        td.appendChild(document.createTextNode(' · '));
      }
      const download = document.createElement('a');
      download.textContent = '下载';
      download.href = `/api/insight/artifacts/${jobId}/file?name=${encodeURIComponent(f.name)}&download=1`;
      download.style.color = '#4d7cfe';
      td.appendChild(download);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);

    if (autoPreview) {
      const target = data.files.find((f) => f.name === autoPreview)
        || data.files.find((f) => f.suffix === '.log');
      if (target) {
        previewArtifact({ jobId, name: target.name, size: target.size });
      } else {
        setStatus('该任务没有日志文件（login/spider 都会写 proc.log）');
      }
    }
  } catch (e) {
    box.className = 'empty';
    box.textContent = `产物读取失败：${e.message}`;
  }
}

/** 任务行的「日志」：列产物并直接把 proc.log 打开在预览弹窗里（日志功能保留在这里）。 */
async function openRunLog(jobId) {
  await loadArtifacts(jobId, 'proc.log');
}

function renderPager() {
  const box = el('ins-pager');
  const pages = Math.max(1, Math.ceil(runTotal / PAGE_SIZE));
  box.innerHTML = '';
  if (runTotal <= PAGE_SIZE) return;

  const prev = document.createElement('button');
  prev.className = 'mbtn ghost';
  prev.textContent = '← 上一页';
  prev.disabled = runPage <= 0;
  prev.onclick = () => { runPage -= 1; loadRuns(); };

  const info = document.createElement('span');
  info.className = 'ins-pager-info';
  info.textContent = `第 ${runPage + 1} / ${pages} 页 · 共 ${runTotal} 条`;

  const next = document.createElement('button');
  next.className = 'mbtn ghost';
  next.textContent = '下一页 →';
  next.disabled = runPage >= pages - 1;
  next.onclick = () => { runPage += 1; loadRuns(); };

  box.append(prev, info, next);
}

async function loadRuns() {
  const data = await api(`/api/insight/runs?limit=${PAGE_SIZE}&offset=${runPage * PAGE_SIZE}`);
  runTotal = data.total ?? data.runs.length;
  const tbody = el('ins-runs').querySelector('tbody');
  tbody.innerHTML = '';
  for (const r of data.runs) {
    const tr = document.createElement('tr');
    const t = new Date(r.created_at).toLocaleString();
    tr.innerHTML = `<td>${t}</td><td>${r.command}</td><td>${r.objects || '—'}</td>` +
      `<td><span class="status-badge ${badgeFor(r.status)}">${r.status}</span></td><td></td>`;
    const td = tr.children[4];
    td.appendChild(link('产物', () => loadArtifacts(r.id)));
    td.appendChild(document.createTextNode(' · '));
    td.appendChild(link('日志', () => openRunLog(r.id)));
    tbody.appendChild(tr);
  }
  if (!data.runs.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">还没有任务</td></tr>';
  }
  renderPager();
}

el('btn-insight-run').onclick = run;
bindPreview();
// 对象编码只在 spider 下需要；login 时整格隐藏，避免误以为必填
el('ins-command').onchange = () => {
  el('ins-objects-field').hidden = el('ins-command').value !== 'spider';
};
el('ins-command').onchange();

loadAccounts()
  .then(checkHealth)
  .then(loadRuns)
  .catch((e) => setStatus(`✗ 初始化失败：${e.message}`));
