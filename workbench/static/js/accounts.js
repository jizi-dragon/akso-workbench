/** 账号中心：凭据托管 + 托管会话 + 盒子 + 分配池 + 轮盘（quick-login 全量内化） */

import { api, el, logLine, badgeFor } from './modules-common.js';

// quick-login SESSION_COLORS（轮盘色环语义）
const COLORS = ['#1E6FFF', '#0FA3B1', '#7C5CFF', '#FF7A1A', '#22C55E'];
const POOL_LABEL = { config: '配置', monitor: '监听' };
const WHEEL_MAX = 10;
const NS = 'http://www.w3.org/2000/svg';

let cacheAccounts = [];
let cacheSessions = new Map();
let cacheBoxes = [];

/* ———————————————— 轮盘（v3.9 几何移植：扇形 + Hub 切盒 + 半透明覆盖层） ———————————————— */

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

/** 按盒子分页（缺省归默认盒子；保持账号固有顺序） */
function groupPagesByBox(accounts, defaultBoxName) {
  const pages = [];
  const byName = new Map();
  for (const a of accounts) {
    const name = (a.box || '').trim() ? a.box.trim() : (defaultBoxName || '默认盒子');
    if (!byName.has(name)) {
      byName.set(name, []);
      pages.push({ label: name, accounts: byName.get(name) });
    }
    byName.get(name).push(a);
  }
  return pages;
}

function buildSectorWheel(root, { pages, pageIndex, onPick }) {
  root.innerHTML = '';
  const svg = svgEl('svg', { class: 'sector-svg', viewBox: `0 0 ${SIZE} ${SIZE}` });
  root.appendChild(svg);

  // 盒子轨道（右侧 120° 装饰轨道）+ 盒节点
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
    const g = svgEl('g', { class: `sector${isOnline(a) ? ' is-online' : ''}` });
    g.style.setProperty('--acc', colorOf(cacheAccounts.indexOf(a)));
    const hit = svgEl('path', { class: 'sector-hit', d: sectorPath(a0, a1) });
    hit.addEventListener('click', () => onPick(a.id));
    g.appendChild(hit);
    g.appendChild(radialText('sector-label', mid, (R_IN + R_OUT) / 2, truncate(a.username, 8)));
    const num = svgEl('g', { class: 'sector-num' });
    const np = polar(R_IN + 26, mid);
    num.appendChild(svgEl('circle', { cx: np.x, cy: np.y, r: 13 }));
    const nt = svgEl('text', { x: np.x, y: np.y, 'text-anchor': 'middle', 'dominant-baseline': 'central' });
    nt.textContent = String(idx + 1);
    num.appendChild(nt);
    g.appendChild(num);
    const dot = svgEl('circle', { class: `sector-dot${isOnline(a) ? ' on' : ''}`, cx: 0, cy: 0, r: 6 });
    const dp = polar(R_OUT - 12, mid);
    dot.setAttribute('cx', dp.x);
    dot.setAttribute('cy', dp.y);
    g.appendChild(dot);
    svg.appendChild(g);
  });

  // Hub：点击/滚轮切盒
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

function isOnline(account) {
  const s = cacheSessions.get(account.id);
  return Boolean(s && s.status === 'online');
}

