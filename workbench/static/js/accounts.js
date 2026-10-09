/** 账号中心 v2：
 *  视觉与功能基线（盒子 chips 全操作 / 批量管理 / 状态徽标 /
 *  移盒弹窗 / 删盒两步处置 / 顶栏统计 / 指纹防闪烁），数据面接桌面 API，
 *  徽标数据来自**托管浏览器会话**（`/api/browser/sessions`）。
 *
 *  > 2026-10-09：原先并存两条打开账号的路径（内置 Chromium / 用户自己的 Chrome 经浏览器扩展）。
 *  > 扩展已整体移出本仓库（用户决策：桌面端与扩展不耦合，本仓只需账号管理页承接云端数据环境），
 *  > 因此这里删掉了：页内轮盘、`/extension/*` 调用、扩展连接/重载提示条。
 *  > **托管浏览器成为唯一路径**；轮盘窗口视图已一并移除（见 desktop/main.js）。
 */

import { api, el, logLine } from './modules-common.js';

// 账号头像色环语义（与扩展端 SESSION_COLORS 同源）
const COLORS = ['#1E6FFF', '#0FA3B1', '#7C5CFF', '#FF7A1A', '#22C55E'];

let cacheAccounts = [];
let cacheSessions = new Map();
let cacheBoxes = [];
let cacheDisabled = [];

let currentBox = ''; // '' = 全部盒子；DEFAULT_FILTER = 默认盒子独立页；否则命名盒
const DEFAULT_FILTER = '\u0000default'; // 哨兵值：真实盒名不会包含 \u0000
let batchOn = false;
const selection = new Set();
let lastFp = '';

/* ———————————————— 版本角标 + 检查更新（启动检查 / 静默下载 / 退出时安装） ————————————————
 * 角标显示「当前版本」；有新版时上色并显示下载进度（数据来自 /api/update，读的是
 * 桌面壳落盘的 shell-state.json）。点击 = 手工检查更新，结论由桌面壳弹窗落地——
 * 浏览器直开本页（无壳）时退化为 alert 提示，不影响其它功能。 */

function updateVerChip(info) {
  const chip = el('ver-chip');
  const shell = info?.shell || {};
  const version = info?.version || '';
  if (version) chip.textContent = `v${version}`;
  chip.classList.remove('has-update', 'is-ready', 'is-off');
  let title = version ? `Workbench v${version}` : 'Workbench';
  // **自动更新关闭**：这一档必须最先判，而且角标要**当着用户的面说出来**。
  // 以前它落到最后的 else，显示「未检查更新」——看起来像"点我可以检查"，
  // 于是用户点一下、吃到一个「自动更新已关闭」的弹窗，当场问"我不是关了吗"
  // （2026-09-21 实测）。状态要写在角标上，不该靠点击之后才知道。
  const autoUpdateOff = shell.live && shell.autoUpdate === false;
  if (autoUpdateOff) {
    chip.classList.add('is-off');
    chip.disabled = true; // 它不是"检查更新"按钮——点不动，也就不会误触弹窗
    title += '\n自动更新已关闭：应用不会自己检查 / 下载 / 替换版本。'
      + '\n需要更新时，手动运行新下载的安装包即可。'
      + '\n（要开启：设置环境变量 WORKBENCH_AUTO_UPDATE=1 后重启应用）';
  } else if (!shell.live) {
    title += '\n桌面壳未运行：浏览器直接打开本页时无法检查更新（改用托盘菜单「检查更新…」）。';
  } else if (shell.phase === 'ready') {
    chip.classList.add('is-ready');
    title += `\n新版本 v${shell.availableVersion} 已下载完成，退出应用时自动安装。`;
  } else if (shell.phase === 'downloading') {
    chip.classList.add('has-update');
    title += `\n正在后台下载 v${shell.availableVersion}（${shell.percent || 0}%），退出应用时自动安装。`;
  } else if (info?.updateAvailable) {
    chip.classList.add('has-update');
    title += `\n发现新版本 v${shell.availableVersion}。`;
  } else if (shell.phase === 'latest') {
    title += `\n${shell.label || '已是最新版本'}`;
  } else {
    title += `\n${shell.label || '检查更新'}`;
  }
  chip.title = title;
}

