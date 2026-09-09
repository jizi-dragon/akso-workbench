import type { ParallelAccount, ParallelAccountStatus } from '../../shared/types';
import type { DataBackup } from '../../shared/messages';
import { EXT_VERSION, LOCAL_KEYS } from '../../shared/constants';
import { send } from '../send';

type BrowserAccount = ParallelAccount & ParallelAccountStatus & { password: boolean };

/* ==================== 元素引用 ==================== */

const verChip = document.getElementById('ver-chip') as HTMLSpanElement;
verChip.textContent = `v${EXT_VERSION}`;

const browserListEl = document.getElementById('browser-list') as HTMLUListElement;
const parForm = document.getElementById('par-form') as HTMLFormElement;
const pHostSelect = document.getElementById('p-host') as HTMLSelectElement;
const pBoxSelect = document.getElementById('p-box') as HTMLSelectElement;
const pTabName = document.getElementById('p-tabname') as HTMLInputElement;
const pUsername = document.getElementById('p-username') as HTMLInputElement;
const pPassword = document.getElementById('p-password') as HTMLInputElement;

const boxChipsEl = document.getElementById('box-chips') as HTMLDivElement;
const batchToggle = document.getElementById('batch-toggle') as HTMLButtonElement;
const batchBar = document.getElementById('batch-bar') as HTMLDivElement;
const selCount = document.getElementById('sel-count') as HTMLElement;
const selClear = document.getElementById('sel-clear') as HTMLButtonElement;
const selMove = document.getElementById('sel-move') as HTMLButtonElement;
const selDelete = document.getElementById('sel-delete') as HTMLButtonElement;

const siteForm = document.getElementById('site-form') as HTMLFormElement;
const sHost = document.getElementById('s-host') as HTMLInputElement;
const siteListEl = document.getElementById('site-list') as HTMLUListElement;

const importToggle = document.getElementById('import-toggle') as HTMLButtonElement;
const importPanel = document.getElementById('import-panel') as HTMLDivElement;
const importText = document.getElementById('import-text') as HTMLTextAreaElement;
const importRun = document.getElementById('import-run') as HTMLButtonElement;
const importCancel = document.getElementById('import-cancel') as HTMLButtonElement;

const dataExportBtn = document.getElementById('data-export') as HTMLButtonElement;
const dataImportBtn = document.getElementById('data-import') as HTMLButtonElement;
const dataImportFile = document.getElementById('data-import-file') as HTMLInputElement;

const boxModal = document.getElementById('box-modal') as HTMLDivElement;
const boxModalList = document.getElementById('box-modal-list') as HTMLDivElement;
const boxModalNew = document.getElementById('box-modal-new') as HTMLInputElement;
const boxModalOk = document.getElementById('box-modal-ok') as HTMLButtonElement;
const boxModalCancel = document.getElementById('box-modal-cancel') as HTMLButtonElement;

/** 有历史记录或站点清单有它时优先预选的默认站点 */
const PREFERRED_HOST = 'example-config.example.com';
const LAST_HOST_KEY = 'ql:lastParHost';
const LAST_BOX_KEY = 'ql:lastParBox';
/** 默认盒子回退名（存储中可自定义，见 loadBoxes） */
const DEFAULT_BOX_FALLBACK = '默认盒子';
/** 默认盒子当前显示名（未归盒账号的归宿；可被用户重命名） */
let defaultBox = DEFAULT_BOX_FALLBACK;

/* ==================== 状态 ==================== */

let browserAccounts: BrowserAccount[] = [];
let grantedHosts: string[] = [];
let blockedHosts: string[] = [];
/** 盒子清单（默认盒子恒在 + storage 记忆 + 账号实际归属） */
let boxes: string[] = [DEFAULT_BOX_FALLBACK];
/** 当前盒子筛选：'全部' 或盒子名 */
let currentBox: string = '全部';
/** 批量模式与选中集 */
let batchMode = false;
const selectedIds = new Set<string>();

/* 防闪烁：数据未变化的轮询不做 DOM 重建 */
let lastListKey = '';
let lastSitesKey = '';
let lastChipsKey = '';
let lastHostOptions = '';
let lastBoxOptions = '';

function setStat(id: string, value: string | number): void {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = String(value);
  }
}

function boxOf(a: ParallelAccount): string {
  return a.box?.trim() || defaultBox;
}

/* ==================== 加载与渲染（diff 防闪烁） ==================== */

async function loadBrowserAccounts(): Promise<void> {
  const res = await send({ kind: 'par.list' });
  browserAccounts =
    res.kind === 'par.list' && res.result.ok
      ? (res.result.data as BrowserAccount[])
      : [];
  // 清掉已删除账号的选中态
  for (const id of Array.from(selectedIds)) {
    if (!browserAccounts.some((a) => a.id === id)) {
      selectedIds.delete(id);
    }
  }

  const key = JSON.stringify([defaultBox, browserAccounts.map((a) => [
      a.id,
      a.tabName,
      a.username,
      a.siteHost,
      a.color,
      a.password,
      a.tabIds,
      a.hasToken,
      a.enforcementOff ?? false,
      a.box ?? '',
    ])]);
  if (key !== lastListKey) {
    lastListKey = key;
    renderBrowserAccounts();
  } else if (batchMode) {
    syncBatchBar();
  }
  setStat('stat-accounts', browserAccounts.length);
  setStat('stat-online', browserAccounts.filter((a) => a.tabIds.length > 0).length);
}

