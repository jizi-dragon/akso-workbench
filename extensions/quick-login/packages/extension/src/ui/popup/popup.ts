import { EXT_VERSION } from '../../shared/constants';

/**
 * 弹窗 = 品牌入口 + 实时统计（v3.8 起；0.2.13 起并行管理页退役，
 * 账号数据由桌面端账号中心统一管理并同步至此）。
 * 轮盘统一走快捷键（Alt+Q）。
 */

document.getElementById('ext-version')!.textContent = `v${EXT_VERSION}`;

/* 诊断包导出（v3.12.2 黑匣子语义）：授权/下载问题的一键取证 */
document.getElementById('export-diag')!.addEventListener('click', () => {
  void (async () => {
    const dump = await chrome.storage.local.get(['ql:diag']);
    const blob = new Blob(
      [JSON.stringify({ extVersion: EXT_VERSION, exportedAt: Date.now(), diag: dump['ql:diag'] ?? [] }, null, 2)],
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
    await renderGrantList(accounts.map((a) => a.siteHost || '').filter(Boolean));
  } catch {
    setStat('stat-accounts', '—');
    setStat('stat-online', '—');
    setStat('stat-boxes', '—');
  }
})();

/* ---- 站点授权（0.2.16 恢复）：并行管理页退役后唯一的授权入口 ----
 * 未授权站点 → DNR 规则停用 → 下载等浏览器导航请求缺 Bearer（"无法从网站上提取文件"）。
 * permissions.request 必须挂在用户手势上——弹窗按钮即是。授权后通知 SW 重装规则。 */
async function renderGrantList(hosts: string[]): Promise<void> {
  const list = document.getElementById('grant-list');
  if (!list) {
    return;
  }
  const unique = [...new Set(hosts)];
  if (!unique.length) {
    list.innerHTML = '<li class="grant-row"><span class="grant-empty">暂无站点</span></li>';
    return;
  }
  const rows = await Promise.all(
    unique.map(async (host) => {
      let granted = false;
      try {
        granted = await chrome.permissions.contains({ origins: [`*://${host}/*`] });
      } catch {
        granted = false;
      }
      return { host, granted };
    }),
  );
  list.innerHTML = rows
    .map(
      (r) => `
      <li class="grant-row">
        <span class="grant-host ${r.granted ? 'ok' : 'missing'}">${r.host}</span>
        ${
          r.granted
            ? '<span class="grant-state ok">已授权</span>'
            : `<button class="grant-btn" data-host="${r.host}">授权</button>`
        }
      </li>`,
    )
    .join('');
  list.querySelectorAll('.grant-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      void (async () => {
        const host = (btn as HTMLElement).dataset.host!;
        try {
          const ok = await chrome.permissions.request({ origins: [`*://${host}/*`] });
          if (ok) {
            await chrome.runtime.sendMessage({ kind: 'par.grantChanged' }).catch(() => undefined);
          }
        } catch {
          // 手势缺失等场景：静默
        }
        const hostsNow = (
          (await chrome.runtime.sendMessage({ kind: 'par.list' }).catch(() => undefined)) as
            | { result?: { data?: Array<{ siteHost?: string }> } }
            | undefined
        )?.result?.data?.map((a) => a.siteHost || '') ?? [];
        await renderGrantList(hostsNow);
      })();
    });
  });
}
