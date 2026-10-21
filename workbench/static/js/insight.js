import { api, el, postSSE, fmtBytes, badgeFor } from './modules-common.js';
import { bindPreview, previewArtifact } from './artifact-preview.js';

/** 能在应用内预览的后缀（其余只能下载）。图片也走弹窗预览。 */
const PREVIEWABLE = [
  '.md', '.markdown', '.drawio', '.json', '.txt', '.log', '.csv',
  '.png', '.jpg', '.jpeg', '.webp', '.gif',
];

const KIND_META = {
  report: { label: '报告', icon: 'MD' },
  graph: { label: '图', icon: 'DIO' },
  data: { label: '数据', icon: 'JSON' },
  log: { label: '日志', icon: 'LOG' },
  image: { label: '图片', icon: 'IMG' },
  other: { label: '其他', icon: 'FILE' },
};

let artifactJob = '';
let artifactFiles = [];
let artifactFilter = 'all';

function kindOf(suffix) {
  if (['.md', '.markdown'].includes(suffix)) return 'report';
  if (suffix === '.drawio') return 'graph';
  if (['.json', '.csv'].includes(suffix)) return 'data';
  if (['.log', '.txt'].includes(suffix)) return 'log';
  if (['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(suffix)) return 'image';
  return 'other';
}

function shortName(name) {
  return name.length > 34 ? `${name.slice(0, 31)}…` : name;
}

function paintArtifactFilters() {
  const box = el('ins-artifact-filters');
  if (!box) return;
  const counts = { all: artifactFiles.length };
  for (const file of artifactFiles) {
    const kind = kindOf(file.suffix);
    counts[kind] = (counts[kind] || 0) + 1;
  }
  const items = [['all', '全部'], ...Object.entries(KIND_META).map(([key, meta]) => [key, meta.label])];
  box.innerHTML = '';
  for (const [key, label] of items) {
    const count = counts[key] || 0;
    if (!count && key !== 'all') continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `artifact-chip${artifactFilter === key ? ' active' : ''}`;
    button.textContent = label;
    const badge = document.createElement('b');
    badge.textContent = String(count);
    button.appendChild(badge);
    button.addEventListener('click', () => {
      artifactFilter = key;
      paintArtifactFilters();
      paintArtifactGrid();
    });
    box.appendChild(button);
  }
  const countEl = el('ins-artifact-count');
  if (countEl) {
    const total = artifactFiles.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
    countEl.textContent = `任务 ${artifactJob} · ${artifactFiles.length} 个文件 · ${fmtBytes(total)}`;
  }
}

function paintArtifactGrid() {
  const box = el('ins-artifacts');
  if (!box) return;
  const files = artifactFilter === 'all'
    ? artifactFiles
    : artifactFiles.filter((file) => kindOf(file.suffix) === artifactFilter);
  box.className = 'artifact-grid';
  box.innerHTML = '';
  if (!files.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.style.gridColumn = '1/-1';
    empty.textContent = artifactFilter === 'all'
      ? '任务完成但无产物文件（读失败会写进 manifest.failures）'
      : '这个分类下没有文件。';
    box.appendChild(empty);
    return;
  }
  for (const file of files) {
    const kind = kindOf(file.suffix);
    const meta = KIND_META[kind];
    const card = document.createElement('div');
    card.className = 'artifact-card';
    card.dataset.kind = kind;
    card.dataset.name = file.name;
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `${file.name} 产物`);

    const icon = document.createElement('span');
    icon.className = 'artifact-icon';
    icon.textContent = meta.icon;

    const main = document.createElement('div');
    main.className = 'artifact-main';
    const name = document.createElement('div');
    name.className = 'artifact-name';
    name.title = file.name;
    name.textContent = shortName(file.name);
    const detail = document.createElement('div');
    detail.className = 'artifact-meta';
    detail.textContent = `${String(file.suffix || 'file').replace('.', '').toUpperCase()} · ${fmtBytes(file.size)}`;
    main.append(name, detail);

    const actions = document.createElement('div');
    actions.className = 'artifact-actions';
    const download = document.createElement('a');
    download.href = `/api/insight/artifacts/${artifactJob}/file?name=${encodeURIComponent(file.name)}&download=1`;
    download.download = file.name;
    download.title = '下载';
    download.textContent = '↓';
    download.addEventListener('click', (event) => event.stopPropagation());
    actions.appendChild(download);
    if (PREVIEWABLE.includes(file.suffix)) {
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.title = '预览';
      preview.textContent = '◉';
      preview.addEventListener('click', (event) => {
        event.stopPropagation();
        openArtifact(file, card);
      });
      actions.appendChild(preview);
    }

    card.append(icon, main, actions);
    card.addEventListener('click', () => openArtifact(file, card));
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openArtifact(file, card);
      }
    });
    box.appendChild(card);
  }
}

function openArtifact(file, card) {
  if (!PREVIEWABLE.includes(file.suffix)) return;
  for (const node of document.querySelectorAll('.artifact-card.selected')) {
    node.classList.remove('selected');
  }
  card?.classList.add('selected');
  void previewArtifact({ jobId: artifactJob, name: file.name, size: file.size });
}

async function loadArtifacts(jobId, autoPreview = '') {
  const box = el('ins-artifacts');
  artifactJob = jobId;
  try {
    const data = await api(`/api/insight/artifacts/${jobId}`);
    artifactFiles = data.files || [];
    artifactFilter = 'all';
    if (!artifactFiles.length) {
      box.className = 'empty';
      box.style.gridColumn = '1/-1';
      box.textContent = '任务完成但无产物文件（读失败会写进 manifest.failures）';
      paintArtifactFilters();
      return;
    }
    box.style.gridColumn = '';
    paintArtifactFilters();
    paintArtifactGrid();

    if (autoPreview) {
      const target = artifactFiles.find((f) => f.name === autoPreview)
        || artifactFiles.find((f) => f.suffix === '.log');
      if (target) {
        openArtifact(target, box.querySelector(`[data-name="${CSS.escape(target.name)}"]`));
      } else {
        setStatus('该任务没有日志文件（login/spider 都会写 proc.log）');
      }
    }
  } catch (e) {
    box.className = 'empty';
    box.textContent = `产物读取失败：${e.message}`;
  }
}

const PAGE_SIZE = 10;   // 用户定稿：一页 10 条 + 翻页
let runPage = 0;
let runTotal = 0;

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