function badgeOf(a: BrowserAccount): { cls: string; text: string } {
  if (a.enforcementOff) {
    return { cls: 'login_failed', text: '未授权 · 已暂停' };
  }
  if (a.hasToken && a.tabIds.length > 0) {
    return { cls: 'online', text: `在线 ×${a.tabIds.length}` };
  }
  if (a.tabIds.length > 0) {
    return { cls: 'starting', text: '待登录' };
  }
  return { cls: 'offline', text: '离线' };
}

function renderBrowserAccounts(): void {
  browserListEl.innerHTML = '';
  browserListEl.classList.toggle('batch-on', batchMode);

  const filtered =
    currentBox === '全部' ? browserAccounts : browserAccounts.filter((a) => boxOf(a) === currentBox);

  if (!filtered.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    const ico = document.createElement('div');
    ico.className = 'empty-ico';
    ico.textContent = '🗂️';
    const tip = document.createElement('div');
    tip.textContent = browserAccounts.length
      ? `盒子「${currentBox}」暂无账号。把其它盒子的账号移入，或在右侧表单直接添加到该盒子。`
      : '还没有并行账号。在下方表单填写并「添加并打开」，密码将以 AES-GCM 加密存放在本机。';
    li.append(ico, tip);
    browserListEl.appendChild(li);
    return;
  }

  for (const a of filtered) {
    const li = document.createElement('li');
    li.className = 'account-card' + (selectedIds.has(a.id) ? ' selected' : '');
    li.style.setProperty('--accent', a.color);
    li.dataset.id = a.id;

    if (batchMode) {
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.className = 'ac-check';
      check.checked = selectedIds.has(a.id);
      check.title = '选择该账号';
      check.addEventListener('change', () => {
        if (check.checked) {
          selectedIds.add(a.id);
        } else {
          selectedIds.delete(a.id);
        }
        li.classList.toggle('selected', check.checked);
        syncBatchBar();
      });
      li.append(check);
    }

    const head = document.createElement('div');
    head.className = 'ac-head';

    const avatar = document.createElement('span');
    avatar.className = 'ac-avatar';
    avatar.style.setProperty('--ring', a.color);
    avatar.textContent = (a.tabName || a.username).trim().charAt(0).toUpperCase() || '?';
    const online = a.tabIds.length > 0;
    const dot = document.createElement('i');
    dot.className = online ? 'dot on' : 'dot';
    avatar.append(dot);

    const meta = document.createElement('div');
    meta.className = 'meta';
    const alias = document.createElement('div');
    alias.className = 'alias';
    alias.textContent = a.tabName || a.username;
    const sub = document.createElement('div');
    sub.className = 'sub';
    const boxTag = document.createElement('span');
    boxTag.className = 'box-tag';
    boxTag.textContent = boxOf(a);
    sub.append(boxTag, `${a.username} · ${a.siteHost}${a.password ? ' · 已存密码' : ''}`);
    meta.append(alias, sub);

    const badge = document.createElement('span');
    const b = badgeOf(a);
    badge.className = `badge ${b.cls}`;
    badge.textContent = b.text;

    head.append(avatar, meta, badge);

    const actions = document.createElement('div');
    actions.className = 'ac-actions';

    const openBtn = document.createElement('button');
    openBtn.className = 'btn-ghost';
    openBtn.textContent = a.tabIds.length ? '聚焦标签页' : '打开';
    openBtn.addEventListener('click', () => void openAccount(a.id, false));

    const newTabBtn = document.createElement('button');
    newTabBtn.className = 'btn-ghost';
    newTabBtn.textContent = '新标签页';
    newTabBtn.addEventListener('click', () => void openAccount(a.id, true));

    const renameBtn = document.createElement('button');
    renameBtn.className = 'btn-ghost';
    renameBtn.textContent = '改页签名';
    renameBtn.addEventListener('click', () => {
      const next = prompt('新的页签名（该账号标签页的标题）', a.tabName || a.username);
      if (next === null) {
        return;
      }
      void (async () => {
        await send({ kind: 'par.update', id: a.id, patch: { tabName: next.trim() || a.username } });
        await refreshAll();
      })();
    });

    const moveBtn = document.createElement('button');
    moveBtn.className = 'btn-ghost';
    moveBtn.textContent = '移入盒子';
    moveBtn.addEventListener('click', () => openBoxChooser([a.id]));

    const del = document.createElement('button');
    del.className = 'btn-danger';
    del.textContent = '删除';
    del.addEventListener('click', () => {
      if (confirm(`删除账号「${a.tabName || a.username}」？将同时关闭其所有并行标签页。`)) {
        void (async () => {
          await send({ kind: 'par.delete', id: a.id });
          await refreshAll();
        })();
      }
    });

    actions.append(openBtn, newTabBtn, renameBtn, moveBtn, del);
    li.append(head, actions);
    browserListEl.appendChild(li);
  }
}

/* ==================== 盒子 ==================== */

let disabledBoxes: string[] = [];

async function loadBoxes(): Promise<void> {
  const stored = await chrome.storage.local.get([LOCAL_KEYS.boxList, LOCAL_KEYS.defaultBox, LOCAL_KEYS.disabledBoxes]);
  const remembered = (stored[LOCAL_KEYS.boxList] as string[] | undefined) ?? [];
  defaultBox = ((stored[LOCAL_KEYS.defaultBox] as string | undefined) ?? '').trim() || DEFAULT_BOX_FALLBACK;
  disabledBoxes = (stored[LOCAL_KEYS.disabledBoxes] as string[] | undefined) ?? [];
  const fromAccounts = browserAccounts.map((a) => boxOf(a));
  boxes = [...new Set([defaultBox, ...remembered.filter((b) => b !== defaultBox), ...fromAccounts])];
  setStat('stat-boxes', boxes.length);
}

