import type { RuntimeRequest, RuntimeResponse, Result } from '../shared/messages';
import type { BridgeUpPayload } from '../shared/types';
import { CONTENT_MESSAGE, EXT_VERSION, LOCAL_KEYS } from '../shared/constants';
import { accountRegistry } from './core/account-registry';
import { credentials } from './core/credentials';
import { navigation, registerNavigationHandlers } from './core/navigation';
import { pageMonitor } from './core/page-monitor';
import { siteAuth, probeScheme } from './core/site-auth';
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
import { sessionManager } from './core/session-manager';
import { tabRules } from './core/tab-rules';

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
    case 'session.list':
      return { kind: 'session.list', result: await tryRun(() => sessionManager.list()) };
    case 'session.update': {
      const r = await tryRun(() => sessionManager.update(req.id, req.patch));
      if (r.ok) {
        accountRegistry.invalidate(req.id);
      }
      return { kind: 'session.update', result: r };
    }
    case 'session.delete': {
      const r = await tryRun(() => sessionManager.delete(req.id));
      accountRegistry.invalidate(req.id);
      return { kind: 'session.delete', result: r };
    }
    case 'session.open': {
      const r = await tryRun(async () => {
        const session = await sessionManager.getOrThrow(req.id);
        let creds: { username: string; password: string } | undefined;
        if (session.credentials) {
          creds = await credentials.decryptCredentials(session.credentials);
        }
        const { tabId } = await navigation.switchAccount(session, creds);
        return { tabId };
      });
      return { kind: 'session.open', result: r };
    }
    case 'session.openOrCreate': {
      const r = await tryRun(async () => {
        const all = await sessionManager.list();
        const byHost = all.filter((s) => s.siteHost === req.host);

        let session: Awaited<ReturnType<typeof sessionManager.get>>;
        if (req.accountAlias) {
          // 显式指定账号：精确匹配该账号（标签标题）的既有会话，否则视为新账号
          session = byHost.find((s) => (s.accountAlias || s.name) === req.accountAlias);
        } else {
          // 快捷打开（未指定账号）：复用该 host 最近更新的会话
          session = byHost.sort((a, b) => b.updatedAt - a.updatedAt)[0];
        }

        if (!session) {
          session = await sessionManager.create({
            name: req.accountAlias || req.username || req.host,
            accountAlias: req.accountAlias || req.username || req.host,
            siteHost: req.host,
          });
        }

        // 本次带入了明文账号密码：加密持久化，并作为本次自动登录凭证
        let creds: { username: string; password: string } | undefined;
        if (req.username && req.password) {
          await sessionManager.updateCredentials(
            session.id,
            await credentials.encryptCredentials(req.username, req.password),
          );
          creds = { username: req.username, password: req.password };
        } else if (session.credentials) {
          creds = await credentials.decryptCredentials(session.credentials);
        }

        const { tabId, reused } = await navigation.switchAccount(session, creds);
        return { tabId, sessionId: session.id, reused };
      });
      return { kind: 'session.openOrCreate', result: r };
    }
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
    case 'par.create': {
      const r = await tryRun(async () => {
        const account = await parallelStore.create({
          siteHost: req.siteHost,
          tabName: req.tabName,
          username: req.username,
          password: req.password,
          box: req.box,
          scheme: req.scheme,
        });
        if (req.open) {
          await parallelSession.open(account.id, false);
        }
        return account;
      });
      return { kind: 'par.create', result: r };
    }
    case 'par.probeScheme': {
      const r = await tryRun(() => probeScheme(req.host));
      return { kind: 'par.probeScheme', result: r };
    }
    case 'par.moveBox': {
      const r = await tryRun(() => parallelStore.updateBox(req.id, req.box));
      return { kind: 'par.moveBox', result: r };
    }
    case 'par.renameBox': {
      const r = await tryRun(async () => ({ moved: await parallelStore.renameBox(req.from, req.to) }));
      return { kind: 'par.renameBox', result: r };
    }
    case 'par.deleteBox': {
      const r = await tryRun(async () => ({ moved: await parallelStore.clearBox(req.name) }));
      return { kind: 'par.deleteBox', result: r };
    }
    case 'par.update': {
      const r = await tryRun(async () => {
        const account = await parallelStore.updateTabName(req.id, req.patch.tabName ?? '');
        await parallelSession.refreshTitle(req.id);
        return account;
      });
      return { kind: 'par.update', result: r };
    }
    case 'par.delete': {
      const r = await tryRun(() => parallelSession.deleteAccount(req.id));
      return { kind: 'par.delete', result: r };
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
    case 'data.export': {
      const r = await tryRun(async () => {
        const [accounts, grants, stored] = await Promise.all([
          parallelStore.list(),
          siteAuth.list(),
          chrome.storage.local.get([LOCAL_KEYS.boxList, LOCAL_KEYS.defaultBox, LOCAL_KEYS.disabledBoxes]),
        ]);
        return {
          format: 'quicklogin-backup' as const,
          version: 1 as const,
          exportedAt: new Date().toISOString(),
          cryptoSeed: await credentials.getKeySeed(),
          sites: grants.map((g) => g.host),
          boxes: {
            default: (stored[LOCAL_KEYS.defaultBox] as string | undefined)?.trim() || undefined,
            remembered: (stored[LOCAL_KEYS.boxList] as string[] | undefined) ?? [],
            disabled: (stored[LOCAL_KEYS.disabledBoxes] as string[] | undefined) ?? [],
          },
          accounts: accounts.map((a) => ({
            siteHost: a.siteHost,
            tabName: a.tabName,
            box: a.box,
            credentials: a.credentials ?? null,
          })),
        };
      });
      return { kind: 'data.export', result: r };
    }
    case 'data.import': {
      const r = await tryRun(async () => {
        const data = req.data;
        if (data?.format !== 'quicklogin-backup' || data.version !== 1) {
          throw new Error('不是有效的 QuickLogin 备份文件（format/version 不符）');
        }
        if (!data.cryptoSeed || !Array.isArray(data.accounts)) {
          throw new Error('备份缺少加密种子或账号清单');
        }
        const fileKey = await credentials.deriveKey(data.cryptoSeed);
        let created = 0;
        let skipped = 0;
        for (const item of data.accounts) {
          if (!item?.siteHost || !item.credentials) {
            skipped++;
            continue;
          }
          let username: string;
          let password: string;
          try {
            username = await credentials.decryptValue(item.credentials.encryptedUsername, item.credentials.iv, fileKey);
            password = await credentials.decryptValue(
              item.credentials.encryptedPassword,
              item.credentials.ivPassword,
              fileKey,
            );
          } catch {
            skipped++; // 凭证无法用文件种子解开（文件损坏/被篡改）
            continue;
          }
          const all = await parallelStore.list();
          if (all.some((x) => x.siteHost === item.siteHost && x.username === username)) {
            skipped++; // 同站同名账号已存在
            continue;
          }
          await parallelStore.create({
            siteHost: item.siteHost,
            tabName: item.tabName || username,
            username,
            password,
            box: item.box || undefined,
          });
          created++;
        }
        // 盒子配置：恢复备份语义 = 以文件为准覆盖（记住盒/默认盒名/禁用名单）
        if (data.boxes) {
          const patch: Record<string, unknown> = {};
          if (Array.isArray(data.boxes.remembered)) {
            patch[LOCAL_KEYS.boxList] = data.boxes.remembered;
          }
          if (data.boxes.default?.trim()) {
            patch[LOCAL_KEYS.defaultBox] = data.boxes.default.trim();
          }
          if (Array.isArray(data.boxes.disabled)) {
            patch[LOCAL_KEYS.disabledBoxes] = data.boxes.disabled;
          }
          if (Object.keys(patch).length) {
            await chrome.storage.local.set(patch);
          }
        }
        return { created, skipped, hosts: Array.isArray(data.sites) ? data.sites : [] };
      });
      return { kind: 'data.import', result: r };
    }
    case 'pages.recent':
      // 实际处理在 onMessage（需要 sender.tab 定位 host）；此处仅满足穷尽性
      return { kind: 'pages.recent', result: ok([]) };
    case 'pages.jump':
      return { kind: 'pages.jump', result: ok({ jumped: false }) };
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
    void navigation.getPendingAutoLogin(tabId).then((creds) => sendResponse(creds));
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

  // 1.5 v3.11 最近配置页：需要 sender.tab，先于通用分流处理
  // 1.8 最近配置页（v3.13 收敛：仅绑定页签有数据，未绑定页签返回空——根本原则）
  if (req && typeof req === 'object' && (req as { kind?: string }).kind === 'pages.recent') {
    void pageMonitor.recentForTab(sender.tab?.id).then((list) => sendResponse({ kind: 'pages.recent', result: ok(list) }));
    return true;
  }
  if (req && typeof req === 'object' && (req as { kind?: string }).kind === 'pages.jump') {
    const url = (req as { url?: string }).url ?? '';
    void pageMonitor
      .jumpCurrentTab(sender.tab?.id, url)
      .then((jumped) => sendResponse({ kind: 'pages.jump', result: ok({ jumped }) }));
    return true;
  }

  // 2. （已移除）旧版本地引擎 NM 桥 —— v2.4 起纯浏览器模式，不再转发引擎指令

  // 3. 普通扩展内部请求
  void dispatch(req as RuntimeRequest).then(sendResponse);
  return true;
});

