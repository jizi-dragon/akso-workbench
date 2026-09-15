import type { RuntimeRequest, RuntimeResponse, Result } from '../shared/messages';
import type { BridgeUpPayload } from '../shared/types';
import { CONTENT_MESSAGE, extVersion } from '../shared/constants';
import { getPendingAutoLogin, registerPendingLoginHandlers } from './core/pending-login';
import {
  forensics,
  handleOpenError,
  invalidateEnforcementCache,
  isSchemeFlipError,
  parallelSession,
  registerParallelHandlers,
  warmEnforcementCache,
} from './core/parallel-session';
import { parallelStore } from './core/parallel-store';
import { tabRules } from './core/tab-rules';

async function diag(msg: string): Promise<void> {
  try {
    const key = 'ql:diag';
    const cur = (await chrome.storage.local.get(key))[key] as string[] | undefined;
    const next = [...(cur ?? []).slice(-59), `${new Date().toISOString().slice(11, 23)} ${msg}`];
    await chrome.storage.local.set({ [key]: next });
  } catch {
    // 埋点失败不影响业务
  }
}

function ok<T>(data: T): Result<T> {
  return { ok: true, data };
}

function fail(error: unknown): Result<never> {
  return { ok: false, error: error instanceof Error ? error.message : String(error) };
}

async function tryRun<T>(fn: () => Promise<T>): Promise<Result<T>> {
  try {
    return ok(await fn());
  } catch (e) {
    return fail(e);
  }
}

async function dispatch(req: RuntimeRequest): Promise<RuntimeResponse> {
  switch (req.kind) {
    case 'site.grants.list':
      // v2.4：旧站点清单入口已移除；保留空实现避免旧调用报 unhandled
      return { kind: 'site.grants.list', result: { ok: true, data: [] } };
    case 'site.grant.add':
      return { kind: 'site.grant.add', result: fail('v2.4 起改为在弹窗/并行页直接授权') };
    case 'par.grantChanged': {
      // 授权增撤后由 UI 通知：刷新授权健康缓存（下轮 par.list 生效）
      invalidateEnforcementCache();
      // 0.2.17：新授权立即重装全部绑定规则——下载等导航请求马上拿到 Bearer
      void (async () => {
        try {
          await parallelSession.reapplyAllRules();
        } catch {
          // 忽略：下轮事件会再同步
        }
      })();
      return { kind: 'par.grantChanged', result: ok(true) };
    }
    case 'ql.diag': {
      // SW 上下文原地诊断（台架取证用）：storage.local 读写 / DNR 安装 / 模块内部状态
      const r = await tryRun(async (): Promise<Record<string, unknown>> => {
        const out: Record<string, unknown> = {};
        try {
          await chrome.storage.local.set({ __qt: Date.now() });
          const v = await chrome.storage.local.get('__qt');
          out.storageWrite = 'OK';
          out.storageRead = Boolean(v['__qt']);
        } catch (e) {
          out.storageErr = e instanceof Error ? e.message : String(e);
        }
        try {
          out.manifestVersion = chrome.runtime.getManifest().version;
        } catch (e) {
          out.manifestErr = e instanceof Error ? e.message : String(e);
        }
        try {
          const rules = await chrome.declarativeNetRequest.getSessionRules();
          out.sessionRuleCount = rules.length;
        } catch (e) {
          out.rulesErr = e instanceof Error ? e.message : String(e);
        }
        try {
          await chrome.declarativeNetRequest.updateSessionRules({
            addRules: [
              {
                id: 777001,
                priority: 1,
                action: {
                  type: 'modifyHeaders',
                  requestHeaders: [{ header: 'Cookie', operation: 'remove' }],
                },
                condition: {
                  resourceTypes: ['main_frame'],
                  requestDomains: ['example-config.example.com'],
                  tabIds: [999999],
                },
              } as chrome.declarativeNetRequest.Rule,
            ],
          });
          await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [777001] });
          out.dnrInSw = 'OK';
        } catch (e) {
          out.dnrInSwErr = e instanceof Error ? e.message : String(e);
        }
        out.parallel = parallelSession.debugState();
        out.tabRules = tabRules.debugState();
        return out;
      });
      return { kind: 'ql.diag', result: r };
    }

    /* ---------------- 浏览器并行账号（纯扩展模式） ---------------- */
    case 'par.list': {
      const r = await tryRun(async () => {
        const list = await parallelStore.list();
        // 预热授权健康缓存（statusOf 同步读取；修复「无绑定账号永远显示离线」）
        await warmEnforcementCache(list.map((a) => a.siteHost));
        return list.map((a) => ({
          ...a,
          ...parallelSession.statusOf(a),
          password: Boolean(a.credentials),
        }));
      });
      return { kind: 'par.list', result: r };
    }
    case 'par.open': {
      const r = await tryRun(() => parallelSession.open(req.id, req.forceNewTab === true));
      return { kind: 'par.open', result: r };
    }
    case 'wheel.toggle': {
      const r = await tryRun(async () => {
        await toggleAccountWheel();
        // toggle 幂等完成后必有可见轮盘面（浮层/小窗/标签页三选一）；
        // 旧实现回传小窗 wheelWinId 状态，与实际主机制（浮层）无关，属失真契约
        return { opened: true };
      });
      return { kind: 'wheel.toggle', result: r };
    }
  }
}