async function saveBoxes(): Promise<void> {
  await chrome.storage.local.set({ [LOCAL_KEYS.boxList]: boxes.filter((b) => b !== defaultBox) });
}

async function saveDisabled(): Promise<void> {
  await chrome.storage.local.set({ [LOCAL_KEYS.disabledBoxes]: disabledBoxes });
}

/** 盒子禁用态：手动禁用名单命中，或空默认盒自动禁用（轮盘不出现） */
function isBoxDisabled(name: string): boolean {
  return disabledBoxes.includes(name) || (name === defaultBox && !browserAccounts.some((a) => boxOf(a) === name));
}

function renderBoxChips(): void {
  const key = JSON.stringify([boxes, currentBox, browserAccounts.map((a) => boxOf(a)), batchMode]);
  if (key === lastChipsKey) {
    return;
  }
  lastChipsKey = key;
  boxChipsEl.innerHTML = '';

  const countOf = (name: string) => browserAccounts.filter((a) => boxOf(a) === name).length;

  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'chip' + (currentBox === '全部' ? ' active' : '');
  const allLabel = document.createElement('span');
  allLabel.textContent = '全部';
  const allN = document.createElement('span');
  allN.className = 'chip-n';
  allN.textContent = String(browserAccounts.length);
  all.append(allLabel, allN);
  all.addEventListener('click', () => {
    currentBox = '全部';
    lastListKey = '';
    renderBoxChips();
    renderBrowserAccounts();
  });
  boxChipsEl.append(all);

  for (const name of boxes) {
    const off = isBoxDisabled(name);
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip' + (currentBox === name ? ' active' : '') + (off ? ' chip-off' : '');
    const label = document.createElement('span');
    label.textContent = name;
    const n = document.createElement('span');
    n.className = 'chip-n';
    n.textContent = String(countOf(name));
    chip.append(label, n);
    const rename = document.createElement('span');
    rename.className = 'chip-act';
    rename.textContent = '✎';
    rename.addEventListener('click', (e) => {
      e.stopPropagation();
      void renameBoxFlow(name);
    });
    const toggle = document.createElement('span');
    toggle.className = 'chip-act';
    toggle.textContent = off ? '▶' : '⏸';
    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      void toggleBoxFlow(name);
    });
    if (name === defaultBox) {
      rename.title = '修改默认盒子名称（未归盒账号的归宿）';
      chip.title = off
        ? '默认盒子：当前为空，已自动禁用（轮盘不显示）'
        : '默认盒子：未归盒账号的归宿，可重命名，不可删除';
      chip.append(rename, toggle);
      toggle.title = off ? '默认盒为空时自动禁用' : '默认盒为空时自动禁用（当前有账号）';
      if (off) {
        toggle.style.display = 'none';
      }
    } else {
      rename.title = '重命名盒子（盒内账号随迁）';
      toggle.title = off ? '启用盒子（恢复轮盘切换）' : '禁用盒子（轮盘跳过切换）';
      const remove = document.createElement('span');
      remove.className = 'chip-act chip-act-del';
      remove.textContent = '✕';
      remove.title = '删除盒子（可选：盒内账号一并删除或归入默认盒子）';
      remove.addEventListener('click', (e) => {
        e.stopPropagation();
        void removeBoxFlow(name);
      });
      chip.append(rename, toggle, remove);
    }
    chip.addEventListener('click', () => {
      currentBox = name;
      lastListKey = '';
      renderBoxChips();
      renderBrowserAccounts();
    });
    boxChipsEl.append(chip);
  }

  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'chip chip-add';
  add.textContent = '＋ 新建盒子';
  add.addEventListener('click', () => void createBox());
  boxChipsEl.append(add);
}

async function createBox(): Promise<void> {
  const name = prompt('新盒子名称（账号按盒子收纳，轮盘按盒子翻页）', '');
  const trimmed = name?.trim();
  if (!trimmed) {
    return;
  }
  if (boxes.includes(trimmed)) {
    alert(`盒子「${trimmed}」已存在。`);
    return;
  }
  boxes.push(trimmed);
  await saveBoxes();
  lastChipsKey = '';
  lastBoxOptions = '';
  renderBoxChips();
  fillBoxOptions();
}

/** 盒子重命名：普通盒 = 账号随迁（目标名已存在则并入）；默认盒 = 仅改显示名（未归盒账号语义不变） */
/** 禁用/启用盒子：禁用后轮盘跳过该盒（盒内账号不受影响，管理页照常可用） */
async function toggleBoxFlow(name: string): Promise<void> {
  if (name === defaultBox) {
    return; // 默认盒只随「是否为空」自动禁用
  }
  if (disabledBoxes.includes(name)) {
    disabledBoxes = disabledBoxes.filter((b) => b !== name);
  } else {
    disabledBoxes.push(name);
  }
  await saveDisabled();
  lastChipsKey = '';
  lastListKey = '';
  await refreshAll();
}

