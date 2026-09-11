import { extVersion } from '../../shared/constants';

/**
 * 弹窗 = 品牌入口 + 实时统计（v3.8 起；0.2.13 起并行管理页退役，
 * 账号数据由桌面端账号中心统一管理并同步至此）。
 * 轮盘统一走快捷键（Alt+Q）。
 *
 * 0.2.22：站点授权区块整块从 UI 下线（manifest 已声明 `<all_urls>`，装载即获得全站权限，
 * 逐站点授权列表既无操作价值、又让人误以为还要手动授权）。
 * **仅删 UI**——授权/停用名单的核心逻辑（`site-auth.ts`、`par.grantChanged`、
 * `ql:blockedHosts` 与 `isEnforceable`）全部保留不动。
 * 「导出诊断」移入品牌头右侧；版本号移入页脚右下角（值取自 manifest，随项目升版）。
 */

document.getElementById('ext-version')!.textContent = `v${extVersion()}`;

/* 诊断包导出（v3.12.2 黑匣子语义）：授权/下载问题的一键取证 */
document.getElementById('export-diag')!.addEventListener('click', () => {
  void (async () => {
    const dump = await chrome.storage.local.get(['ql:diag']);
    const blob = new Blob(
      [
        JSON.stringify(
          { extVersion: extVersion(), exportedAt: Date.now(), diag: dump['ql:diag'] ?? [] },
          null,
          2,
        ),
      ],
      { type: 'application/json' },
    );
    const url = URL.createObjectURL(blob);
    await chrome.downloads.download({ url, filename: `quicklogin-diag-${Date.now()}.json` });
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  })();
});

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
      | { kind: 'par.list'; result: { ok: boolean; data?: Array<{ tabIds?: number[]; box?: string; siteHost?: string }> } }
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
