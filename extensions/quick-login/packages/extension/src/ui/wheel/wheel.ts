/**
 * 账号选择轮盘（扩展弹窗页版本，v3.9 Hub 切盒 + 扇形环）。
 * 由 background 在收到 quick-wheel 命令时以独立 popup 窗口打开——
 * 在浏览器任何位置（含 chrome:// 页、新标签页）都能唤起。
 * 页面内无窗口控制元素；Esc 关闭；数字键 1-9/0 快选；失焦自动关闭（带启动宽限）。
 * 中心翻页：滚轮 / 点击 Hub 循环切换盒子，单环显示当前盒子的账号。
 */
import { LOCAL_KEYS } from '../../shared/constants';
import { DEFAULT_BOX, buildSectorWheel, groupPagesByBox, type WheelAccount } from './wheel-core';

const wheelEl = document.getElementById('wheel') as HTMLDivElement;
const PARALLEL_PAGE = chrome.runtime.getURL('ui/parallel/parallel.html');

/* 启动宽限后再武装失焦关闭（创建瞬间可能误触发 blur） */
let blurArmed = false;
window.setTimeout(() => {
  blurArmed = true;
}, 400);
window.addEventListener('blur', () => {
  if (blurArmed) {
    window.close();
  }
});
void window.focus();

async function fetchAccounts(): Promise<WheelAccount[]> {
  const res = (await chrome.runtime.sendMessage({ kind: 'par.list' })) as
    | { kind: 'par.list'; result: { ok: boolean; data?: WheelAccount[] } }
    | undefined;
  if (!res?.result?.ok || !Array.isArray(res.result.data)) {
    return [];
  }
  // 保持账号固有顺序：盒子内扇区位置稳定，便于肌肉记忆
  return res.result.data;
}

/**
 * 轮盘分页 = 账号归属的盒子（有账号）+ 管理页记忆的空盒子（`ql:boxes`，补到页尾）。
 * 修复：新建的空盒子此前不会出现在轮盘里，导致「切换不到」。
 */
async function fetchPages(): Promise<{ label: string; accounts: WheelAccount[] }[]> {
  const [accounts, remembered, defaultBoxStore, disabledStore] = await Promise.all([
    fetchAccounts(),
    chrome.storage.local
      .get(LOCAL_KEYS.boxList)
      .then((s) => (s[LOCAL_KEYS.boxList] as string[] | undefined) ?? [])
      .catch(() => [] as string[]),
    chrome.storage.local
      .get(LOCAL_KEYS.defaultBox)
      .then((s) => (s[LOCAL_KEYS.defaultBox] as string | undefined)?.trim() ?? '')
      .catch(() => ''),
    chrome.storage.local
      .get(LOCAL_KEYS.disabledBoxes)
      .then((s) => (s[LOCAL_KEYS.disabledBoxes] as string[] | undefined) ?? [])
      .catch(() => [] as string[]),
  ]);
  const disabled = new Set(disabledStore);
  // 禁用盒不进轮盘（即使盒内有账号）；空默认盒自动禁用（groupPagesByBox 只为有未归盒账号的默认盒出页）
  const pages = groupPagesByBox(accounts, defaultBoxStore || DEFAULT_BOX).filter((p) => !disabled.has(p.label));
  const seen = new Set(pages.map((p) => p.label));
  for (const name of remembered) {
    if (!seen.has(name) && name !== defaultBoxStore && !disabled.has(name)) {
      pages.push({ label: name, accounts: [] });
    }
  }
  return pages;
}

async function pick(accountId: string): Promise<void> {
  try {
    await chrome.runtime.sendMessage({ kind: 'par.open', id: accountId });
  } finally {
    window.close();
  }
}

let pages: { label: string; accounts: WheelAccount[] }[] = [];
let pageIndex = 0;
function render(): void {
  buildSectorWheel(wheelEl, {
    pages,
    pageIndex,
    onPage: (delta) => {
      if (!pages.length) {
        return;
      }
      pageIndex = (pageIndex + delta + pages.length) % pages.length;
      render();
      requestAnimationFrame(() => requestAnimationFrame(() => wheelEl.classList.add('in')));
    },
    onPick: (id) => void pick(id),
    emptyAction: {
      label: '去并行管理页添加',
      run: async () => {
        await chrome.tabs.create({ url: PARALLEL_PAGE });
        window.close();
      },
    },
  });
  requestAnimationFrame(() => requestAnimationFrame(() => wheelEl.classList.add('in')));
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.preventDefault();
    window.close();
    return;
  }
  const page = pages[pageIndex];
  if (!page) {
    return;
  }
  const idx = Number(e.key) === 0 ? 9 : Number(e.key) - 1;
  if (!Number.isNaN(idx) && idx >= 0 && idx < 10 && idx < page.accounts.length) {
    void pick(page.accounts[idx].id);
  }
}

/* 盒子/账号/禁用名单变化联动：3s 轮询，指纹未变不重绘（避免打断节点滑移动画） */
let lastFingerprint = '';
function fingerprint(grouped: { label: string; accounts: WheelAccount[] }[]): string {
  return JSON.stringify(grouped.map((p) => [p.label, p.accounts.map((a) => a.id)]));
}
async function refresh(): Promise<void> {
  const grouped = await fetchPages();
  const fp = fingerprint(grouped);
  if (fp === lastFingerprint) {
    return;
  }
  lastFingerprint = fp;
  pages = grouped;
  if (pageIndex >= pages.length) {
    pageIndex = Math.max(0, pages.length - 1);
  }
  render();
  requestAnimationFrame(() => requestAnimationFrame(() => wheelEl.classList.add('in')));
}

void refresh().then(() => {
  document.addEventListener('keydown', onKey);
});
window.setInterval(() => void refresh(), 3000);
