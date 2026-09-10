/** 账号中心 v2（quick-login 管理页复刻）：
 *  上游 parallel 页的视觉与功能基线（盒子 chips 全操作 / 批量管理 / 四态徽标 /
 *  移盒弹窗 / 删盒两步处置 / 顶栏统计 / 指纹防闪烁），数据面接桌面 API，
 *  四态徽标数据来自扩展执行面状态回传（/extension/state）。
 */

import { api, el, logLine } from './modules-common.js';

// quick-login SESSION_COLORS（轮盘色环语义）
const COLORS = ['#1E6FFF', '#0FA3B1', '#7C5CFF', '#FF7A1A', '#22C55E'];
const WHEEL_MAX = 10;
const NS = 'http://www.w3.org/2000/svg';

let cacheAccounts = [];
let cacheSessions = new Map();
let cacheBoxes = [];
let cacheDisabled = [];
let savedCache = new Map();
const extState = new Map(); // desktopId → {tabs, hasToken, enforcementOff}

let currentBox = ''; // '' = 全部盒子
let batchOn = false;
const selection = new Set();
let lastFp = '';

/* ———————————————— 轮盘（v3.9 几何：扇形 + Hub 切盒 + 半透明覆盖层） ———————————————— */

const SIZE = 520;
const C = SIZE / 2;
const R_OUT = 240;
const R_IN = 118;
const R_ARC = 254;
const GAP_DEG = 2;
let wheelPages = [];
let wheelPage = 0;
let wheelOpen = false;