function openWheel() {
  if (!cacheAccounts.length) {
    alert('暂无账号——先在下方新增或从原项目 env 导入。');
    return;
  }
  const defaultName = cacheBoxes.find((b) => b.box === '')?.displayName || '默认盒子';
  wheelPages = groupPagesByBox(cacheAccounts, defaultName);
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

function renderWheelOverlay() {
  const defaultName = cacheBoxes.find((b) => b.box === '')?.displayName || '默认盒子';
  wheelPages = groupPagesByBox(cacheAccounts, defaultName);
  if (wheelPage >= wheelPages.length) wheelPage = 0;
  buildSectorWheel(el('wheel-svg'), {
    pages: wheelPages,
    pageIndex: wheelPage,
    onPick: (accountId) => { closeWheel(); openAccount(accountId); },
  });
}

document.addEventListener('keydown', (e) => {
  if (e.key.toLowerCase() === 'q' && (e.altKey)) {
    e.preventDefault();
    if (wheelOpen) closeWheel(); else openWheel();
    return;
  }
  if (!wheelOpen) return;
  if (e.key === 'Escape') { closeWheel(); return; }
  const num = Number(e.key);
  if (num >= 1 && num <= WHEEL_MAX) {
    const page = wheelPages[wheelPage];
    const account = page?.accounts?.[num - 1];
    if (account) { closeWheel(); openAccount(account.id); }
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

/* ———————————————— 轮盘启动 + 卡片墙 + 盒子 + 分配池 ———————————————— */

function colorOf(index) { return COLORS[index % COLORS.length]; }
function poolList(pool) { return String(pool || '').split(',').filter(Boolean); }

async function loadAccountsAndSessions() {
  const [accData, sessData, boxData] = await Promise.all([
    api('/api/accounts'),
    api('/api/browser/sessions'),
    api('/api/accounts/boxes'),
  ]);
  cacheAccounts = accData.accounts;
  cacheSessions = new Map(sessData.sessions.map((s) => [s.account_id, s]));
  cacheBoxes = boxData.boxes;
  renderBoxes(boxData.boxes);
  renderPool(cacheAccounts);
  renderCards(cacheAccounts, cacheSessions, savedCache);
  if (wheelOpen) renderWheelOverlay();
}

let savedCache = new Map();

async function loadSavedFlags(accounts) {
  const entries = await Promise.all(accounts.map(async (a) => {
    const saved = await api(`/api/browser/saved/${a.id}`).catch(() => ({ saved: false }));
    return [a.id, saved.saved];
  }));
  savedCache = new Map(entries);
}

function renderBoxes(boxes) {
  const box = el('box-chips');
  box.innerHTML = boxes.map((b) =>
    `<span class="chip ${b.count ? 'chip-active' : ''}" title="${b.count} 个账号">${b.displayName} · ${b.count}</span>`
  ).join('');
}

function renderPool(accounts) {
  for (const role of ['config', 'monitor']) {
    const box = el(`pool-${role}`);
    const members = accounts.filter((a) => poolList(a.pool).includes(role));
    if (!members.length) {
      box.innerHTML = '<div class="empty">（空）</div>';
      continue;
    }
    box.innerHTML = members.map((a) => {
      const s = cacheSessions.get(a.id);
      const status = s ? s.status : 'stopped';
      return `<div class="legend-item" data-id="${a.id}" title="启动 ${a.username}">
        <span class="legend-dot" style="background:${colorOf(cacheAccounts.indexOf(a))}"></span>
        <span>${a.username}</span>
        <span class="env">${a.env_base_url || ''}</span>
        <span class="status-badge ${badgeFor(status) || ''}">${status}</span>
      </div>`;
    }).join('');
    box.querySelectorAll('[data-id]').forEach((node) => {
      node.onclick = () => openAccount(node.dataset.id);
    });
  }
}

function renderCards(accounts, sessions) {
  const wall = el('acc-wall');
  wall.innerHTML = '';
  if (!accounts.length) {
    wall.innerHTML = '<div class="empty" style="grid-column:1/-1">暂无账号——先新增环境与账号，或从原项目 env 导入</div>';
    return;
  }
  accounts.forEach((a, i) => {
    const color = colorOf(i);
    const s = sessions.get(a.id);
    const status = s ? s.status : 'stopped';
    const badgeCls = badgeFor(status) || (s ? 'warn' : '');
    const chips = [];
    for (const role of poolList(a.pool)) {
      chips.push(`<span class="chip chip-active">池·${POOL_LABEL[role] || role}</span>`);
    }
    if (savedCache.get(a.id)) chips.push('<span class="chip">已保存登录态</span>');
    if (s && s.has_token) chips.push('<span class="chip">已捕获 token</span>');
    const boxName = (a.box || '').trim();
    chips.push(`<span class="chip">盒·${boxName || '默认'}</span>`);
    for (const t of a.tags || []) chips.push(`<span class="chip">${t}</span>`);

    const card = document.createElement('div');
    card.className = 'acard';
    card.innerHTML = `
      <div class="row1">
        <div class="avatar" style="background:${color}; --ql-ring: ${color}55">${a.username.slice(0, 1).toUpperCase()}</div>
        <div>
          <div class="name">${a.username}</div>
          <div class="env">${a.env_name}${a.env_base_url ? ` · ${a.env_base_url}` : ''}</div>
        </div>
        <div style="flex:1"></div>
        <span class="status-badge ${a.has_password ? 'ok' : 'warn'}">${a.has_password ? '凭据就绪' : '无凭据'}</span>
      </div>
      ${a.role ? `<div class="env">${a.role}</div>` : ''}
      ${chips.length ? `<div class="chip-row">${chips.join('')}</div>` : ''}
      ${s && (s.title || s.detail) ? `<div class="env">${s.title || ''}${s.detail ? ` · ${s.detail}` : ''}</div>` : ''}
      <div class="actions">
        ${s && s.status !== 'stopped'
          ? `<button class="mbtn" data-act="focus" data-id="${a.id}" title="把该账号的窗口带到前台">聚焦窗口</button>
             <button class="mbtn ghost" data-act="close" data-id="${a.id}">关闭会话</button>`
          : `<button class="mbtn" data-act="open" data-id="${a.id}">启动会话（可见窗口）</button>`}
        <button class="mbtn ghost" data-act="box" data-id="${a.id}" data-name="${a.username}" data-box="${boxName}">盒子</button>
        <button class="mbtn ghost ${poolList(a.pool).includes('config') ? 'chip-active' : ''}" data-act="pool" data-id="${a.id}" data-role="config"
          title="加入/移出配置池（洞察/工厂取用）">配置池</button>
        <button class="mbtn ghost ${poolList(a.pool).includes('monitor') ? 'chip-active' : ''}" data-act="pool" data-id="${a.id}" data-role="monitor"
          title="加入/移出监听池（Monitor 取用）">监听池</button>
        ${savedCache.get(a.id) ? `<button class="mbtn ghost" data-act="forget" data-id="${a.id}" data-name="${a.username}">忘记会话</button>` : ''}
        <button class="mbtn danger" data-act="del" data-id="${a.id}" data-name="${a.username}">删除</button>
      </div>`;
    card.querySelectorAll('button[data-act]').forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          if (btn.dataset.act === 'open') {
            await openAccount(a.id);
          } else if (btn.dataset.act === 'focus') {
            await api(`/api/browser/focus/${a.id}`, { method: 'POST' });
          } else if (btn.dataset.act === 'close') {
            await api(`/api/browser/close/${a.id}`, { method: 'POST' });
          } else if (btn.dataset.act === 'box') {
            const target = prompt(`将 ${a.username} 移动到盒子（留空 = 默认盒子）：`, btn.dataset.box || '');
            if (target === null) return;
            await api(`/api/accounts/${a.id}`, { method: 'PATCH', body: { box: target.trim() } });
          } else if (btn.dataset.act === 'forget') {
            if (!confirm(`清除 ${a.username} 的持久登录态？下次打开将重新走自动登录。`)) return;
            await api(`/api/browser/forget/${a.id}`, { method: 'POST' });
          } else if (btn.dataset.act === 'pool') {
            const current = poolList(a.pool);
            const role = btn.dataset.role;
            const next = current.includes(role) ? current.filter((r) => r !== role) : [...current, role];
            await api(`/api/accounts/${a.id}/pool`, { method: 'POST', body: { pool: next } });
          } else if (btn.dataset.act === 'del') {
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
    wall.appendChild(card);
  });
}

/* ———————————————— 环境 / 账号 CRUD / 备份 / 导入 ———————————————— */

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
  const box = el('env-list');
  if (!data.envs.length) { box.innerHTML = '<div class="empty">暂无平台环境</div>'; return; }
  box.innerHTML = '<table class="mtable"><thead><tr><th>环境</th><th>baseUrl</th><th>账号数</th><th></th></tr></thead><tbody></tbody></table>';
  const tbody = box.querySelector('tbody');
  for (const env of data.envs) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${env.name}</td><td class="mono">${env.base_url || '—'}</td><td>${env.account_count}</td><td></td>`;
    const del = document.createElement('button');
    del.className = 'mbtn danger';
    del.style.padding = '4px 10px';
    del.textContent = '删除';
    del.onclick = async () => {
      if (!env.account_count || confirm(`删除环境「${env.name}」？（其下 ${env.account_count} 个账号将一并删除）`)) {
        await api(`/api/accounts/envs/${env.id}`, { method: 'DELETE' });
        refresh();
      }
    };
    tr.children[3].appendChild(del);
    tbody.appendChild(tr);
  }
}

async function addEnv() {
  const name = el('env-name').value.trim();
  if (!name) return alert('请填写环境名称');
  await api('/api/accounts/envs', { method: 'POST', body: { name, base_url: el('env-url').value.trim() } });
  el('env-name').value = '';
  el('env-url').value = '';
  refresh();
}

async function addAccount() {
  const env_id = el('acc-env').value;
  const username = el('acc-username').value.trim();
  const password = el('acc-password').value;
  if (!env_id) return alert('请先新增平台环境');
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

async function refresh() {
  const accData = await api('/api/accounts');
  await loadSavedFlags(accData.accounts);
  await loadEnvs();
  await loadAccountsAndSessions();
}

el('btn-env-add').onclick = addEnv;
el('btn-acc-add').onclick = addAccount;
el('btn-import').onclick = openImport;
el('btn-import-close').onclick = () => el('import-dialog').close();
el('btn-import-run').onclick = runImport;
el('btn-wheel').onclick = openWheel;
el('btn-export').onclick = () => exportBackup().catch((e) => alert(`导出失败：${e.message}`));
el('btn-import-backup').onclick = () => el('backup-file').click();
el('backup-file').addEventListener('change', () => {
  const file = el('backup-file').files?.[0];
  el('backup-file').value = '';
  if (file) importBackup(file);
});
el('btn-box-rename').onclick = async () => {
  const from = prompt('要重命名的盒子名：');
  if (!from) return;
  const to = prompt('新盒子名（留空 = 并入默认盒子）：', from) ?? '';
  const result = await api('/api/accounts/boxes/rename', { method: 'POST', body: { from, to } });
  alert(`已移动 ${result.moved} 个账号`);
  refresh();
};
el('btn-box-delete').onclick = async () => {
  const name = prompt('要删除的盒子名（账号将并入默认盒子）：');
  if (!name) return;
  const result = await api('/api/accounts/boxes/delete', { method: 'POST', body: { from: name } });
  alert(`已并入 ${result.moved} 个账号`);
  refresh();
};
el('btn-box-default').onclick = async () => {
  const name = prompt('默认盒子的显示名：', '') ?? '';
  await api('/api/accounts/boxes/default-name', { method: 'POST', body: { to: name } });
  refresh();
};

async function boot() {
  await refresh();
}
boot().catch((e) => alert(`加载失败：${e.message}`));
setInterval(() => loadAccountsAndSessions().catch(() => {}), 3000);
