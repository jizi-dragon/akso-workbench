/**
 * Wheel Overlay —— 快捷键轮盘的「页面内浮层」主机制（v3.9：Hub 切盒 + 账号色扇区）。
 * background 对当前 https 标签页 executeScript 注入本脚本；再次注入 = 关闭（幂等开关）。
 * Shadow DOM 完全隔离宿主页样式；打开期间锁滚动、聚焦遮罩，数字键不干扰页面输入。
 * 视觉：无边框完整圆形（SVG 环形扇区，单环 = 当前盒子，至多 10 个），外圈细进度弧
 * 指示盒子序位；中心 Hub 显示盒子名与汇总，滚轮 / 点击 Hub 循环切盒。
 * 交互：悬停高亮 → 点击切换；Esc / 再次快捷键 / 点击遮罩关闭。无任何窗口控制元素。
 */
import { LOCAL_KEYS } from '../shared/constants';
import { DEFAULT_BOX, buildSectorWheel, groupPagesByBox, type WheelAccount } from '../ui/wheel/wheel-core';

const WIN = window as typeof window & { __QL_WHEEL_ACTIVE__?: boolean };
const PARALLEL_PAGE = chrome.runtime.getURL('ui/parallel/parallel.html');

/* 再次注入 = 关闭 */
if (WIN.__QL_WHEEL_ACTIVE__) {
  closeExisting();
} else {
  void mount();
}

function closeExisting(): void {
  const prev = document.getElementById('ql-wheel-overlay-host') as HTMLDivElement | null;
  if (prev) {
    prev.dataset.forceClose = '1';
    prev.dispatchEvent(new CustomEvent('ql-wheel-close'));
    prev.remove();
  }
  document.documentElement.style.removeProperty('overflow');
  WIN.__QL_WHEEL_ACTIVE__ = false;
}

async function fetchAccounts(): Promise<WheelAccount[]> {
  const res = (await chrome.runtime.sendMessage({ kind: 'par.list' })) as
    | { kind: 'par.list'; result: { ok: boolean; data?: WheelAccount[] } }
    | undefined;
  return res?.result?.ok && Array.isArray(res.result.data) ? res.result.data : [];
}

/** 轮盘分页 = 账号归属盒子 + 管理页记忆的空盒子（修复「新建空盒子切换不到」） */
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