/* ---------------- 快捷键：账号选择轮盘（v3.8：扇形环；页面内无框浮层优先） ----------------
 * toggleAccountWheel 实现抽至 background/account-wheel.ts——桌面指令通道（sync.ts）
 * 需要直调同一函数（SW 自消息不投递给自身上下文，wheel.toggle 曾因此静默丢失）。 */

import { toggleAccountWheel } from './account-wheel';

chrome.commands.onCommand.addListener((command) => {
  if (command === 'quick-wheel') {
    // 角标闪标：证明命令确实到达了当前版本的后台（现场诊断手段）
    void flashBadge('→');
    void toggleAccountWheel();
  }
  if (command === 'quick-pages') {
    void flashBadge('⇢');
    void togglePagesOverlay();
  }
});

/** 最近配置页轮盘（v3.13 收敛：仅绑定页签可唤起——页面监视只记录绑定页签） */
async function togglePagesOverlay(): Promise<void> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url || !/^https?:/i.test(tab.url)) {
      return;
    }
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content/pages-overlay.js'],
      world: 'ISOLATED',
    });
  } catch {
    // 受限页/权限收回：静默
  }
}

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

registerNavigationHandlers();
registerParallelHandlers();
pageMonitor.registerPageMonitorListeners();

// 打开失败自学习（v3.10.9）：绑定页签加载失败时按错误类型翻转协议并原页签重开。
// 优先并行账号（par.* 主流程），未命中再试旧会话模型（session.* 轮盘路径）。
chrome.webNavigation.onErrorOccurred.addListener((details) => {
  if (details.frameId !== 0 || !isSchemeFlipError(details.error)) {
    return; // 仅主 frame 的 scheme 类导航失败才触发协议翻转
  }
  void (async () => {
    if (await handleOpenError(details.tabId, details.error)) {
      return;
    }
    await navigation.handleSessionOpenError(details.tabId);
  })();
});

/* 启动即短显版本号：重新加载扩展后，无需打开任何界面即可确认新代码已生效 */
void flashBadge(`v${EXT_VERSION.split('.').slice(0, 2).join('.')}`).finally(() => {
  // flashBadge 自身 1.2s 后清空；这里把启动展示延长为额外一次，共约 2.4s 可见窗口
});

/* 桌面同步桥（Akso Workbench 私有改造）：账号/盒子数据面外移到桌面端，
   本扩展作为执行面每 2s 轮询快照与指令（wheel.toggle/par.open）；桌面不可达时离线回退本地数据 */
import { startDesktopSync } from './sync';
startDesktopSync();