el('ver-chip').addEventListener('click', () => {
  void (async () => {
    if (checkingUpdate) return;
    const chip = el('ver-chip');
    checkingUpdate = true;
    try {
      // 先自己确认"自动更新是不是关着的"。`updateVerChip` 会在关闭时把角标置灰，
      // 但**首次 `refresh()` 之前**它还没跑过——那一小段时间里角标仍是可点的，
      // 一次误点照样弹出「自动更新已关闭」框（2026-09-21 实测要修的就是这个意外）。
      const info = await api('/api/update').catch(() => null);
      if (info?.shell?.live && info.shell.autoUpdate === false) {
        updateVerChip(info); // 把状态写回角标（置灰 + 悬停说明），不弹窗
        return;
      }
      chip.disabled = true;
      const r = await api('/api/update/check', { method: 'POST' });
      if (!r || r.ok === false) {
        alert(
          '未能检查更新：桌面壳未响应。\n' +
            '自动更新只在桌面安装版里生效——请用托盘菜单「检查更新…」，或确认桌面应用正在运行。',
        );
      } else {
        // 桌面壳已经把结论（已是最新 / 正在下载 / 失败原因）弹窗告知；这里只刷新角标
        updateVerChip(await api('/api/update').catch(() => null));
      }
    } catch (e) {
      alert(`检查更新失败：${e.message}`);
    } finally {
      checkingUpdate = false;
      // 不在这里无条件 `disabled = false`：自动更新关着时角标本来就该是禁用的，
      // 恢复了就等于把刚修掉的误点入口又打开。交给 `updateVerChip` 按状态决定。
      if (chip.classList.contains('is-off')) chip.disabled = true;
      else chip.disabled = false;
    }
  })();
});



/* ———————————————— 状态徽标（托管浏览器会话） ———————————————— */

function sessionBadgeOf(a) {
  const s = cacheSessions.get(a.id);
  if (s && s.status === 'online') return { cls: 'online', label: '在线 · 内置' };
  if (s && s.status && s.status !== 'stopped') return { cls: 'starting', label: s.status };
  return { cls: 'offline', label: '离线' };
}

/* ———————————————— 数据加载 + 指纹防闪烁渲染 ———————————————— */

async function refresh() {
  const [accData, sessData, boxData, updateInfo] = await Promise.all([
    api('/api/accounts'),
    api('/api/browser/sessions'),
    api('/api/accounts/boxes'),
    // 更新面：读壳落盘的状态（壳没跑时 live=false → 角标只显示版本号）
    api('/api/update').catch(() => null),
  ]);
  cacheAccounts = accData.accounts;
  cacheSessions = new Map(sessData.sessions.map((s) => [s.account_id, s]));
  cacheBoxes = boxData.boxes;
  cacheDisabled = boxData.disabled || accData.disabled_boxes || [];
  updateVerChip(updateInfo);

  renderStats();
  const fp = JSON.stringify([
    cacheAccounts.map((a) => [a.id, a.box, a.pool, a.tags, a.has_password, a.env_name, a.env_base_url, a.username, a.tab_name]),
    [...cacheSessions].map(([k, s]) => [k, s.status, s.has_token, s.monitoring, s.title]),
    cacheBoxes, cacheDisabled,
    currentBox, batchOn, [...selection].sort(),
  ]);
  if (fp === lastFp) return; // 指纹未变不重建 DOM（防闪烁、保留悬停态）
  lastFp = fp;

  renderBoxChips();
  renderPools();
  const visible = currentBox === ''
    ? cacheAccounts
    : currentBox === DEFAULT_FILTER
      ? cacheAccounts.filter((a) => !(a.box || '').trim())
      : cacheAccounts.filter((a) => (a.box || '').trim() === currentBox);
  renderCards(visible);
}