async function mount(): Promise<void> {
  let pages = await fetchPages();
  let pageIndex = 0;

  /* ---------- 宿主节点与 Shadow 根 ---------- */
  const host = document.createElement('div');
  host.id = 'ql-wheel-overlay-host';
  const shadow = host.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    * { margin:0; padding:0; box-sizing:border-box;
        font-family: system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
    :host {
      position:fixed; inset:0; z-index:2147483646;
      display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px;
      background:rgba(241,245,252,.66);
      backdrop-filter:blur(10px) saturate(1.15);
      opacity:0; transition:opacity .18s ease;
    }
    :host(.show){opacity:1}

    .wheel-root{
      width:min(88vmin,560px);
      transform:scale(.9) translateY(10px);
      transition:transform .28s cubic-bezier(.22,1.3,.36,1), opacity .18s ease;
    }
    :host(.show) .wheel-root{transform:none}
    .wheel-root:not(.in){opacity:.35;transform:scale(.985) rotate(3deg)}

    .sector-wheel svg{
      width:100%; height:auto; display:block;
      filter: drop-shadow(0 26px 60px rgba(23,42,84,.16)) drop-shadow(0 2px 10px rgba(23,42,84,.08));
    }

    .box-track{fill:none;stroke:url(#qlBoxGrad);stroke-width:3.5;stroke-linecap:round}
    .box-node{fill:#b9c4d8;opacity:.55}
    .box-node-on-g{}
    .box-node-on{fill:#1e6fff;stroke:#fff;stroke-width:2;filter:drop-shadow(0 0 6px rgba(30,111,255,.55))}
    .box-node-label{fill:#64748b;font-size:15px;font-weight:600;paint-order:stroke;stroke:#fff;stroke-width:3}

    .sector{cursor:pointer;transform-box:view-box;transform-origin:50% 50%;
      transform:scale(.96);opacity:0;
      transition:transform .34s cubic-bezier(.22,1.2,.36,1),opacity .3s ease;
      transition-delay:var(--d,0s);}
    .sector-wheel.in .sector{opacity:1;transform:scale(1)}
    .sector:hover{transform:scale(1.016);transition-delay:0s}

    .sector-hit{fill:color-mix(in srgb,var(--acc,#1e6fff) 7%,#ffffff);
      stroke:color-mix(in srgb,var(--acc,#1e6fff) 26%,#e4eaf6);stroke-width:1.6;
      transition:fill .16s ease,stroke .16s ease}
    .sector:hover .sector-hit,.sector.hot .sector-hit{
      fill:color-mix(in srgb,var(--acc,#1e6fff) 20%,#ffffff);stroke:var(--acc,#1e6fff)}

    .sector-label{font-size:15px;font-weight:650;fill:#1b2a4a;letter-spacing:.02em;
      pointer-events:none;transition:fill .16s ease}
    .sector:hover .sector-label{fill:var(--acc,#1e6fff)}

    .sector-num circle{fill:#ffffff;stroke:color-mix(in srgb,var(--acc,#1e6fff) 30%,#e4eaf6);
      stroke-width:1.2;transition:stroke .16s ease,fill .16s ease}
    .sector:hover .sector-num circle{fill:var(--acc,#1e6fff);stroke:var(--acc,#1e6fff)}
    .sector-num text{font-size:11px;font-weight:700;fill:#66759b;
      font-variant-numeric:tabular-nums;pointer-events:none;transition:fill .16s ease}
    .sector:hover .sector-num text{fill:#ffffff}

    .sector-dot{fill:#cbd5e1;pointer-events:none;transition:fill .16s ease}
    .sector-dot.on{fill:#22c55e;filter:drop-shadow(0 0 4px rgba(34,197,94,.75));
      animation:ql-pulse 2.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
    @keyframes ql-pulse{0%,100%{opacity:1}50%{opacity:.55}}

    .hub-click{cursor:pointer}
    .hub-bg{fill:#ffffff;stroke:#e4eaf6;stroke-width:1.5;transition:stroke .16s ease}
    .hub-click:hover .hub-bg{stroke:#1e6fff}
    .hub-box{font-size:15px;font-weight:700;fill:#1b2a4a;letter-spacing:.04em}
    .hub-num{font-size:46px;font-weight:800;fill:#1b2a4a;font-variant-numeric:tabular-nums}
    .hub-num .den{font-size:16px;font-weight:600;fill:#66759b;letter-spacing:.04em}
    .hub-sub{font-size:11px;fill:#66759b;letter-spacing:.05em;transition:fill .16s ease}
    .hub-click:hover .hub-sub{fill:#1e6fff}

    .hub-go{cursor:pointer}
    .hub-go-rect{fill:#1e6fff;transition:fill .15s ease}
    .hub-go:hover .hub-go-rect{fill:#185ed6}
    .hub-go-text{fill:#ffffff;font-size:13px;font-weight:600;pointer-events:none}

    .hint{
      display:flex; align-items:center; gap:9px;
      font-size:11.5px; color:#5b6b8f; letter-spacing:.05em;
      background:rgba(255,255,255,.82); border:1px solid rgba(228,234,246,.9);
      padding:6px 14px; border-radius:999px;
      box-shadow:0 2px 10px rgba(23,42,84,.06);
      opacity:0; transform:translateY(6px);
      transition:opacity .3s ease .25s, transform .3s ease .25s;
    }
    :host(.show) .hint{opacity:1;transform:none}
    .hint i{width:3px;height:3px;border-radius:50%;background:#c3cede}
    .hint kbd{display:inline-block;min-width:15px;padding:1.5px 6px;border-radius:5px;
      border:1px solid #d4ddf0;border-bottom-width:2px;background:#fff;color:#5b6b8f;
      font-family:inherit;font-size:10px;text-align:center}
  `;
  shadow.append(style);

  const wheelRoot = document.createElement('div');
  wheelRoot.className = 'wheel-root';

  const hint = document.createElement('div');
  hint.className = 'hint';
  hint.innerHTML =
    '<span><kbd>1-9</kbd> 快速选择</span><i></i><span>滚轮 / 点击中心切盒</span><i></i><span><kbd>Esc</kbd> 关闭</span>';

  shadow.append(wheelRoot, hint);

  let closed = false;
  const refreshTimer = window.setInterval(() => void refresh(), 3000);
  function close(): void {
    if (closed) {
      return;
    }
    closed = true;
    window.clearInterval(refreshTimer);
    document.removeEventListener('keydown', onKeyDown, true);
    document.documentElement.style.removeProperty('overflow');
    host.classList.remove('show');
    WIN.__QL_WHEEL_ACTIVE__ = false;
    window.setTimeout(() => host.remove(), 220);
  }

  function onKeyDown(e: KeyboardEvent): void {
    // 遮罩期间输入框仍可能持有焦点：不劫持编辑键
    const t = e.target as HTMLElement | null;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (e.ctrlKey || e.altKey || e.metaKey) {
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

  async function pick(accountId: string): Promise<void> {
    close();
    try {
      await chrome.runtime.sendMessage({ kind: 'par.open', id: accountId });
    } catch {
      // 忽略
    }
  }

  function render(): void {
    buildSectorWheel(wheelRoot, {
      pages,
      pageIndex,
      onPage: (delta) => {
        if (!pages.length) {
          return;
        }
        pageIndex = (pageIndex + delta + pages.length) % pages.length;
        render();
        requestAnimationFrame(() => requestAnimationFrame(() => wheelRoot.classList.add('in')));
      },
      onPick: (id) => void pick(id),
      emptyAction: {
        label: '去并行管理页添加',
        run: () => {
          close();
          void chrome.tabs.create({ url: PARALLEL_PAGE });
        },
      },
    });
    requestAnimationFrame(() => requestAnimationFrame(() => wheelRoot.classList.add('in')));
  }

  /* ---------- 盒子/账号/禁用名单变化联动：3s 轮询（指纹未变不重绘） ---------- */
  let lastFingerprint = JSON.stringify(pages.map((p) => [p.label, p.accounts.map((a) => a.id)]));
  const refresh = async (): Promise<void> => {
    try {
      const grouped = await fetchPages();
      const fp = JSON.stringify(grouped.map((p) => [p.label, p.accounts.map((a) => a.id)]));
      if (fp === lastFingerprint) {
        return;
      }
      lastFingerprint = fp;
      pages = grouped;
      if (pageIndex >= pages.length) {
        pageIndex = Math.max(0, pages.length - 1);
      }
      render();
    } catch {
      // 轮询失败静默，下个周期重试
    }
  };
  window.setInterval(() => void refresh(), 3000);

  /* ---------- 挂载 & 入场 ---------- */
  document.addEventListener('keydown', onKeyDown, true);
  document.documentElement.style.setProperty('overflow', 'hidden');
  document.documentElement.append(host);
  WIN.__QL_WHEEL_ACTIVE__ = true;
  render();
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      host.classList.add('show');
    }),
  );
}