chrome.runtime.onMessage.addListener((req: unknown, sender, sendResponse) => {
  // 0. shield 桥上行：绑定查询 / token 捕获上报（先于通用分流）
  if (
    req &&
    typeof req === 'object' &&
    (req as { type?: string }).type === CONTENT_MESSAGE.bridgeUp
  ) {
    const payload = (req as { payload?: BridgeUpPayload }).payload;
    void parallelSession
      .handleBridge(payload as BridgeUpPayload, sender.tab?.id)
      .then(sendResponse);
    return true;
  }

  // 1. auto-login 内容脚本就绪后主动索取自动登录凭证
  if (
    req &&
    typeof req === 'object' &&
    (req as { type?: string }).type === CONTENT_MESSAGE.autoLoginRequest
  ) {
    const tabId = sender.tab?.id;
    if (tabId === undefined) {
      sendResponse(null);
      return true;
    }
    void getPendingAutoLogin(tabId).then((creds) => sendResponse(creds));
    return true;
  }

  // 1.2 自动填表取证事件（v3.12.2）：填充/点击/让位/被拒逐事件入 forensics 环形缓冲
  if (
    req &&
    typeof req === 'object' &&
    (req as { type?: string }).type === CONTENT_MESSAGE.autoLoginEvent
  ) {
    const p = (req as { event?: Record<string, unknown> }).event ?? {};
    void forensics('autoLogin', { tabId: sender.tab?.id, ...p });
    sendResponse({ ok: true });
    return true;
  }

  // 2. （已移除）旧版本地引擎 NM 桥 —— v2.4 起纯浏览器模式，不再转发引擎指令

  // 3. 普通扩展内部请求
  void dispatch(req as RuntimeRequest).then(sendResponse);
  return true;
});

/* ---------------- 账号选择轮盘（触发源：桌面指令通道） ----------------
 * `toggleAccountWheel` 实现在 background/account-wheel.ts——桌面指令通道（sync.ts）
 * 直调同一函数（SW 自消息不投递给自身上下文，wheel.toggle 曾因此静默丢失）。
 *
 * ⚠️ **扩展不注册任何快捷键，也不能引用 `chrome.commands`**（2026-09-14）：
 * `Alt+Q` 归**桌面壳全局热键独占**。曾经在 manifest 里给 `quick-wheel` 声明
 * `suggested_key: Alt+Q`，并注释「桌面壳运行时 Alt+Q 优先」——这个假设是错的：
 * 用户没开工作台、只有扩展挂在浏览器里时，按 Alt+Q 会被 Chrome 交给扩展，
 * 于是**在浏览器里弹出浏览器版轮盘**（内容与桌面轮盘重复，且此时桌面数据面不可达）。
 *
 * **删除 manifest 的 commands 后必须同时删掉下面这个监听器**：
 * Chrome 在扩展未声明任何命令时**根本不提供 `chrome.commands` 这个 API**（值为 undefined），
 * 所以 `chrome.commands.onCommand.addListener(...)` 会在 **SW 顶层**抛
 * `TypeError: Cannot read properties of undefined (reading 'onCommand')`
 * ——顶层抛出 = 其后所有代码（含 `startDesktopSync()`）永不执行，
 * 表现是「账号不再下发、扩展徽标离线、状态上报全停」这种**看起来完全无关**的静默故障。
 * 这条是实机踩出来的：扩展启动冒烟 B3/B7 都是绿的（SW 进程活着、宿主页零错误），
 * 只有拿 `alarms.getAll()` / `storage.local` 才能发现同步桥压根没启动。
 *
 * 轮盘唯一触发源 = 桌面经 `/extension/commands` 下发 `wheel.toggle`；桌面没运行 = 没有触发源。 */