function polar(r, deg) {
  const a = ((deg - 90) * Math.PI) / 180;
  return { x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
}

function sectorPath(a0, a1) {
  const s = a0 + GAP_DEG / 2;
  const e = a1 - GAP_DEG / 2;
  const large = e - s > 180 ? 1 : 0;
  const p1 = polar(R_OUT, s);
  const p2 = polar(R_OUT, e);
  const p3 = polar(R_IN, e);
  const p4 = polar(R_IN, s);
  return [
    `M ${p1.x} ${p1.y}`,
    `A ${R_OUT} ${R_OUT} 0 ${large} 1 ${p2.x} ${p2.y}`,
    `L ${p3.x} ${p3.y}`,
    `A ${R_IN} ${R_IN} 0 ${large} 0 ${p4.x} ${p4.y}`,
    'Z',
  ].join(' ');
}

function svgEl(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  return node;
}

function radialText(cls, mid, r, content) {
  const p = polar(r, mid);
  const flip = mid > 90 && mid < 270;
  const rot = flip ? mid + 180 : mid;
  const t = svgEl('text', {
    class: cls, x: p.x, y: p.y,
    'text-anchor': 'middle', 'dominant-baseline': 'central',
    transform: `rotate(${rot} ${p.x} ${p.y})`,
  });
  t.textContent = content;
  return t;
}

function truncate(s, max) { return s.length > max ? `${s.slice(0, max - 1)}…` : s; }

function colorOf(i) { return COLORS[((i % COLORS.length) + COLORS.length) % COLORS.length]; }
function poolList(pool) { return String(pool || '').split(',').filter(Boolean); }

/** 按盒子分页（缺省归默认盒子；跳过禁用盒——轮盘语义） */
function groupPagesByBox(accounts, disabled) {
  const pages = [];
  const byName = new Map();
  for (const a of accounts) {
    const name = (a.box || '').trim();
    if (disabled.includes(name)) continue;
    if (!byName.has(name)) {
      byName.set(name, []);
      pages.push({ label: name || '默认盒子', accounts: byName.get(name) });
    }
    byName.get(name).push(a);
  }
  return pages;
}

function buildSectorWheel(root, { pages, pageIndex, onPick }) {
  root.innerHTML = '';
  if (!pages.length || !pages.some((p) => p.accounts.length)) {
    root.innerHTML = '<span class="empty">暂无可选账号——请在账号中心添加或启用盒子</span>';
    return;
  }
  const svg = svgEl('svg', { class: 'sector-svg', viewBox: `0 0 ${SIZE} ${SIZE}` });
  root.appendChild(svg);

  const trackA = -20;
  const trackB = 100;
  const track = svgEl('path', {
    class: 'box-track',
    d: `M ${polar(R_ARC, trackA).x} ${polar(R_ARC, trackA).y} A ${R_ARC} ${R_ARC} 0 0 1 ${polar(R_ARC, trackB).x} ${polar(R_ARC, trackB).y}`,
  });
  svg.appendChild(track);
  pages.forEach((page, idx) => {
    const deg = trackA + ((trackB - trackA) * (idx + 0.5)) / Math.max(pages.length, 1);
    const p = polar(R_ARC, deg);
    const node = svgEl('circle', {
      class: `box-node${idx === pageIndex ? ' box-node-on' : ''}`,
      cx: p.x, cy: p.y, r: 7,
    });
    node.addEventListener('click', () => { wheelPage = idx; renderWheelOverlay(); });
    svg.appendChild(node);
  });

  const page = pages[pageIndex] || { label: '', accounts: [] };
  const list = page.accounts.slice(0, WHEEL_MAX);
  const sweep = 360 / Math.max(list.length, 1);
  list.forEach((a, idx) => {
    const a0 = idx * sweep;
    const a1 = (idx + 1) * sweep;
    const mid = (a0 + a1) / 2;
    const g = svgEl('g', { class: `sector${extBadgeOf(a).cls === 'online' ? ' is-online' : ''}` });
    g.style.setProperty('--acc', colorOf(cacheAccounts.indexOf(a)));
    g.style.animationDelay = `${0.06 + idx * 0.05}s`; // 扇区逐个入场（上游 v3.9 语义）
    const hit = svgEl('path', { class: 'sector-hit', d: sectorPath(a0, a1) });
    hit.addEventListener('click', () => onPick(a.id));
    g.appendChild(hit);
    g.appendChild(radialText('sector-label', mid, (R_IN + R_OUT) / 2, truncate(a.username, 8)));
    const num = svgEl('g', { class: 'sector-num' });
    const np = polar(R_IN + 26, mid);
    num.appendChild(svgEl('circle', { cx: np.x, cy: np.y, r: 13 }));
    const nt = svgEl('text', { x: np.x, y: np.y, 'text-anchor': 'middle', 'dominant-baseline': 'central' });
    nt.textContent = idx === 9 ? '0' : String(idx + 1);
    num.appendChild(nt);
    g.appendChild(num);
    const dot = svgEl('circle', { class: `sector-dot${extBadgeOf(a).cls === 'online' ? ' on' : ''}`, cx: 0, cy: 0, r: 6 });
    const dp = polar(R_OUT - 12, mid);
    dot.setAttribute('cx', dp.x);
    dot.setAttribute('cy', dp.y);
    g.appendChild(dot);
    svg.appendChild(g);
  });

  const hub = svgEl('g', { class: 'hub hub-click' });
  hub.appendChild(svgEl('circle', { class: 'hub-bg', cx: C, cy: C, r: 100 }));
  hub.appendChild(radialText('hub-box', 0, 0, truncate(pages[pageIndex]?.label || '默认盒子', 10)));
  const numText = svgEl('text', { class: 'hub-num', x: C, y: C - 18, 'dominant-baseline': 'central' });
  numText.textContent = `${pageIndex + 1}/${pages.length}`;
  hub.appendChild(numText);
  const sub = svgEl('text', { class: 'hub-sub', x: C, y: C + 34, 'dominant-baseline': 'central' });
  sub.textContent = '点击或滚轮切盒';
  hub.appendChild(sub);
  hub.addEventListener('click', () => { wheelPage = (wheelPage + 1) % pages.length; renderWheelOverlay(); });
  svg.appendChild(hub);
  root.appendChild(svg);
}

function isOnline(a) { return extBadgeOf(a).cls === 'online'; }

function openWheel() {
  if (!cacheAccounts.length) {
    alert('暂无账号——先在下方新增或从 env 导入。');
    return;
  }
  wheelPages = groupPagesByBox(cacheAccounts, cacheDisabled);
  wheelPage = Math.min(wheelPage, wheelPages.length - 1);
  wheelOpen = true;
  const overlay = el('wheel-overlay');
  overlay.hidden = false;
  renderWheelOverlay();
  requestAnimationFrame(() => overlay.classList.add('in'));
}

function closeWheel() {
  const overlay = el('wheel-overlay');
  overlay.classList.remove('in');
  wheelOpen = false;
  setTimeout(() => { overlay.hidden = true; }, 200);
}

let lastWheelFp = '';

function renderWheelOverlay() {
  wheelPages = groupPagesByBox(cacheAccounts, cacheDisabled);
  if (wheelPage >= wheelPages.length) wheelPage = 0;
  // 指纹防重绘：3s 轮询数据未变不重建（否则入场动画每 3s 重播 = 轮盘眨眼）
  const fp = JSON.stringify([wheelPages.map((p) => [p.label, p.accounts.map((a) => a.id)]), wheelPage]);
  if (fp === lastWheelFp) return;
  lastWheelFp = fp;
  buildSectorWheel(el('wheel-svg'), {
    pages: wheelPages,
    pageIndex: wheelPage,
    onPick: (accountId) => { closeWheel(); quickLogin(accountId); },
  });
}

async function postExtCommand(type, payload) {
  try {
    await api('/extension/commands', { method: 'POST', body: { type, payload } });
  } catch (e) {
    alert(`指令下发失败：${e.message}`);
  }
}

document.addEventListener('keydown', (e) => {
  /* Alt+Q 为主（桌面壳运行时被全局热键接管，纯浏览器模式由页面响应）；
     Ctrl+Shift+Q 保留为页内备用 */
  const q = e.key.toLowerCase() === 'q';
  if (q && (e.altKey || (e.ctrlKey && e.shiftKey))) {
    e.preventDefault();
    if (wheelOpen) closeWheel(); else openWheel();
    return;
  }
  if (!wheelOpen) return;
  if (e.key === 'Escape') { closeWheel(); return; }
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  const page = wheelPages[wheelPage];
  if (!page) return;
  const idx = Number(e.key) === 0 ? 9 : Number(e.key) - 1; // 0 = 第 10 个（上游语义）
  if (!Number.isNaN(idx) && idx >= 0 && idx < 10 && idx < page.accounts.length) {
    const account = page.accounts[idx];
    if (account) { closeWheel(); quickLogin(account.id); }
  }
});

el('wheel-overlay')?.addEventListener('click', (e) => {
  if (e.target.id === 'wheel-overlay') closeWheel();
});
el('wheel-overlay')?.addEventListener('wheel', (e) => {
  if (!wheelOpen) return;
  e.preventDefault();
  wheelPage = (wheelPage + (e.deltaY > 0 ? 1 : wheelPages.length - 1)) % wheelPages.length;
  renderWheelOverlay();
}, { passive: false });

/* ———————————————— 指令路径：快捷登录（launch-chrome + par.open） ———————————————— */

async function quickLogin(accountId) {
  try {
    await api('/extension/launch-chrome', { method: 'POST' });
  } catch { /* 壳不可达时仍尝试指令（扩展可能已在轮询） */ }
  await postExtCommand('par.open', { accountId });
}

/* ———————————————— 四态徽标（扩展执行面状态 + 内置会话回落） ———————————————— */

function extBadgeOf(a) {
  const st = extState.get(a.id);
  if (st) {
    if (st.enforcementOff) return { cls: 'login_failed', label: '未授权 · 已暂停' };
    if (st.tabs > 0 && st.hasToken) return { cls: 'online', label: `在线 ×${st.tabs}` };
    if (st.tabs > 0) return { cls: 'starting', label: '待登录' };
    return { cls: 'offline', label: '离线' };
  }
  const s = cacheSessions.get(a.id);
  if (s && s.status === 'online') return { cls: 'online', label: '在线 · 内置' };
  if (s && s.status && s.status !== 'stopped') return { cls: 'starting', label: s.status };
  return { cls: 'offline', label: '离线' };
}

/* ———————————————— 数据加载 + 指纹防闪烁渲染 ———————————————— */

async function loadSavedFlags(accounts) {
  const entries = await Promise.all(accounts.map(async (a) => {
    const saved = await api(`/api/browser/saved/${a.id}`).catch(() => ({ saved: false }));
    return [a.id, saved.saved];
  }));
  savedCache = new Map(entries);
}

async function refresh() {
  const [accData, sessData, boxData, stateData] = await Promise.all([
    api('/api/accounts'),
    api('/api/browser/sessions'),
    api('/api/accounts/boxes'),
    api('/extension/state').catch(() => ({ items: [] })),
  ]);
  cacheAccounts = accData.accounts;
  cacheSessions = new Map(sessData.sessions.map((s) => [s.account_id, s]));
  cacheBoxes = boxData.boxes;
  cacheDisabled = boxData.disabled || accData.disabled_boxes || [];
  extState.clear();
  for (const it of stateData.items || []) extState.set(it.desktopId, it);
  await loadSavedFlags(cacheAccounts);

  renderStats();
  const fp = JSON.stringify([
    cacheAccounts.map((a) => [a.id, a.box, a.pool, a.tags, a.has_password, a.env_name, a.env_base_url, a.username, a.tab_name, a.role]),
    [...cacheSessions].map(([k, s]) => [k, s.status, s.has_token, s.monitoring, s.title]),
    cacheBoxes, cacheDisabled, [...extState], [...savedCache],
    currentBox, batchOn, [...selection].sort(),
  ]);
  if (fp === lastFp) return; // 指纹未变不重建 DOM（防闪烁、保留悬停态）
  lastFp = fp;

  renderBoxChips();
  renderPools();
  const visible = currentBox === ''
    ? cacheAccounts
    : cacheAccounts.filter((a) => (a.box || '').trim() === currentBox);
  renderCards(visible);
  if (wheelOpen) renderWheelOverlay();
}

function renderStats() {
  el('stat-accounts').textContent = String(cacheAccounts.length);
  const online = cacheAccounts.filter((a) => extBadgeOf(a).cls === 'online').length;
  el('stat-online').textContent = String(online);
  el('stat-boxes').textContent = String(new Set(cacheAccounts.map((a) => (a.box || '').trim())).size);
}

/* ———————————————— 盒子 chips（悬停操作：✎ 重命名 ⏸/▶ 禁用 ✕ 删除） ———————————————— */

function renderBoxChips() {
  const row = el('box-chips');
  const total = cacheAccounts.length;
  const defaultBox = cacheBoxes.find((b) => b.box === '');
  const named = cacheBoxes.filter((b) => b.box !== '');
  const parts = [`<span class="chip ${currentBox === '' ? 'active' : ''}" data-box="">全部 <span class="chip-n">${total}</span></span>`];
  if (defaultBox) {
    parts.push(`<span class="chip" data-box="" data-default="1">默认盒子 <span class="chip-n">${defaultBox.count}</span><span class="chip-act" data-op="defname">✎</span></span>`);
  }
  for (const b of named) {
    const off = cacheDisabled.includes(b.box);
    parts.push(`<span class="chip ${currentBox === b.box ? 'active' : ''} ${off ? 'chip-off' : ''}" data-box="${b.box}">${b.displayName} <span class="chip-n">${b.count}</span>`
      + `<span class="chip-act" data-op="rename">✎</span>`
      + `<span class="chip-act" data-op="disable">${off ? '▶' : '⏸'}</span>`
      + `<span class="chip-act chip-act-del" data-op="del">✕</span></span>`);
  }
  parts.push('<span class="chip chip-add" data-add="1">＋ 新建盒</span>');
  row.innerHTML = parts.join('');

  row.querySelectorAll('.chip').forEach((chip) => {
    chip.onclick = async (ev) => {
      const op = ev.target?.dataset?.op;
      if (op) { ev.stopPropagation(); await boxOp(op, chip); return; }
      if (chip.dataset.add) {
        const name = prompt('新盒子名称：');
        if (!name || !name.trim()) return;
        await api('/api/accounts/boxes/create', { method: 'POST', body: { name: name.trim() } });
        currentBox = name.trim();
        refresh();
        return;
      }
      currentBox = chip.dataset.box || '';
      refresh();
    };
  });

  const datalist = el('box-list');
  if (datalist) {
    datalist.innerHTML = cacheBoxes
      .filter((b) => b.box)
      .map((b) => `<option value="${b.box}"></option>`)
      .join('');
  }
}

async function boxOp(op, chip) {
  const box = chip.dataset.box || '';
  const displayName = box || '默认盒子';
  const inBox = cacheAccounts.filter((a) => (a.box || '').trim() === box);
  if (op === 'defname') {
    const name = prompt('默认盒子的显示名：', '') ?? '';
    await api('/api/accounts/boxes/default-name', { method: 'POST', body: { to: name } });
    refresh();
    return;
  }
  if (op === 'rename') {
    const to = prompt(`重命名盒子「${displayName}」为：`, box) ?? '';
    if (!to.trim() || to.trim() === box) return;
    const result = await api('/api/accounts/boxes/rename', { method: 'POST', body: { from: box, to: to.trim() } });
    alert(`已移动 ${result.moved} 个账号`);
    currentBox = to.trim();
    refresh();
    return;
  }
  if (op === 'disable') {
    const off = cacheDisabled.includes(box);
    await api('/api/accounts/boxes/disable', { method: 'POST', body: { box, disabled: !off } });
    refresh();
    return;
  }
  if (op === 'del') {
    if (!confirm(`删除盒子「${displayName}」？`)) return;
    let withAccounts = false;
    if (inBox.length) {
      withAccounts = confirm(`盒内还有 ${inBox.length} 个账号。\n「确定」= 连同账号一并删除；「取消」= 账号并入默认盒，仅删盒子。`);
    }
    if (withAccounts) {
      for (const a of inBox) await api(`/api/accounts/${a.id}`, { method: 'DELETE' });
    }
    const result = await api('/api/accounts/boxes/delete', { method: 'POST', body: { from: box } });
    alert(withAccounts ? '盒子与账号已删除' : `已并入 ${result.moved} 个账号`);
    currentBox = '';
    refresh();
  }
}

/* ———————————————— 移入盒子弹窗（单选带计数 + 新盒名自动创建） ———————————————— */

let boxModalTargets = [];
let boxModalChoice = null; // '' = 默认盒；'名' = 命名盒；null = 未选

function openBoxModal(ids) {
  boxModalTargets = ids;
  boxModalChoice = null;
  el('box-modal-title').textContent = ids.length > 1 ? `移入盒子（已选 ${ids.length} 个账号）` : '移入盒子';
  el('box-new-name').value = '';
  renderBoxOptions();
  el('box-modal').classList.remove('hidden');
}

function renderBoxOptions() {
  const wrap = el('box-options');
  const defaultBox = cacheBoxes.find((b) => b.box === '');
  const options = [`<button type="button" class="box-option ${boxModalChoice === '' ? 'active' : ''}" data-box=""><span>默认盒子</span><span class="chip-n">${defaultBox?.count ?? 0}</span></button>`];
  for (const b of cacheBoxes.filter((x) => x.box !== '')) {
    options.push(`<button type="button" class="box-option ${boxModalChoice === b.box ? 'active' : ''}" data-box="${b.box}"><span>${b.displayName}</span><span class="chip-n">${b.count}</span></button>`);
  }
  wrap.innerHTML = options.join('');
  wrap.querySelectorAll('.box-option').forEach((btn) => {
    btn.onclick = () => {
      boxModalChoice = btn.dataset.box || '';
      el('box-new-name').value = '';
      renderBoxOptions();
    };
  });
}

async function confirmBoxMove() {
  const newName = el('box-new-name').value.trim();
  const target = newName || boxModalChoice;
  if (target === null) return alert('请选择目标盒子，或输入新盒名。');
  for (const id of boxModalTargets) {
    await api(`/api/accounts/${id}`, { method: 'PATCH', body: { box: target } });
  }
  el('box-modal').classList.add('hidden');
  selection.clear();
  updateBatchBar();
  refresh();
}

el('box-modal-ok').onclick = () => confirmBoxMove().catch((e) => alert(`移动失败：${e.message}`));
el('box-modal-cancel').onclick = () => el('box-modal').classList.add('hidden');
el('box-new-name').addEventListener('input', () => {
  const v = el('box-new-name').value.trim();
  boxModalChoice = v || null;
  renderBoxOptions();
  if (v) el('box-new-name').focus();
});

/* ———————————————— 批量管理 ———————————————— */

function updateBatchBar() {
  el('batch-bar').classList.toggle('hidden', !batchOn);
  el('sel-count').textContent = String(selection.size);
  document.body.classList.toggle('batch-on', batchOn);
}

el('batch-toggle').onclick = () => {
  batchOn = !batchOn;
  if (!batchOn) selection.clear();
  el('batch-toggle').textContent = batchOn ? '退出批量' : '批量管理';
  lastFp = ''; // 强制重绘（勾选框显隐）
  updateBatchBar();
  refresh();
};
el('sel-clear').onclick = () => { selection.clear(); updateBatchBar(); lastFp = ''; refresh(); };
el('sel-move').onclick = () => {
  if (!selection.size) return alert('先勾选账号。');
  openBoxModal([...selection]);
};
el('sel-delete').onclick = async () => {
  if (!selection.size) return;
  if (!confirm(`删除所选 ${selection.size} 个账号？此操作不可撤销。`)) return;
  for (const id of selection) await api(`/api/accounts/${id}`, { method: 'DELETE' });
  selection.clear();
  updateBatchBar();
  refresh();
};

/* ———————————————— 账号卡片（上游 account-card 结构 + 四态徽标） ———————————————— */

function renderCards(accounts) {
  const wall = el('acc-wall');
  wall.innerHTML = '';
  if (!accounts.length) {
    wall.innerHTML = '<li class="empty" style="grid-column:1/-1"><span class="empty-ico">📭</span>暂无账号——先新增环境与账号，或从 env 导入</li>';
    return;
  }
  accounts.forEach((a, i) => {
    const color = colorOf(i);
    const badge = extBadgeOf(a);
    const s = cacheSessions.get(a.id);
    const boxName = (a.box || '').trim();
    const alias = (a.tab_name || '').trim() || a.username;
    const selected = selection.has(a.id);

    const chips = [];
    for (const role of poolList(a.pool)) chips.push(`<span class="chip active">${role === 'config' ? '池·配置' : '池·监听'}</span>`);
    if (savedCache.get(a.id)) chips.push('<span class="chip">已保存登录态</span>');
    if (s && s.has_token) chips.push('<span class="chip">已捕获 token</span>');
    if (s && s.monitoring) chips.push('<span class="chip active">监听中</span>');
    for (const t of (a.tags || []).slice(0, 3)) chips.push(`<span class="chip">${t}</span>`);

    const li = document.createElement('li');
    li.className = `account-card${selected ? ' selected' : ''}`;
    li.dataset.id = a.id;
    li.style.setProperty('--accent', color);
    li.innerHTML = `
      <input type="checkbox" class="ac-check" ${selected ? 'checked' : ''} data-check="${a.id}" />
      <div class="ac-head">
        <div class="ac-avatar" style="--ring:${color}">${a.username.slice(0, 1).toUpperCase()}<span class="dot ${badge.cls === 'online' ? 'on' : ''}"></span></div>
        <div class="meta">
          <div class="alias">${alias}</div>
          <div class="sub">${boxName ? `<span class="box-tag">${boxName}</span>` : ''}${alias !== a.username ? `${a.username} · ` : ''}${a.env_name}${a.env_base_url ? ` · ${a.env_base_url}` : ''}</div>
        </div>
        <span class="badge ${badge.cls}">${badge.label}</span>
      </div>
      ${a.role ? `<div class="sub">${a.role}</div>` : ''}
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
      ${s && (s.title || s.detail) ? `<div class="sub">${s.title || ''}${s.detail ? ` · ${s.detail}` : ''}</div>` : ''}
      <div class="ac-actions">
        <button class="btn-primary" data-act="quicklogin" title="经 quick-login 扩展，在你的 Chrome 中打开并自动登录">快捷登录</button>
        ${s && s.status !== 'stopped'
          ? `<button class="btn-ghost" data-act="focus">聚焦</button>
             <button class="btn-ghost" data-act="close">关闭</button>`
          : `<button class="btn-ghost" data-act="monitor" data-on="${s?.monitoring ? 1 : 0}">监听会话</button>`}
      </div>
      <div class="ac-actions">
        <button class="btn-ghost btn-sm" data-act="edit">编辑</button>
        <button class="btn-ghost btn-sm" data-act="box">移盒</button>
        <button class="btn-ghost btn-sm" data-act="pool" data-role="config">配置池${poolList(a.pool).includes('config') ? ' ✓' : ''}</button>
        <button class="btn-ghost btn-sm" data-act="pool" data-role="monitor">监听池${poolList(a.pool).includes('monitor') ? ' ✓' : ''}</button>
        ${s && s.monitoring ? '<button class="btn-ghost btn-sm" data-act="monitor" data-on="1">停止监听</button>'
          : (s && s.status !== 'stopped' ? '<button class="btn-ghost btn-sm" data-act="monitor" data-on="0">开始监听</button>' : '')}
        ${savedCache.get(a.id) ? '<button class="btn-ghost btn-sm" data-act="forget">忘记会话</button>' : ''}
        <button class="btn-danger btn-sm" data-act="del">删除</button>
      </div>`;

    li.querySelector('[data-check]')?.addEventListener('change', (ev) => {
      const id = ev.target.dataset.check;
      if (ev.target.checked) selection.add(id); else selection.delete(id);
      li.classList.toggle('selected', ev.target.checked);
      updateBatchBar();
    });

    li.querySelectorAll('button[data-act]').forEach((btn) => {
      btn.onclick = async () => {
        const act = btn.dataset.act;
        try {
          if (act === 'quicklogin') {
            btn.disabled = true;
            await quickLogin(a.id);
          } else if (act === 'monitor') {
            await monitorToggle(a.id, btn.dataset.on === '1');
          } else if (act === 'focus') {
            await api(`/api/browser/focus/${a.id}`, { method: 'POST' });
          } else if (act === 'close') {
            await api(`/api/browser/close/${a.id}`, { method: 'POST' });
          } else if (act === 'edit') {
            await editAccount(a.id);
          } else if (act === 'box') {
            openBoxModal([a.id]);
          } else if (act === 'forget') {
            if (!confirm(`清除 ${a.username} 的持久登录态？下次打开将重新走自动登录。`)) return;
            await api(`/api/browser/forget/${a.id}`, { method: 'POST' });
          } else if (act === 'pool') {
            const role = btn.dataset.role;
            const current = poolList(a.pool);
            const next = current.includes(role) ? current.filter((r) => r !== role) : [...current, role];
            await api(`/api/accounts/${a.id}/pool`, { method: 'POST', body: { pool: next } });
          } else if (act === 'del') {
            if (!confirm(`删除账号「${a.username}」？`)) return;
            await api(`/api/accounts/${a.id}`, { method: 'DELETE' });
          }
        } catch (e) {
          alert(`操作失败：${e.message}`);
        } finally {
          refresh();
        }
      };
    });
    wall.appendChild(li);
  });
}

/* ———————————————— 分配池 ———————————————— */

function renderPools() {
  for (const role of ['config', 'monitor']) {
    const box = el(`pool-${role}`);
    const members = cacheAccounts.filter((a) => poolList(a.pool).includes(role));
    if (!members.length) {
      box.innerHTML = '<li class="empty">（空）——在账号卡上点击「池」加入</li>';
      continue;
    }
    box.innerHTML = members.map((a) => {
      const badge = extBadgeOf(a);
      return `<li class="site-row" data-id="${a.id}" title="打开内置会话">
        <span class="ac-avatar" style="--ring:${colorOf(cacheAccounts.indexOf(a))}; width:26px; height:26px; font-size:11px">${a.username.slice(0, 1).toUpperCase()}</span>
        <div class="meta"><div class="alias">${a.username}</div><div class="sub">${a.env_base_url || ''}</div></div>
        <span class="badge ${badge.cls}">${badge.label}</span>
      </li>`;
    }).join('');
    box.querySelectorAll('[data-id]').forEach((node) => {
      node.onclick = () => openAccount(node.dataset.id);
    });
  }
}

async function openAccount(accountId) {
  const head = accountId.slice(0, 6);
  try {
    const result = await api('/api/browser/open', { method: 'POST', body: { account_id: accountId } });
    console.log(`[acc] ${head}: ${result.detail || result.status}`);
  } catch (e) {
    alert(`启动会话失败：${e.message}`);
  }
  refresh();
}

async function monitorToggle(accountId, active) {
  try {
    if (active) {
      await api('/api/monitor/stop', { method: 'POST', body: { account_id: accountId } });
    } else {
      await api('/api/monitor/start', { method: 'POST', body: { account_id: accountId } });
    }
  } catch (e) {
    alert(`监听操作失败：${e.message}`);
  }
  refresh();
}

/* ———————————————— 账号编辑 ———————————————— */

let editTargetId = null;

async function editAccount(accountId) {
  const account = await api(`/api/accounts/${accountId}`);
  editTargetId = accountId;
  const envs = (await api('/api/accounts/envs')).envs;
  const sel = el('edit-env');
  sel.innerHTML = '';
  for (const env of envs) {
    const opt = document.createElement('option');
    opt.value = env.id;
    opt.textContent = `${env.name}${env.base_url ? ` · ${env.base_url}` : ''}`;
    if (env.id === account.env_id) opt.selected = true;
    sel.appendChild(opt);
  }
  el('edit-username').value = account.username;
  el('edit-password').value = '';
  el('edit-tabname').value = account.tab_name || '';
  el('edit-role').value = account.role || '';
  el('edit-box').value = (account.box || '').trim();
  el('edit-tags').value = (account.tags || []).join(',');
  el('edit-dialog').showModal();
}

async function saveEdit() {
  if (!editTargetId) return;
  const body = {
    env_id: el('edit-env').value,
    username: el('edit-username').value.trim(),
    tab_name: el('edit-tabname').value.trim(),
    role: el('edit-role').value.trim(),
    box: el('edit-box').value.trim(),
    tags: el('edit-tags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
  };
  const password = el('edit-password').value;
  if (password) body.password = password;
  await api(`/api/accounts/${editTargetId}`, { method: 'PATCH', body });
  closeDialog(el('edit-dialog'));
  editTargetId = null;
  refresh();
}

/* ———————————————— 批量添加 ———————————————— */

async function bulkAdd() {
  const envId = el('acc-env').value;
  if (!envId) return alert('请先在「站点管理」添加站点');
  const lines = el('acc-bulk').value.split('\n').map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return alert('请粘贴账号行（用户名,密码[,盒子]）');
  const slot = el('bulk-result');
  slot.style.display = 'block';
  slot.innerHTML = '';
  let ok = 0, fail = 0;
  for (const line of lines) {
    const parts = line.split(/[,，]/).map((s) => s.trim());
    if (parts.length < 2) { fail += 1; logLine(slot, `✗ 格式错误（应为 用户名,密码[,盒子]）：${line}`, 'err'); continue; }
    try {
      await api('/api/accounts', {
        method: 'POST',
        body: { env_id: envId, username: parts[0], password: parts[1],
                box: parts[2] || '', tags: ['批量导入'] },
      });
      ok += 1;
      logLine(slot, `✓ ${parts[0]}`, 'ok');
    } catch (e) {
      fail += 1;
      logLine(slot, `✗ ${parts[0]}：${e.message}`, 'err');
    }
  }
  logLine(slot, `■ 批量完成：成功 ${ok}，失败 ${fail}`, ok && !fail ? 'ok' : 'warn');
  refresh();
}

/* ———————————————— 站点管理（链接清洗 + 站点清单，上游 parseSiteInput 语义） ———————————————— */

/** 清洗输入：完整链接 → 站点 origin（scheme://host[:port]，保留端口）；裸域名 → 默认 https */
function cleanSiteInput(raw) {
  const t = (raw || '').trim();
  if (!t) return null;
  try {
    if (/^https?:\/\//i.test(t)) {
      const u = new URL(t);
      if (!u.hostname) return null;
      return { base: u.origin, host: u.hostname };
    }
    if (t.includes('://')) return null; // 非 http(s) 协议不支持
    const host = t.split(/[/?#]/)[0];
    if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(host) && !/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return null;
    return { base: `https://${host}`, host };
  } catch {
    return null;
  }
}

async function addSite(ev) {
  ev.preventDefault();
  const raw = el('s-host').value;
  const cleaned = cleanSiteInput(raw);
  if (!cleaned) return alert('无法识别站点——请粘贴 http(s) 链接或域名。');
  const envs = (await api('/api/accounts/envs')).envs;
  if (envs.some((e) => (e.base_url || '').replace(/\/$/, '') === cleaned.base.replace(/\/$/, ''))) {
    alert(`站点已存在：${cleaned.base}`);
    el('s-host').value = '';
    return;
  }
  await api('/api/accounts/envs', {
    method: 'POST',
    body: { name: cleaned.host, base_url: cleaned.base },
  });
  el('s-host').value = '';
  await loadEnvs();
  refresh();
}

async function loadEnvs() {
  const data = await api('/api/accounts/envs');
  const sel = el('acc-env');
  sel.innerHTML = '';
  for (const env of data.envs) {
    const opt = document.createElement('option');
    opt.value = env.id;
    opt.textContent = `${env.name}${env.base_url ? ` · ${env.base_url}` : ''}（${env.account_count} 账号）`;
    sel.appendChild(opt);
  }
  const box = el('site-list');
  if (!data.envs.length) {
    box.innerHTML = '<li class="empty">暂无站点——粘贴链接或域名添加</li>';
    return;
  }
  box.innerHTML = data.envs.map((env) => `
    <li class="site-row" data-env="${env.id}">
      <span class="ac-avatar" style="--ring:#1E6FFF; width:26px; height:26px; font-size:11px">${(env.name || '?').slice(0, 1).toUpperCase()}</span>
      <div class="meta"><div class="alias">${env.name}</div><div class="sub">${env.base_url || '—'}</div></div>
      <span class="badge ${env.account_count ? 'online' : 'offline'}">${env.account_count} 账号</span>
      <button class="btn-danger btn-sm" data-del-env="${env.id}" data-name="${env.name}" data-count="${env.account_count}">删除</button>
    </li>`).join('');
  box.querySelectorAll('[data-del-env]').forEach((btn) => {
    btn.onclick = async () => {
      const count = Number(btn.dataset.count || 0);
      if (!count || confirm(`删除站点「${btn.dataset.name}」？（其下 ${count} 个账号将一并删除）`)) {
        await api(`/api/accounts/envs/${btn.dataset.delEnv}`, { method: 'DELETE' });
        await loadEnvs();
        refresh();
      }
    };
  });
}

async function addAccount() {
  const env_id = el('acc-env').value;
  const username = el('acc-username').value.trim();
  const password = el('acc-password').value;
  if (!env_id) return alert('请先在「站点管理」添加站点');
  if (!username || !password) return alert('用户名与密码必填');
  const tags = el('acc-tags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  const box = el('acc-box').value.trim();
  await api('/api/accounts', {
    method: 'POST',
    body: {
      env_id, username, password,
      role: el('acc-role').value.trim(), tags,
      ...(box ? { box } : {}),
    },
  });
  el('acc-username').value = '';
  el('acc-password').value = '';
  el('acc-tags').value = '';
  refresh();
}

/* ———————————————— 备份 / env 导入 ———————————————— */

async function exportBackup() {
  const data = await api('/api/accounts/export');
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `akso-backup-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

async function importBackup(file) {
  let backup;
  try {
    backup = JSON.parse(await file.text());
  } catch {
    alert('文件不是有效的 JSON。');
    return;
  }
  if (backup?.format !== 'akso-workbench-backup' || backup.version !== 1) {
    alert('不是 Akso Workbench 备份文件（格式版本不符）。');
    return;
  }
  const total = backup.accounts?.length ?? 0;
  if (!confirm(`导入备份：${total} 个账号。\n同站同名账号将跳过，盒子配置以备份为准。\n⚠ 备份文件内含密钥与加密凭据，请勿外传。继续？`)) return;
  try {
    const result = await api('/api/accounts/import-backup', { method: 'POST', body: backup });
    alert(`导入完成：新建 ${result.created} 个账号，跳过 ${result.skipped} 个。`);
    refresh();
  } catch (e) {
    alert(`导入失败：${e.message}`);
  }
}

async function openImport() {
  const dlg = el('import-dialog');
  const preview = el('import-preview');
  preview.innerHTML = '<div class="sys">扫描中…</div>';
  dlg.showModal();
  try {
    const data = await api('/api/accounts/import/preview');
    preview.innerHTML = '';
    for (const line of data.preview) logLine(preview, line.text, line.cls || '');
  } catch (e) {
    preview.innerHTML = '';
    logLine(preview, `✗ 扫描失败：${e.message}`, 'err');
  }
}

async function runImport() {
  const preview = el('import-preview');
  preview.innerHTML = '';
  try {
    const data = await api('/api/accounts/import', { method: 'POST', body: {} });
    for (const line of data.result) logLine(preview, line.text, line.cls || '');
    refresh();
  } catch (e) {
    logLine(preview, `✗ 导入失败：${e.message}`, 'err');
  }
}

/* 弹窗淡出关闭（微交互） */
function closeDialog(d) {
  if (!d || !d.open) return;
  d.classList.add('closing');
  setTimeout(() => {
    d.classList.remove('closing');
    d.close();
  }, 160);
}

/* ———————————————— 事件绑定 + 启动 ———————————————— */

el('btn-acc-add').onclick = addAccount;
el('btn-acc-bulk').onclick = bulkAdd;
el('btn-acc-bulk-toggle').onclick = () => el('bulk-panel').classList.toggle('hidden');
el('btn-import').onclick = openImport;
el('btn-import-close').onclick = () => closeDialog(el('import-dialog'));
el('btn-import-run').onclick = runImport;
el('btn-edit-cancel').onclick = () => { closeDialog(el('edit-dialog')); editTargetId = null; };
el('btn-edit-save').onclick = () => saveEdit().catch((e) => alert(`保存失败：${e.message}`));
el('site-form').addEventListener('submit', addSite);
document.querySelectorAll('[data-close-dialog]').forEach((btn) => {
  btn.onclick = () => closeDialog(btn.closest('dialog'));
});
el('btn-wheel').onclick = openWheel;
el('btn-export').onclick = () => exportBackup().catch((e) => alert(`导出失败：${e.message}`));
el('btn-import-backup').onclick = () => el('backup-file').click();
el('backup-file').addEventListener('change', () => {
  const file = el('backup-file').files?.[0];
  el('backup-file').value = '';
  if (file) importBackup(file);
});

async function boot() {
  await loadEnvs();
  await refresh();
  try {
    const meta = await fetch('/openapi.json').then((r) => r.json());
    el('ver-chip').textContent = `v${meta.info.version}`;
  } catch { /* 版本号拿不到就不显示 */ }
}

boot().catch((e) => alert(`加载失败：${e.message}`));
setInterval(() => refresh().catch(() => {}), 3000);
updateBatchBar();