async function renameBoxFlow(from: string): Promise<void> {
  const isDefault = from === defaultBox;
  const input = prompt(
    isDefault
      ? `修改默认盒子名称（当前「${from}」）。未归盒账号将显示在新名称下。`
      : `重命名盒子「${from}」（盒内账号随迁）`,
    from,
  );
  const to = input?.trim();
  if (!to || to === from) {
    return;
  }
  if (isDefault) {
    if (boxes.includes(to)) {
      alert(`盒子「${to}」已存在，默认盒子不能与其它盒子重名。`);
      return;
    }
    defaultBox = to;
    await chrome.storage.local.set({ [LOCAL_KEYS.defaultBox]: to });
    boxes = boxes.map((b) => (b === from ? to : b));
    disabledBoxes = disabledBoxes.map((b) => (b === from ? to : b));
    await saveDisabled();
    if (currentBox === from) {
      currentBox = to;
    }
    lastChipsKey = '';
    lastBoxOptions = '';
    lastListKey = '';
    await refreshAll();
    return;
  }
  if (boxes.includes(to) && !confirm(`盒子「${to}」已存在，将把「${from}」中的账号并入其中。继续？`)) {
    return;
  }
  const res = await send({ kind: 'par.renameBox', from, to });
  if (!(res.kind === 'par.renameBox' && res.result.ok)) {
    alert(`重命名失败：${res.kind === 'par.renameBox' && !res.result.ok ? res.result.error : '无响应'}`);
    return;
  }
  boxes = [...new Set(boxes.map((b) => (b === from ? to : b)))];
  disabledBoxes = disabledBoxes.map((b) => (b === from ? to : b));
  await saveBoxes();
  await saveDisabled();
  if (currentBox === from) {
    currentBox = to;
  }
  lastChipsKey = '';
  lastBoxOptions = '';
  lastListKey = '';
  await refreshAll();
}

/**
 * 删除盒子：两步确认——
 * 1) 确认删除盒子本身；
 * 2) 盒内有账号时选择处置：[确定]=连同账号一并删除（含关闭页签），[取消]=账号归入默认盒子。
 */
async function removeBoxFlow(name: string): Promise<void> {
  if (name === defaultBox) {
    return;
  }
  if (!confirm(`删除盒子「${name}」？`)) {
    return;
  }
  const victims = browserAccounts.filter((a) => boxOf(a) === name);
  let deleteAccounts = false;
  if (victims.length) {
    deleteAccounts = confirm(
      `盒子「${name}」中有 ${victims.length} 个账号。\n\n「确定」= 连同账号一并删除（关闭其页签）\n「取消」= 保留账号，移入「${defaultBox}」`,
    );
  }
  if (deleteAccounts) {
    let failed = 0;
    for (const a of victims) {
      const res = await send({ kind: 'par.delete', id: a.id });
      if (!(res.kind === 'par.delete' && res.result.ok)) {
        failed++;
      }
    }
    if (failed) {
      alert(`有 ${failed} 个账号删除失败，盒子已保留，请重试。`);
      return;
    }
  } else {
    const res = await send({ kind: 'par.deleteBox', name });
    if (!(res.kind === 'par.deleteBox' && res.result.ok)) {
      alert(`删除失败：${res.kind === 'par.deleteBox' && !res.result.ok ? res.result.error : '无响应'}`);
      return;
    }
  }
  boxes = boxes.filter((b) => b !== name);
  disabledBoxes = disabledBoxes.filter((b) => b !== name);
  await saveBoxes();
  await saveDisabled();
  if (currentBox === name) {
    currentBox = '全部';
  }
  lastChipsKey = '';
  lastBoxOptions = '';
  lastListKey = '';
  await refreshAll();
}

/* ==================== 数据导出 / 导入（备份文件 v1） ==================== */