import { toggleAccountWheel } from './account-wheel';


/** 角标临时显示文本后恢复 */
async function flashBadge(text: string): Promise<void> {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: '#1E6FFF' });
    await chrome.action.setBadgeText({ text });
    window.setTimeout(() => {
      void chrome.action.setBadgeText({ text: '' });
    }, 1200);
  } catch {
    // 角标不可用忽略
  }
}

registerPendingLoginHandlers();
registerParallelHandlers();

// 打开失败自学习（v3.10.9）：绑定页签加载失败时按错误类型翻转协议并原页签重开（并行账号主流程）。
// 旧会话模型（session.*）的兜底路径已随该模型退役（0.2.22）——其绑定表恒空，兜底恒为 no-op。
chrome.webNavigation.onErrorOccurred.addListener((details) => {
  if (details.frameId !== 0 || !isSchemeFlipError(details.error)) {
    return; // 仅主 frame 的 scheme 类导航失败才触发协议翻转
  }
  void (async () => {
    await handleOpenError(details.tabId, details.error);
  })();
});

/* 启动即短显版本号：重新加载扩展后，无需打开任何界面即可确认新代码已生效 */
void flashBadge(`v${extVersion().split('.').slice(0, 2).join('.')}`).finally(() => {
  // flashBadge 自身 1.2s 后清空；这里把启动展示延长为额外一次，共约 2.4s 可见窗口
});

/* 桌面同步桥（Workbench 私有改造）：账号/盒子数据面外移到桌面端，
   本扩展作为执行面每 2s 轮询快照与指令（wheel.toggle/par.open）；桌面不可达时离线回退本地数据 */
import { startDesktopSync } from './sync';
startDesktopSync();

/* ---------------- 下载失败取证 + 单账号站点自动重试（0.2.18） ----------------
 * "无法从网站上提取文件"：下载请求（尤其 tabId=-1 的下载管理器/部分 <a download> 归型）
 * 脱离页签作用域，tab 锁定的 DNR 规则永远罩不住 → 缺 Bearer → 401。
 * 策略：监听下载中断 → 单账号归属判定 → chrome.downloads.download 直接带 Bearer 重发。 */
const downloadUrlById = new Map<number, string>();
const retriedUrls = new Set<string>();

chrome.downloads.onCreated.addListener((item) => {
  downloadUrlById.set(item.id, item.url);
  if (downloadUrlById.size > 200) {
    // 防膨胀：丢弃最早一半
    const keys = [...downloadUrlById.keys()].slice(0, 100);
    for (const k of keys) downloadUrlById.delete(k);
  }
});

chrome.downloads.onChanged.addListener((delta) => {
  const url = downloadUrlById.get(delta.id);
  if (!url) return;
  if (delta.state?.current === 'complete') {
    downloadUrlById.delete(delta.id);
    return;
  }
  if (delta.error || delta.state?.current === 'interrupted') {
    const errDesc = delta.error?.current || delta.state?.current || '?';
    downloadUrlById.delete(delta.id);
    void (async () => {
      if (retriedUrls.has(url)) {
        void diag(`下载重试后仍失败 id=${delta.id} url=${url.slice(0, 140)}`);
        return;
      }
      const auth = await parallelSession.authHeaderForUrl(url);
      if (!auth) {
        void diag(`下载失败（无法单账号归属，不自动重试）id=${delta.id} err=${errDesc} url=${url.slice(0, 150)}`);
        return;
      }
      retriedUrls.add(url);
      try {
        await chrome.downloads.download({ url, headers: [auth] });
        void diag(`下载失败自动重试（已带 Bearer）err=${errDesc} url=${url.slice(0, 140)}`);
      } catch (e) {
        void diag(`下载重试失败 id=${delta.id}：${e instanceof Error ? e.message : String(e)}`);
      }
    })();
  }
});