function renderStats() {
  el('stat-accounts').textContent = String(cacheAccounts.length);
  const online = cacheAccounts.filter((a) => sessionBadgeOf(a).cls === 'online').length;
  el('stat-online').textContent = String(online);
  el('stat-boxes').textContent = String(new Set(cacheAccounts.map((a) => (a.box || '').trim())).size);
}

/* ———————————————— 通用文本输入模态（原生 dialog：Electron 焦点管理最稳） ———————————————— */

function askText(title, defaultValue = '') {
  return new Promise((resolve) => {
    const dlg = el('ask-modal');
    const input = el('ask-input');
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      if (dlg.open) dlg.close();
      resolve(value);
    };
    el('ask-title').textContent = title;
    input.value = defaultValue;
    el('ask-ok').onclick = () => done(input.value.trim());
    el('ask-cancel').onclick = () => done(null);
    input.onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); done(input.value.trim()); }
    };
    dlg.onclose = () => done(null); // Esc 原生 cancel/close 兜底
    dlg.showModal();
    input.focus();
    input.select();
  });
}

/* ———————————————— 盒子 chips（悬停操作：✎ 重命名 ⏸/▶ 禁用 ✕ 删除） ———————————————— */

function renderBoxChips() {
  const row = el('box-chips');
  const total = cacheAccounts.length;
  const defaultBox = cacheBoxes.find((b) => b.box === '');
  const named = cacheBoxes.filter((b) => b.box !== '');
  const parts = [`<span class="chip ${currentBox === '' ? 'active' : ''}" data-box="">全部 <span class="chip-n">${total}</span></span>`];
  if (defaultBox) {
    // 默认盒子 = 独立过滤页；判别走 data-default（哨兵 \u0000 不能进 DOM——会被解析器吞掉）
    parts.push(`<span class="chip ${currentBox === DEFAULT_FILTER ? 'active' : ''}" data-default="1">默认盒子 <span class="chip-n">${defaultBox.count}</span><span class="chip-act" data-op="defname">✎</span></span>`);
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
        const name = await askText('新盒子名称：');
        if (!name) return;
        await api('/api/accounts/boxes/create', { method: 'POST', body: { name } });
        currentBox = name;
        refresh();
        return;
      }
      currentBox = chip.dataset.default === '1' ? DEFAULT_FILTER : (chip.dataset.box || '');
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
    const name = await askText('默认盒子的显示名：');
    await api('/api/accounts/boxes/default-name', { method: 'POST', body: { to: name ?? '' } });
    refresh();
    return;
  }
  if (op === 'rename') {
    const to = await askText(`重命名盒子「${displayName}」为：`, box);
    if (!to || to === box) return;
    const result = await api('/api/accounts/boxes/rename', { method: 'POST', body: { from: box, to } });
    if (result.moved > 0) alert(`已移动 ${result.moved} 个账号`); // 空盒重命名静默（用户定稿 0.2.16）
    currentBox = to;
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
    if (withAccounts) alert('盒子与账号已删除');
    else if (result.moved > 0) alert(`已将 ${result.moved} 个账号移入默认盒子`);
    // 空盒删除静默（用户定稿 0.2.16：不再弹"已并入 0 个账号"）
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
  el('box-modal').showModal();
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
  el('box-modal').close();
  selection.clear();
  updateBatchBar();
  refresh();
}

el('box-modal-ok').onclick = () => confirmBoxMove().catch((e) => alert(`移动失败：${e.message}`));
el('box-modal-cancel').onclick = () => el('box-modal').close();
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

/* ———————————————— 账号卡片（上游 account-card 结构 + 会话徽标） ———————————————— */