dataExportBtn.addEventListener('click', () => {
  void (async () => {
    const res = await send({ kind: 'data.export' });
    if (!(res.kind === 'data.export' && res.result.ok)) {
      alert(`导出失败：${res.kind === 'data.export' && !res.result.ok ? res.result.error : '无响应'}`);
      return;
    }
    const blob = new Blob([JSON.stringify(res.result.data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `quicklogin-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  })();
});

dataImportBtn.addEventListener('click', () => dataImportFile.click());

dataImportFile.addEventListener('change', () => {
  void (async () => {
    const file = dataImportFile.files?.[0];
    dataImportFile.value = ''; // 允许重复选择同一文件
    if (!file) {
      return;
    }
    let backup: DataBackup;
    try {
      backup = JSON.parse(await file.text()) as DataBackup;
    } catch {
      alert('文件不是有效的 JSON。');
      return;
    }
    if (backup?.format !== 'quicklogin-backup' || backup.version !== 1) {
      alert('不是 QuickLogin 备份文件（格式版本不符）。');
      return;
    }
    const total = backup.accounts?.length ?? 0;
    if (!confirm(`导入备份：${total} 个账号、${backup.sites?.length ?? 0} 个授权站点。\n同站同名的账号将跳过，盒子配置以备份为准。继续？`)) {
      return;
    }
    // 站点授权必须趁用户手势仍有效时最先请求（手势会随后续 await 失效，
    // v3.10.1 修复：原顺序导致 request 静默失败 → 永远显示「未授权·已暂停」）
    const hosts = (backup.sites ?? []).map((h) => String(h).trim().toLowerCase()).filter(Boolean);
    let granted = hosts.length === 0;
    if (hosts.length) {
      try {
        granted = await chrome.permissions.request({ origins: hosts.map((h) => `*://${h}/*`) });
      } catch {
        granted = false;
      }
      if (granted) {
        try {
          const stored = await chrome.storage.local.get(LOCAL_KEYS.siteGrants);
          const grants = (stored[LOCAL_KEYS.siteGrants] as Array<{ host: string; grantedAt: number }> | undefined) ?? [];
          const known = new Set(grants.map((g) => g.host.toLowerCase()));
          const now = Date.now();
          for (const h of hosts) {
            if (!known.has(h)) {
              grants.push({ host: h, grantedAt: now });
            }
          }
          await chrome.storage.local.set({ [LOCAL_KEYS.siteGrants]: grants });
        } catch {
          // 清单写入失败不影响已获得的浏览器权限
        }
      }
    }
    const res = await send({ kind: 'data.import', data: backup });
    if (!(res.kind === 'data.import' && res.result.ok)) {
      alert(`导入失败：${res.kind === 'data.import' && !res.result.ok ? res.result.error : '无响应'}`);
      return;
    }
    // 通知后台刷新授权健康缓存（无论授权成败——缓存需与实际权限对齐）
    await send({ kind: 'par.grantChanged' });
    const { created, skipped } = res.result.data;
    alert(
      `导入完成：新建 ${created} 个账号，跳过 ${skipped} 个。` +
        (hosts.length && !granted ? `\n注意：站点授权未完成，账号将显示「未授权·已暂停」——请在「添加授权」中补授站点。` : ''),
    );
    lastChipsKey = '';
    lastBoxOptions = '';
    lastListKey = '';
    await refreshAll();
  })();
});

/* ==================== 批量管理 ==================== */

function syncBatchBar(): void {
  selCount.textContent = String(selectedIds.size);
  batchBar.classList.toggle('hidden', !batchMode);
}

batchToggle.addEventListener('click', () => {
  batchMode = !batchMode;
  batchToggle.textContent = batchMode ? '退出批量' : '批量管理';
  if (!batchMode) {
    selectedIds.clear();
  }
  // 强制重绘：卡片上的勾选框随批量模式增删（进入/退出都要）
  lastListKey = '';
  renderBrowserAccounts();
  syncBatchBar();
  lastChipsKey = '';
  renderBoxChips();
});

selClear.addEventListener('click', () => {
  selectedIds.clear();
  lastListKey = '';
  renderBrowserAccounts();
  syncBatchBar();
});

selMove.addEventListener('click', () => {
  if (!selectedIds.size) {
    return;
  }
  openBoxChooser([...selectedIds]);
});

selDelete.addEventListener('click', () => {
  if (!selectedIds.size) {
    return;
  }
  if (!confirm(`删除选中的 ${selectedIds.size} 个账号？将同时关闭它们的所有并行标签页。`)) {
    return;
  }
  void (async () => {
    for (const id of selectedIds) {
      await send({ kind: 'par.delete', id });
    }
    selectedIds.clear();
    await refreshAll();
  })();
});

/* ==================== 移入盒子：选择弹窗（列出实际存在的盒子 + 可输新名） ==================== */

let pendingMoveIds: string[] = [];
let modalPick = DEFAULT_BOX_FALLBACK;

function openBoxChooser(ids: string[]): void {
  if (!ids.length) {
    return;
  }
  pendingMoveIds = ids;
  const first = browserAccounts.find((a) => a.id === ids[0]);
  modalPick = currentBox !== '全部' ? currentBox : first ? boxOf(first) : DEFAULT_BOX_FALLBACK;
  boxModalNew.value = '';
  renderBoxModalList();
  boxModal.classList.remove('hidden');
}

function renderBoxModalList(): void {
  boxModalList.innerHTML = '';
  const typing = boxModalNew.value.trim();
  for (const name of boxes) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'box-option' + (!typing && modalPick === name ? ' active' : '');
    const label = document.createElement('span');
    label.textContent = name;
    const count = document.createElement('span');
    count.className = 'chip-n';
    count.textContent = String(browserAccounts.filter((a) => boxOf(a) === name).length);
    row.append(label, count);
    row.addEventListener('click', () => {
      modalPick = name;
      boxModalNew.value = '';
      renderBoxModalList();
    });
    boxModalList.append(row);
  }
}

boxModalNew.addEventListener('input', () => {
  const typed = boxModalNew.value.trim();
  if (typed) {
    modalPick = typed; // 输入新盒子名 = 以新名参与选择（确定时自动创建）
    renderBoxModalList();
  } else {
    const first = browserAccounts.find((a) => a.id === pendingMoveIds[0]);
    modalPick = currentBox !== '全部' ? currentBox : first ? boxOf(first) : DEFAULT_BOX_FALLBACK;
    renderBoxModalList();
  }
});

function hideBoxModal(): void {
  boxModal.classList.add('hidden');
  pendingMoveIds = [];
}

boxModalOk.addEventListener('click', () => {
  void (async () => {
    const target = (boxModalNew.value.trim() || modalPick).trim() || defaultBox;
    const ids = [...pendingMoveIds];
    hideBoxModal();
    if (!ids.length) {
      return;
    }
    for (const id of ids) {
      // 移入默认盒 = 清除 box 字段（保持「未归盒 = 默认盒」不变量）
      await send({ kind: 'par.moveBox', id, box: target === defaultBox ? '' : target });
    }
    if (target !== defaultBox && !boxes.includes(target)) {
      boxes.push(target);
      await saveBoxes();
      lastBoxOptions = '';
    }
    if (currentBox !== '全部' && currentBox !== target) {
      currentBox = target;
    }
    selectedIds.clear();
    lastChipsKey = '';
    lastListKey = '';
    await refreshAll();
  })();
});

boxModalCancel.addEventListener('click', hideBoxModal);
boxModal.addEventListener('click', (e) => {
  if (e.target === boxModal) {
    hideBoxModal();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !boxModal.classList.contains('hidden')) {
    hideBoxModal();
  }
});

/* ==================== 批量导入 ==================== */

importToggle.addEventListener('click', () => {
  importPanel.classList.toggle('hidden');
});

importCancel.addEventListener('click', () => {
  importPanel.classList.add('hidden');
});

