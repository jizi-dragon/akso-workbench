/**
 * Pages Overlay —— 最近配置页轮盘的「页面内浮层」（v3.11）。
 * background 对当前 https 标签页 executeScript 注入本脚本；再次注入 = 关闭（幂等开关）。
 * Shadow DOM 完全隔离宿主页样式；竖排列表（可读性优先于圆环，5 条中文长名称）。
 * 每项：类型徽标 + 主体名·类型 + 来源账号别名；点击 / 数字键 1-5 = 当前标签页跳转
 * （tabId 不变，账号绑定与六平面隔离规则无缝延续）；Esc / 再次快捷键 / 点遮罩关闭。
 */
import type { RecentPageEntry } from '../shared/messages';

const WIN = window as typeof window & { __QL_PAGES_ACTIVE__?: boolean };

/* 再次注入 = 关闭 */
if (WIN.__QL_PAGES_ACTIVE__) {
  const prev = document.getElementById('ql-pages-overlay-host');
  prev?.remove();
  document.documentElement.style.removeProperty('overflow');
  WIN.__QL_PAGES_ACTIVE__ = false;
} else {
  WIN.__QL_PAGES_ACTIVE__ = true;
  void mount();
}

interface PageTypeInfo {
  glyph: string;
  color: string;
}
const TYPE_STYLE: Record<string, PageTypeInfo> = {
  对象工作区: { glyph: ' ◈ ', color: '#1E6FFF' },
  业务菜单: { glyph: ' ☰ ', color: '#0FA3B1' },
  对象配置: { glyph: ' ⚙ ', color: '#7C5CFF' },
  生命周期配置: { glyph: ' ↻ ', color: '#FF7A1A' },
  工作流配置: { glyph: ' ⇉ ', color: '#22C55E' },
};
function typeStyle(t: string): PageTypeInfo {
  return TYPE_STYLE[t] ?? { glyph: ' • ', color: '#64748B' };
}

async function fetchRecent(): Promise<RecentPageEntry[]> {
  try {
    const res = (await chrome.runtime.sendMessage({ kind: 'pages.recent' })) as
      | { kind: 'pages.recent'; result: { ok: boolean; data?: RecentPageEntry[] } }
      | undefined;
    return res?.result?.ok && Array.isArray(res.result.data) ? res.result.data : [];
  } catch {
    return [];
  }
}

function relTime(ts: number): string {
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec} 秒前`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  return `${Math.round(min / 60)} 小时前`;
}

function jump(url: string): void {
  void chrome.runtime.sendMessage({ kind: 'pages.jump', url }).catch(() => undefined);
  close();
}

function close(): void {
  document.getElementById('ql-pages-overlay-host')?.remove();
  document.documentElement.style.removeProperty('overflow');
  WIN.__QL_PAGES_ACTIVE__ = false;
}

async function mount(): Promise<void> {
  const entries = await fetchRecent();

  const host = document.createElement('div');
  host.id = 'ql-pages-overlay-host';
  host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483646;';
  const shadow = host.attachShadow({ mode: 'closed' });

  const style = document.createElement('style');
  style.textContent = `
    .mask { position:absolute; inset:0; background:rgba(8,14,26,.42); display:flex; align-items:center; justify-content:center; font-family:system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif; }
    .panel { width:min(560px, 86vw); max-height:70vh; overflow:auto; background:#0F172AEE; color:#E6EDF7; border:1px solid #FFFFFF22; border-radius:14px; padding:18px 16px 14px; box-shadow:0 18px 60px #000A; }
    .head { display:flex; align-items:baseline; justify-content:space-between; margin-bottom:12px; }
    .title { font-size:15px; font-weight:600; letter-spacing:.02em; }
    .hint { font-size:11px; color:#8FA3BF; }
    .item { display:flex; align-items:center; gap:10px; padding:10px 12px; border:1px solid #FFFFFF14; border-radius:10px; margin-bottom:8px; cursor:pointer; background:#FFFFFF08; transition:background .12s, border-color .12s; }
    .item:hover, .item.kbd-focus { background:#1E6FFF26; border-color:#1E6FFF88; }
    .badge { flex:0 0 auto; font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid #FFFFFF22; color:#CFE0FF; white-space:nowrap; }
    .main { flex:1 1 auto; min-width:0; }
    .subject { font-size:13.5px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .meta { font-size:11px; color:#8FA3BF; margin-top:2px; display:flex; gap:8px; }
    .meta .acct { color:#9FB7DF; }
    .num { flex:0 0 auto; width:20px; height:20px; line-height:20px; text-align:center; border-radius:6px; background:#FFFFFF12; font-size:11px; color:#B9C9E2; }
    .empty { text-align:center; color:#8FA3BF; font-size:13px; padding:26px 0; }
  `;
  shadow.append(style);

  const mask = document.createElement('div');
  mask.className = 'mask';
  const panel = document.createElement('div');
  panel.className = 'panel';
  const head = document.createElement('div');
  head.className = 'head';
  head.innerHTML = `<span class="title">最近配置页</span><span class="hint">点击或按 1-5 跳转 · Esc 关闭</span>`;
  panel.append(head);

  if (!entries.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '暂无记录 —— 打开菜单 / 对象 / 工作流 / 生命周期配置页后自动收集';
    panel.append(empty);
  } else {
    entries.forEach((e, i) => {
      const st = typeStyle(e.pageType);
      const item = document.createElement('div');
      item.className = 'item';
      item.dataset.idx = String(i);
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.style.color = st.color;
      badge.textContent = `${st.glyph}${e.pageType}`;
      const main = document.createElement('div');
      main.className = 'main';
      const subject = document.createElement('div');
      subject.className = 'subject';
      subject.textContent = `${e.subject}·${e.suffix}`;
      const meta = document.createElement('div');
      meta.className = 'meta';
      const acct = document.createElement('span');
      acct.className = 'acct';
      acct.textContent = e.accountAlias ? `👤 ${e.accountAlias}` : '';
      const time = document.createElement('span');
      time.textContent = relTime(e.ts);
      meta.append(acct, time);
      main.append(subject, meta);
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = String(i + 1);
      item.append(badge, main, num);
      item.addEventListener('click', () => jump(e.url));
      panel.append(item);
    });
  }

  mask.append(panel);
  shadow.append(mask);
  document.documentElement.append(host);
  document.documentElement.style.setProperty('overflow', 'hidden');

  mask.addEventListener('click', (ev) => {
    if (ev.target === mask) {
      close();
    }
  });
  const onKey = (ev: KeyboardEvent): void => {
    if (ev.key === 'Escape') {
      close();
      return;
    }
    const n = Number(ev.key);
    if (n >= 1 && n <= entries.length) {
      jump(entries[n - 1].url);
      return;
    }
    if (ev.key.length === 1) {
      const idx = '12345'.indexOf(ev.key);
      if (idx >= 0 && idx < entries.length) {
        jump(entries[idx].url);
      }
    }
  };
  window.addEventListener('keydown', onKey, { capture: true, once: false });
  // 关闭时移除键盘监听（ MutationObserver 不必要：host 移除即失效，这里保守清理）
  const mo = new MutationObserver(() => {
    if (!document.getElementById('ql-pages-overlay-host')) {
      window.removeEventListener('keydown', onKey, { capture: true });
      mo.disconnect();
    }
  });
  mo.observe(document.documentElement, { childList: true });
}
