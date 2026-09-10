import { EXT_VERSION } from '../../shared/constants';

/**
 * 弹窗 = 品牌入口 + 实时统计（v3.8 起；0.2.13 起并行管理页退役，
 * 账号数据由桌面端账号中心统一管理并同步至此）。
 * 轮盘统一走快捷键（Alt+Q）。
 */

document.getElementById('ext-version')!.textContent = `v${EXT_VERSION}`;

/* ---- 实时统计：账号 / 在线 / 盒子（与桌面账号中心同源） ---- */
function setStat(id: string, value: string | number): void {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = String(value);
  }
}

void (async () => {
  try {
    const res = (await chrome.runtime.sendMessage({ kind: 'par.list' })) as
      | { kind: 'par.list'; result: { ok: boolean; data?: Array<{ tabIds?: number[]; box?: string }> } }
      | undefined;
    const accounts = res?.result?.ok && Array.isArray(res.result.data) ? res.result.data : [];
    setStat('stat-accounts', accounts.length);
    setStat('stat-online', accounts.filter((a) => (a.tabIds?.length ?? 0) > 0).length);
    setStat('stat-boxes', new Set(accounts.map((a) => a.box || '')).size);
  } catch {
    setStat('stat-accounts', '—');
    setStat('stat-online', '—');
    setStat('stat-boxes', '—');
  }
})();