interface ImportRow {
  tabName: string;
  username: string;
  password: string;
}

function parseImportText(text: string): ImportRow[] {
  const trimmed = text.trim();
  if (!trimmed) {
    return [];
  }
  // 先尝试 JSON 数组：[{"tabName":"…","username":"…","password":"…"}, …]
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (Array.isArray(parsed)) {
      return parsed
        .map((row) => {
          const r = row as Record<string, unknown>;
          return {
            tabName: String(r.tabName ?? r['页签名'] ?? '').trim(),
            username: String(r.username ?? r['账号名'] ?? '').trim(),
            password: String(r.password ?? r['密码'] ?? '').trim(),
          };
        })
        .filter((r) => r.username && r.password);
    }
  } catch {
    // 非 JSON：按 CSV/TSV 行解析
  }
  return trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/[,，\t]/).map((s) => s.trim());
      return { tabName: parts[0] ?? '', username: parts[1] ?? '', password: parts[2] ?? '' };
    })
    .filter((r) => r.username && r.password);
}

importRun.addEventListener('click', () => {
  void (async () => {
    const siteHost = pHostSelect.value;
    if (!siteHost) {
      alert('请先在「站点管理」添加并授权站点。');
      return;
    }
    const box = pBoxSelect.value;
    const rows = parseImportText(importText.value);
    if (!rows.length) {
      alert('没有可导入的行。请检查格式：每行「页签名,账号名,密码」，或 JSON 数组。');
      return;
    }
    let okCount = 0;
    let failCount = 0;
    const scheme = await resolveSchemeForHost(siteHost);
    for (const row of rows) {
      try {
        const res = await send({
          kind: 'par.create',
          siteHost,
          tabName: row.tabName,
          username: row.username,
          password: row.password,
          open: false,
          box,
          scheme,
        });
        if (res.result.ok) {
          okCount += 1;
        } else {
          failCount += 1;
        }
      } catch {
        failCount += 1;
      }
    }
    if (okCount) {
      await chrome.storage.local.set({ [LAST_HOST_KEY]: siteHost });
    }
    importText.value = '';
    importPanel.classList.add('hidden');
    await refreshAll();
    alert(`批量导入完成：成功 ${okCount} 个${failCount ? `，失败 ${failCount} 条（缺账号名或密码）` : ''}。账号已保存到盒子「${box}」，可从列表逐个打开。`);
  })();
});

/* ==================== 添加账号（单个） ==================== */

async function ensureHostPermission(host: string): Promise<boolean> {
  try {
    return await chrome.permissions.request({ origins: [`*://${host}/*`] });
  } catch {
    // 无手势上下文等场景
    return false;
  }
}

/* ==================== scheme（v3.10.9）：协议 hint 与解析 ==================== */

type Scheme = 'http' | 'https';

/** 从用户输入解析 host 与 scheme：支持粘贴完整 URL 或纯 host */
function parseSiteInput(raw: string): { host: string; scheme?: Scheme } {
  const trimmed = raw.trim();
  const m = trimmed.match(/^(https?):\/\/([^/]+)(\/.*)?$/i);
  if (m) {
    return { host: m[2].toLowerCase(), scheme: m[1].toLowerCase() as Scheme };
  }
  return { host: trimmed.replace(/\/.*$/, '').toLowerCase() };
}

async function readSchemeHint(host: string): Promise<Scheme | undefined> {
  const stored = await chrome.storage.local.get(LOCAL_KEYS.siteSchemes);
  const map = (stored[LOCAL_KEYS.siteSchemes] as Record<string, Scheme> | undefined) ?? {};
  return map[host.toLowerCase()];
}

async function writeSchemeHint(host: string, scheme: Scheme): Promise<void> {
  const stored = await chrome.storage.local.get(LOCAL_KEYS.siteSchemes);
  const map = (stored[LOCAL_KEYS.siteSchemes] as Record<string, Scheme> | undefined) ?? {};
  map[host.toLowerCase()] = scheme;
  await chrome.storage.local.set({ [LOCAL_KEYS.siteSchemes]: map });
}

/** 账号创建时确定站点协议：授权时用户输入的 hint 优先，否则 SW 自动探测（https 优先） */
async function resolveSchemeForHost(host: string): Promise<Scheme | undefined> {
  const hint = await readSchemeHint(host);
  if (hint) {
    return hint;
  }
  const res = await send({ kind: 'par.probeScheme', host }).catch(() => null);
  if (res && res.result.ok && (res.result.data === 'http' || res.result.data === 'https')) {
    return res.result.data;
  }
  return undefined;
}

async function openAccount(id: string, forceNewTab: boolean): Promise<void> {
  const account = browserAccounts.find((x) => x.id === id);
  if (!account) {
    return;
  }
  if (!(await ensureHostPermission(account.siteHost))) {
    alert(`需要授权访问 ${account.siteHost} 才能改写其请求头。`);
    return;
  }
  const res = await send({ kind: 'par.open', id, forceNewTab });
  if (!res.result.ok) {
    alert(`打开失败：${res.result.error}`);
    return;
  }
  await refreshAll();
}