function renderCards(accounts) {
  const wall = el('acc-wall');
  wall.innerHTML = '';
  if (!accounts.length) {
    wall.innerHTML = '<li class="empty" style="grid-column:1/-1"><span class="empty-ico">📭</span>暂无账号——先新增环境与账号，或从 env 导入</li>';
    return;
  }
  accounts.forEach((a, i) => {
    const color = colorOf(i);
    const badge = sessionBadgeOf(a);
    const s = cacheSessions.get(a.id);
    const boxName = (a.box || '').trim();
    const alias = (a.tab_name || '').trim() || a.username;
    const selected = selection.has(a.id);

    const chips = [];
    for (const role of poolList(a.pool)) if (role === 'config') chips.push('<span class="chip active">池·配置</span>');
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
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
      ${s && (s.title || s.detail) ? `<div class="sub">${s.title || ''}${s.detail ? ` · ${s.detail}` : ''}</div>` : ''}
      <div class="ac-actions">
        ${s && s.status !== 'stopped'
          ? `<button class="btn-ghost" data-act="focus">聚焦</button>
             <button class="btn-ghost" data-act="close">关闭</button>`
          : `<button class="btn-ghost" data-act="monitor" data-on="${s?.monitoring ? 1 : 0}">监听会话</button>`}
      </div>
      <div class="ac-actions">
        <button class="btn-ghost btn-sm" data-act="edit">编辑</button>
        <button class="btn-ghost btn-sm" data-act="box">移盒</button>
        <button class="btn-ghost btn-sm" data-act="pool" data-role="config">配置池${poolList(a.pool).includes('config') ? ' ✓' : ''}</button>
        ${s && s.monitoring ? '<button class="btn-ghost btn-sm" data-act="monitor" data-on="1">停止监听</button>'
          : (s && s.status !== 'stopped' ? '<button class="btn-ghost btn-sm" data-act="monitor" data-on="0">开始监听</button>' : '')}
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
          if (act === 'monitor') {
            await monitorToggle(a.id, btn.dataset.on === '1');
          } else if (act === 'focus') {
            await api(`/api/browser/focus/${a.id}`, { method: 'POST' });
          } else if (act === 'close') {
            await api(`/api/browser/close/${a.id}`, { method: 'POST' });
          } else if (act === 'edit') {
            await editAccount(a.id);
          } else if (act === 'box') {
            openBoxModal([a.id]);
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
  // 监听池已随 Monitor 解耦退役（用户定稿 0.2.14）：仅保留配置池
  const box = el('pool-config');
  const members = cacheAccounts.filter((a) => poolList(a.pool).includes('config'));
  if (!members.length) {
    box.innerHTML = '<li class="empty">（空）——在账号卡上点击「配置池」加入</li>';
    return;
  }
  box.innerHTML = members.map((a) => {
    const badge = sessionBadgeOf(a);
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

/** 站点选项文案：以站点名称为主（用户定稿）；重名时附加域名区分 */
function siteOptionLabel(env, dupNames) {
  return dupNames.has(env.name) ? `${env.name} · ${env.base_url}` : env.name;
}

async function editAccount(accountId) {
  const account = await api(`/api/accounts/${accountId}`);
  editTargetId = accountId;
  const envs = (await api('/api/accounts/envs')).envs;
  const dupNames = new Set(envs.map((e) => e.name).filter((n, i, arr) => arr.indexOf(n) !== i));
  const sel = el('edit-env');
  sel.innerHTML = '';
  for (const env of envs) {
    const opt = document.createElement('option');
    opt.value = env.id;
    opt.textContent = siteOptionLabel(env, dupNames);
    if (env.id === account.env_id) opt.selected = true;
    sel.appendChild(opt);
  }
  el('edit-username').value = account.username;
  el('edit-password').value = '';
  el('edit-tabname').value = account.tab_name || '';
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
    el('s-name').value = '';
    return;
  }
  // 站点名称：留空取域名首段（example-config.example.com → example-config）
  const name = el('s-name').value.trim() || cleaned.host.split('.')[0];
  await api('/api/accounts/envs', {
    method: 'POST',
    body: { name, base_url: cleaned.base },
  });
  el('s-host').value = '';
  el('s-name').value = '';
  await loadEnvs();
  refresh();
}

async function renameSite(envId, currentName) {
  const name = await askText(`重命名站点「${currentName}」为：`, currentName);
  if (!name || name === currentName) return;
  await api(`/api/accounts/envs/${envId}`, { method: 'PATCH', body: { name } });
  await loadEnvs();
  refresh();
}

async function loadEnvs() {
  const data = await api('/api/accounts/envs');
  const dupNames = new Set(data.envs.map((e) => e.name).filter((n, i, arr) => arr.indexOf(n) !== i));
  const sel = el('acc-env');
  sel.innerHTML = '';
  for (const env of data.envs) {
    const opt = document.createElement('option');
    opt.value = env.id;
    opt.textContent = siteOptionLabel(env, dupNames); // 仅站点名称（用户定稿：不带账号数）
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
      <button class="btn-ghost btn-sm" data-rename-env="${env.id}" data-name="${env.name}" title="仅修改站点名称，站点地址不可改">✎</button>
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
  box.querySelectorAll('[data-rename-env]').forEach((btn) => {
    btn.onclick = () => renameSite(btn.dataset.renameEnv, btn.dataset.name).catch((e) => alert(`重命名失败：${e.message}`));
  });
}

async function addAccount() {
  const env_id = el('acc-env').value;
  const username = el('acc-username').value.trim();
  const password = el('acc-password').value;
  if (!env_id) return alert('请先在「站点管理」添加站点');
  if (!username || !password) return alert('用户名与密码必填');
  const box = el('acc-box').value.trim();
  await api('/api/accounts', {
    method: 'POST',
    body: {
      env_id, username, password,
      tab_name: el('acc-tabname').value.trim(),
      ...(box ? { box } : {}),
    },
  });
  el('acc-username').value = '';
  el('acc-password').value = '';
  el('acc-tabname').value = '';
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
  if (backup?.format !== 'workbench-backup' || backup.version !== 1) {
    alert('不是 Workbench 备份文件（格式版本不符）。');
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
    // 版本号来源：优先 /api/update（同时带回更新状态），失败回落 openapi
    const info = await api('/api/update').catch(() => null);
    const meta = info?.version ? null : await fetch('/openapi.json').then((r) => r.json());
    const version = info?.version || meta?.info?.version || '';
    if (version) el('ver-chip').textContent = `v${version}`;
  } catch { /* 版本号拿不到就不显示 */ }
}

boot().catch((e) => alert(`加载失败：${e.message}`));
setInterval(() => refresh().catch(() => {}), 3000);
updateBatchBar();


/* ==================== 云端账号库：登录与身份 ====================
 *
 * 设备流（RFC 8628）：点登录 -> 开系统浏览器 -> 服务端那边登录即自动批准
 * -> 这里按 interval 轮询取会话。**用户一个字都不用抄。**
 *
 * ★ `deviceCode` 只活在这个闭包里：它是换令牌的凭据，**不写进 DOM、不落存储**。
 *   页面能看到的只有给人看的批准页 URL。
 * ★ 取消必须**立刻叫醒等待中的那一拍**（见 wake()）—— 否则用户点了取消
 *   还要傻等一个 interval 才看到反应，那和没反应一样。
 */
(function vaultLogin() {
  const POLL_FALLBACK_MS = 3000;
  const chip = document.getElementById('vault-chip');
  if (!chip) return;
  const av = document.getElementById('vault-avatar');
  const nm = document.getElementById('vault-name');
  const panel = document.getElementById('vault-panel');
  const body = document.getElementById('vault-body');
  const btnCancel = document.getElementById('vault-cancel');
  const btnReopen = document.getElementById('vault-reopen');
  const btnLogout = document.getElementById('vault-logout');

  let deviceCode = null;      // 只在内存里
  let verifyUrl = '';
  let timer = null;
  let cancelled = false;

  const paint = (st) => {
    if (st.loggedIn) {
      const name = st.displayName || st.email || '已登录';
      nm.textContent = name;
      chip.title = `云端账号库 · ${st.email}`;
      if (st.avatar) av.innerHTML = `<img src="${st.avatar}" alt="">`;
      else av.textContent = name.slice(0, 1).toUpperCase();
      btnLogout.classList.remove('hidden');
    } else {
      nm.textContent = '登录云端账号库';
      chip.title = '云端账号库';
      av.textContent = '';
      av.innerHTML = '';
      btnLogout.classList.add('hidden');
    }
  };

  const say = (text, isErr) => {
    body.innerHTML = `<p class="${isErr ? 'vault-err' : 'vault-hint'}"></p>`;
    body.firstChild.textContent = text;
  };

  /* 取消/重试要能**立刻**打断正在等的那一拍 */
  let wake = null;
  const sleep = (ms) => new Promise((res) => {
    timer = setTimeout(() => { timer = null; wake = null; res(); }, ms);
    wake = () => { clearTimeout(timer); timer = null; wake = null; res(); };
  });

  async function status() {
    try {
      const st = await api('/api/vault/status');
      paint(st);
      return st;
    } catch { return { loggedIn: false }; }
  }

  async function start() {
    panel.classList.remove('hidden');
    cancelled = false;
    say('正在向云端申请授权…');
    let info;
    try {
      info = await api('/api/vault/login/start', { method: 'POST', body: { clientName: '桌面工作台' } });
    } catch (e) {
      say(`申请失败：${e.message}`, true);
      return;
    }
    deviceCode = info.deviceCode;
    verifyUrl = info.verificationUrl || '';
    // 打开系统浏览器。桌面壳的 setWindowOpenHandler 会把 http(s) 交给系统浏览器。
    window.open(verifyUrl, '_blank', 'noopener');
    say('已在浏览器打开授权页。请在那边登录 —— 登录后这里会自动完成，不必抄任何码。');
    poll(Math.max(1, Math.round((info.interval || 3))) * 1000, (info.expiresIn || 600) * 1000);
  }

  async function poll(intervalMs, leftMs) {
    const startedAt = Date.now();
    while (!cancelled && deviceCode) {
      if (Date.now() - startedAt > leftMs) {
        await cancel(true);
        say('授权超时了。请重新点一次「登录云端账号库」。', true);
        return;
      }
      await sleep(intervalMs);
      if (cancelled || !deviceCode) return;
      let res;
      try {
        res = await api('/api/vault/login/poll', { method: 'POST', body: { deviceCode } });
      } catch (e) {
        await cancel(true);
        say(`云端不可用：${e.message}`, true);
        return;
      }
      if (res.status === 'pending') continue;
      deviceCode = null;
      if (res.status === 'approved') {
        say('已登录。');
        await status();
        setTimeout(() => panel.classList.add('hidden'), 900);
        return;
      }
      const why = {
        denied: '你在网页上拒绝了这个设备。',
        expired: '授权码已过期，请重新发起。',
        consumed: '这次授权已经被用掉了，请重新发起。',
        unknown: '这次授权已失效，请重新发起。',
      }[res.status] || '授权未完成。';
      say(why, true);
      return;
    }
  }

  async function cancel(silent) {
    cancelled = true;
    if (wake) wake();
    if (timer) { clearTimeout(timer); timer = null; }
    deviceCode = null;
    if (!silent) panel.classList.add('hidden');
    /* ★ 这里**只停本地轮询，不调服务端**。
       设备流里设备**无法自行吊销**自己的码（`device-deny` 是网页端的动作，
       要用户已登录才能调）。所以正确做法是让它自然过期（10 分钟）。
       曾经这里写成再调一次 `login/start` —— 那是**新建**一条授权记录，
       后果是：用户随后在浏览器批准了旧的码，服务端就凭空多出一个
       **没人领取的会话**，要躺满 7 天才失效。 */
  }

  chip.addEventListener('click', async () => {
    const st = await status();
    if (st.loggedIn) { panel.classList.toggle('hidden'); if (!panel.classList.contains('hidden')) say('已登录云端账号库。'); return; }
    start();
  });
  btnReopen.addEventListener('click', () => { if (verifyUrl) window.open(verifyUrl, '_blank', 'noopener'); });
  btnCancel.addEventListener('click', () => cancel(false));
  btnLogout.addEventListener('click', async () => {
    try { await api('/api/vault/logout', { method: 'POST' }); } catch {}
    await status();
    say('已退出云端。');
  });
  window.addEventListener('beforeunload', () => { cancelled = true; if (wake) wake(); });

  status();
})();