async function createAccount(open: boolean): Promise<void> {
  const siteHost = pHostSelect.value;
  if (!siteHost) {
    alert('请先在「站点管理」添加并授权站点，站点下拉才会有可选项。');
    return;
  }
  const tabName = pTabName.value.trim();
  const username = pUsername.value.trim();
  const password = pPassword.value;
  if (!username || !password) {
    return;
  }
  const scheme = await resolveSchemeForHost(siteHost);
  const res = await send({
    kind: 'par.create',
    siteHost,
    tabName,
    username,
    password,
    open,
    box: pBoxSelect.value,
    scheme,
  });
  if (!res.result.ok) {
    alert(`添加失败：${res.result.error}`);
    return;
  }
  // 记住本次选择的站点与盒子，下次打开默认选中
  await chrome.storage.local.set({ [LAST_HOST_KEY]: siteHost, [LAST_BOX_KEY]: pBoxSelect.value });
  pTabName.value = '';
  pUsername.value = '';
  pPassword.value = '';
  await refreshAll();
}

parForm.addEventListener('submit', (e) => {
  e.preventDefault();
  void createAccount(true);
});

document.getElementById('add-only')!.addEventListener('click', () => {
  void createAccount(false);
});

/* ==================== 站点管理（读取真实权限清单） ==================== */

function originToHost(origin: string): string | null {
  // 形如 "*://example-config.example.com/*" 或 "https://*.example.com/*"
  const m = /^(?:\*|https?):\/\/([^/]+)(?:\/.*)?$/.exec(origin);
  if (!m) {
    return null;
  }
  // 跳过全通配行（*://*/* 等）：无法归属到具体站点，单独展示无意义
  return m[1] === '*' ? null : m[1];
}

function patternOfHost(host: string): string {
  return `*://${host}/*`;
}

/** 统计某 host 下已有的并行账号数（支持 *.example.com 通配模式粗匹配） */
function countAccountsFor(displayHost: string): number {
  const bare = displayHost.replace(/^\*\./, '');
  return browserAccounts.filter((a) => a.siteHost === displayHost || a.siteHost.endsWith(`.${bare}`)).length;
}

async function loadSites(): Promise<void> {
  const all = await chrome.permissions.getAll();
  const blockedStored = await chrome.storage.local.get(LOCAL_KEYS.blockedHosts);
  const blocked = new Set<string>((blockedStored[LOCAL_KEYS.blockedHosts] as string[] | undefined) ?? []);

  grantedHosts = Array.from(
    new Set((all.origins ?? []).map(originToHost).filter((h): h is string => h !== null)),
  ).sort();

  // 名单自愈：浏览器层已无授权的停用条目直接移除
  for (const b of Array.from(blocked)) {
    const still = await chrome.permissions.contains({ origins: [`*://${b}/*`] }).catch(() => false);
    if (!still) {
      blocked.delete(b);
    }
  }
  blockedHosts = Array.from(blocked).sort();
  await chrome.storage.local.set({ [LOCAL_KEYS.blockedHosts]: blockedHosts });

  const sitesKey = JSON.stringify([
    grantedHosts,
    blockedHosts,
    browserAccounts.map((a) => [a.id, a.tabName, a.box ?? '']),
  ]);
  if (sitesKey !== lastSitesKey) {
    lastSitesKey = sitesKey;
    renderSites(grantedHosts, blockedHosts);
  }
}

function renderSites(hosts: string[], blocked: string[] = []): void {
  siteListEl.innerHTML = '';
  if (!hosts.length && !blocked.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    const ico = document.createElement('div');
    ico.className = 'empty-ico';
    ico.textContent = '🛡️';
    const tip = document.createElement('div');
    tip.textContent = '尚无授权站点。用上方表单输入 host 添加并授权。';
    li.append(ico, tip);
    siteListEl.appendChild(li);
    return;
  }

  const seen = new Set<string>();
  for (const host of [...hosts, ...blocked]) {
    if (seen.has(host)) {
      continue;
    }
    seen.add(host);
    const isBlocked = !hosts.includes(host) || blocked.includes(host);
    const n = countAccountsFor(host);

    const li = document.createElement('li');
    li.className = 'site-row';

    const meta = document.createElement('div');
    meta.className = 'meta';
    const alias = document.createElement('div');
    alias.className = 'alias';
    alias.textContent = isBlocked ? `${host}（已停用）` : host;
    if (isBlocked) {
      alias.style.color = '#C2410C';
    }
    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = isBlocked
      ? `${n} 个并行账号 · 改头规则已全部暂停`
      : n > 0
        ? `${n} 个并行账号`
        : '该站点暂无并行账号（添加账号后自动更新）';
    meta.append(alias, sub);

    const actionBtn = document.createElement('button');
    actionBtn.className = isBlocked ? 'btn-ghost' : 'btn-danger';
    actionBtn.textContent = isBlocked ? '恢复使用' : '移除授权';
    actionBtn.addEventListener('click', () => {
      if (isBlocked) {
        void unblockHost(host);
        return;
      }
      if (!confirm(`移除对 ${host} 的授权？该站点的鉴权改写将立即失效（浏览器内的登录态不受影响）。`)) {
        return;
      }
      void revokeHost(host);
    });

    li.append(meta, actionBtn);
    siteListEl.appendChild(li);
  }
}

/**
 * 移除某 host 的全部授权（尽力而为）+ 本地停用兜底。
 * Chrome 的 permissions.remove 只能回收「可选授权」，且模式串必须与授予时一致；
 * 若浏览器拒绝（required 权限 / 站点访问模式限制），则把该 host 写入本地停用名单，
 * 后台不再对其安装任何改头规则 —— 效果等价于功能层撤销，且不与 Chrome 的内部状态搏斗。
 */
async function revokeHost(displayHost: string): Promise<void> {
  const all = await chrome.permissions.getAll();
  const raws = (all.origins ?? []).filter((o) => originToHost(o) === displayHost);
  const errors: string[] = [];

  for (const raw of raws) {
    try {
      const okc = await chrome.permissions.remove({ origins: [raw] });
      if (!okc) {
        errors.push(`浏览器拒绝移除：${raw}`);
      }
    } catch (e) {
      errors.push(`${raw} → ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  // 校验真实残留；仍有覆盖（含通配）→ 记入本地停用名单，保证「移除」在功能上必然生效
  const leftover = ((await chrome.permissions.getAll()).origins ?? []).filter(
    (o) => originToHost(o) === displayHost || o.includes('*://*/*'),
  );
  const stillCovers = leftover.length > 0;

  const blockedStored = await chrome.storage.local.get(LOCAL_KEYS.blockedHosts);
  const blocked = new Set<string>((blockedStored[LOCAL_KEYS.blockedHosts] as string[] | undefined) ?? []);

  if (stillCovers) {
    blocked.add(displayHost);
    await chrome.storage.local.set({ [LOCAL_KEYS.blockedHosts]: Array.from(blocked).sort() });
    alert(
      `「${displayHost}」已在 QuickLogin 中停用（改头规则立即失效）。\n\n` +
        `浏览器底层授权未能回收：\n${errors.join('\n') || '(无明细)'}\n\n` +
        `如需彻底回收浏览器层权限：chrome://extensions → 详情 → 网站访问权限 → 手动收窄。`,
    );
  } else if (blocked.has(displayHost)) {
    blocked.delete(displayHost);
    await chrome.storage.local.set({ [LOCAL_KEYS.blockedHosts]: Array.from(blocked).sort() });
  }

  await send({ kind: 'par.grantChanged' });
  await refreshAll();
}

/** 恢复此前手动停用的站点（仅解除 QuickLogin 内部封锁） */
async function unblockHost(host: string): Promise<void> {
  const stored = await chrome.storage.local.get(LOCAL_KEYS.blockedHosts);
  const blocked = new Set<string>((stored[LOCAL_KEYS.blockedHosts] as string[] | undefined) ?? []);
  blocked.delete(host);
  await chrome.storage.local.set({ [LOCAL_KEYS.blockedHosts]: Array.from(blocked).sort() });
  await send({ kind: 'par.grantChanged' });
  await refreshAll();
}

/* ==================== 下拉填充（防闪烁：选项未变化不重建） ==================== */

async function fillSiteOptions(): Promise<void> {
  const key = grantedHosts.join('|');
  if (key === lastHostOptions && pHostSelect.options.length) {
    return;
  }
  lastHostOptions = key;
  pHostSelect.innerHTML = '';

  if (!grantedHosts.length) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.disabled = true;
    empty.selected = true;
    empty.textContent = '（暂无已授权站点 —— 请在「站点管理」添加）';
    pHostSelect.append(empty);
    return;
  }

  for (const h of grantedHosts) {
    const opt = document.createElement('option');
    opt.value = h;
    opt.textContent = h;
    pHostSelect.append(opt);
  }

  const stored = await chrome.storage.local.get(LAST_HOST_KEY);
  const last = stored[LAST_HOST_KEY] as string | undefined;
  const preferred =
    last && grantedHosts.includes(last)
      ? last
      : grantedHosts.includes(PREFERRED_HOST)
        ? PREFERRED_HOST
        : grantedHosts[0];
  pHostSelect.value = preferred;
}

async function fillBoxOptions(): Promise<void> {
  const key = boxes.join('|');
  if (key === lastBoxOptions && pBoxSelect.options.length) {
    return;
  }
  lastBoxOptions = key;
  pBoxSelect.innerHTML = '';
  for (const name of boxes) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    pBoxSelect.append(opt);
  }
  const stored = await chrome.storage.local.get(LAST_BOX_KEY);
  const last = stored[LAST_BOX_KEY] as string | undefined;
  pBoxSelect.value = currentBox !== '全部' && boxes.includes(currentBox) ? currentBox : last && boxes.includes(last) ? last : defaultBox;
}

/* ==================== 站点授权表单 ==================== */

siteForm.addEventListener('submit', (e) => {
  e.preventDefault();
  // v3.10.9：支持粘贴完整 URL——scheme 解析存 hint（账号创建时采用），host 用于授权
  const parsed = parseSiteInput(sHost.value);
  const host = parsed.host;
  if (!host) {
    return;
  }
  void (async () => {
    if (parsed.scheme) {
      await writeSchemeHint(host, parsed.scheme);
    }
    const ok = await chrome.permissions.request({ origins: [patternOfHost(host)] }).catch(() => false);
    if (!ok) {
      alert(`未完成授权：${host}`);
      return;
    }
    sHost.value = '';
    await send({ kind: 'par.grantChanged' });
    await refreshAll();
  })();
});

/* ==================== 统一刷新（轮询与操作后共用；各渲染层自带 diff 防闪烁） ==================== */

async function refreshAll(): Promise<void> {
  // 先取盒子配置（含默认盒显示名），再渲染账号列表，避免首绘用回退名
  await loadBoxes();
  await loadBrowserAccounts();
  await loadSites();
  renderBoxChips();
  await fillSiteOptions();
  await fillBoxOptions();
  syncBatchBar();
}

/* ==================== 启动装载 ==================== */

// 顺序：账号（含统计/卡片）→ 站点 → 盒子 → 下拉填充
void refreshAll();

// 轻量轮询：绑定/在线状态/计数可能被后台事件改变（标签关闭、token 捕获）；
// 各渲染函数自带 diff 守卫，数据未变化时不重建 DOM（消除 3s 轮询闪烁）
setInterval(() => void refreshAll(), 3000);
