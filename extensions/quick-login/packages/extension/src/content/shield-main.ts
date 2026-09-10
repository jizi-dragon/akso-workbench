/**
 * Shield Main —— MAIN world，document_start 注入。
 *
 * 双平面隔离的存储平面（页面侧）v2.7：
 * 1. Storage.prototype 方法级补丁 —— 绑定标签页的 localStorage 全部键重定向到
 *    `__ql_ns_<accountId>__` 命名空间；
 * 2. document.cookie 完全虚拟化 —— 该站登录后服务端经 Set-Cookie 把 token 副本写进
 *    「真实 Cookie jar」（全标签页共享，是 v2.6 实测串号的泄漏通道）。激活后页面读写的
 *    cookie 改存于命名空间内的「Cookie 袋」（__ql_cookies__ JSON），真实 jar 永不被读取；
 * 3. 种子直灌 —— bind 载荷携带后台保存的账号快照（token 等），激活瞬间同步灌入，
 *    杜绝「先跑的页面代码读到别人/空值」的启动竞态；
 * 4. 写入上报 —— 命名空间内 __auth_token__ 等键的变化经桥上报 background（token 捕获通道）；
 * 5. SW/CacheStorage 封控 —— 绑定标签页内阻止站点注册 Service Worker、注销既有注册，
 *    CacheStorage 按账号命名空间键控且 CacheStorage.match 一律 miss（封堵无视 DNR/_qlck
 *    的站点自建缓存层，详见 installSwAndCacheShield）。
 * 6. IndexedDB 命名空间 —— open/deleteDatabase 按账号前缀化、databases() 剥前缀回显、
 *    激活时清扫历史共享库。v3.7 实测：站点把 isAdmin 标志与菜单树缓存在共享 IDB（DBFetch），
 *    是四象限权限串号的直接载体（详见 installIdbShield）。
 */

(() => {
  const win = window as typeof window & { __QL_SHIELD_INSTALLED__?: boolean };
  if (win.__QL_SHIELD_INSTALLED__) {
    return;
  }
  win.__QL_SHIELD_INSTALLED__ = true;

  const WATCH_KEYS = ['__auth_token__', '__auth_user__', '__device_fp__'];
  const TOKEN_KEY = '__auth_token__';
  const NS_TAG = '__ql_ns_';
  const COOKIE_BAG_KEY = '__ql_cookies__';
  const BOOT_GUARD_KEY = '__ql_boot_guard';
  const SRC_PAGE_TO_BRIDGE = 'QL_PAGE_TO_BRIDGE';
  const SRC_BRIDGE_TO_PAGE = 'QL_BRIDGE_TO_PAGE';

  type Mode = 'passthrough' | 'active';
  let mode: Mode = 'passthrough';
  let ns = '';
  let settled = false;

  /** 缓存分区平面（页面层实现；Chrome DNR 无 urlTransform，见 tab-rules.ts 注释） */
  const CACHE_PARTITION_ENABLED = true;
  /** 当前标签页 id（bind 载荷携带），用于 _qlck=t<tabId> 缓存分区键 */
  let tabIdForCache: number | null = null;

  /* ---- 原生方法引用（必须在任何补丁之前捕获） ---- */
  const proto = Storage.prototype;
  const origGetItem = proto.getItem;
  const origSetItem = proto.setItem;
  const origRemoveItem = proto.removeItem;
  const origKey = proto.key;
  const origLengthDesc = Object.getOwnPropertyDescriptor(proto, 'length');
  const cookieDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie')
    ?? Object.getOwnPropertyDescriptor(document, 'cookie');

  /* ================= Cookie 袋（document.cookie 的虚拟视图） ================= */

  let bag: Record<string, string> | null = null;

  function loadBag(): Record<string, string> {
    if (bag) {
      return bag;
    }
    try {
      bag = JSON.parse(origGetItem.call(window.localStorage, ns + COOKIE_BAG_KEY) ?? '{}') as Record<string, string>;
    } catch {
      bag = {};
    }
    if (typeof bag !== 'object' || bag === null) {
      bag = {};
    }
    return bag!;
  }

  function saveBag(): void {
    try {
      journalWrite(window.localStorage, ns + COOKIE_BAG_KEY);
      origSetItem.call(window.localStorage, ns + COOKIE_BAG_KEY, JSON.stringify(loadBag()));
    } catch {
      // 存储满等极端场景忽略；袋与 LS 短暂不一致优于崩溃
    }
    scheduleBagReport();
  }

  /* ---- 袋→快照回流（v3.10.6）：页内 Cookie 写入实时并入账号网络平面快照 ----
   * 绑定页签的 document.cookie 写入只进袋子（jar 隐身、DNR 回放不可见）——平台
   * 登录后由页内 JS 写入的下载票据/凭据若不回流，出站回放永远缺失（下载被拒）。
   * 尾沿节流 120ms（v3.10.8：300ms 的竞态窗口足够让「写票据→立刻下载」的请求
   * 带着旧回放值出站而被拒）；叛逃回滚冻结后不再上报（回滚值不得覆盖账号快照）。 */
  let bagReportTimer = 0;
  function scheduleBagReport(): void {
    if (journalFrozen || bagReportTimer) {
      return;
    }
    bagReportTimer = window.setTimeout(() => {
      bagReportTimer = 0;
      if (journalFrozen) {
        return;
      }
      window.postMessage(
        { src: SRC_PAGE_TO_BRIDGE, payload: { op: 'bagChanged', bag: { ...loadBag() } } },
        '*',
      );
    }, 120);
  }

  function bagSet(key: string, value: string): void {
    loadBag();
    if (value === '') {
      delete bag![key];
    } else {
      bag![key] = value;
    }
    saveBag();
  }

  function installCookieVirtualization(): void {
    if (!cookieDesc?.configurable) {
      return;
    }
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get(this: Document): string {
        const b = loadBag();
        return Object.entries(b)
          .map(([k, v]) => `${k}=${v}`)
          .join('; ');
      },
      set(this: Document, text: string): void {
        // 仅取首个分号前的 k=v 对；path/domain/expires 等属性对页内读取无意义，忽略
        const firstChunk = String(text).split(';')[0] ?? '';
        const eq = firstChunk.indexOf('=');
        if (eq <= 0) {
          return;
        }
        const key = firstChunk.slice(0, eq).trim();
        const value = firstChunk.slice(eq + 1).trim();
        if (!key) {
          return;
        }
        bagSet(key, decodeURIComponentSafe(value));
      },
    });
  }

  function decodeURIComponentSafe(v: string): string {
    try {
      return decodeURIComponent(v);
    } catch {
      return v;
    }
  }

  /* ================= 上行报告（经桥转 background） ================= */

  function reportStorageWrite(key: string, value: string | null): void {
    if (!WATCH_KEYS.includes(key)) {
      return;
    }
    window.postMessage(
      { src: SRC_PAGE_TO_BRIDGE, payload: { op: 'storageWrite', key, value } },
      '*',
    );
  }

  /* ---- 写入日志（身份叛逃回滚的依据，v3.10.3） ----
   * 绑定页签若被用户用于登录另一个账号（低代码平台「新开页签手输网址」场景），
   * 叛逃会话的写入会污染共享命名空间（token/user/缓存 → 其它同账号页签读错身份）。
   * 本日志记录「本页会话对命名空间的每一次写入前的原始值」，回滚时逐键恢复，
   * 使命名空间回到页签打开前的状态——叛逃页签转为原始页签，账号数据零污染。 */

  const journal = new Map<string, string | null>(); // rawKey → 写入前的原始值（null = 原不存在）
  let journalFrozen = false; // 回滚后冻结（页签即将重载）

  function journalWrite(storage: Storage, rawKey: string): void {
    if (journalFrozen || journal.has(rawKey)) {
      return; // 只记首次：保留「页签打开前」的原始状态
    }
    try {
      journal.set(rawKey, origGetItem.call(storage, rawKey));
    } catch {
      // 读失败不阻断写入
    }
  }

  function journalRollback(): void {
    if (journalFrozen) {
      return;
    }
    journalFrozen = true;
    try {
      for (const [rawKey, prev] of journal) {
        try {
          if (prev === null) {
            origRemoveItem.call(window.localStorage, rawKey);
          } else {
            origSetItem.call(window.localStorage, rawKey, prev);
          }
        } catch {
          // 单键失败不阻断其余回滚
        }
      }
    } finally {
      journal.clear();
      bag = null; // 袋内存缓存作废，下次读取从已恢复的存储重载
      window.postMessage(
        { src: SRC_PAGE_TO_BRIDGE, payload: { op: 'journalRollbackDone' } },
        '*',
      );
    }
  }

  /** 清扫本账号命名空间的 IDB 库与 CacheStorage 缓存（叛逃会话写入的他人数据） */
  async function nsWipeShared(): Promise<void> {
    try {
      if (typeof indexedDB?.databases === 'function') {
        const dbs = (await indexedDB.databases()) ?? [];
        for (const info of dbs) {
          if (typeof info.name === 'string' && info.name.startsWith(ns)) {
            try {
              indexedDB.deleteDatabase(info.name);
            } catch {
              // 单库失败不阻断
            }
          }
        }
      }
      if (typeof caches !== 'undefined' && typeof caches.keys === 'function') {
        const names = await caches.keys();
        for (const name of names) {
          if (name.startsWith(ns)) {
            try {
              await caches.delete(name);
            } catch {
              // 单缓存失败不阻断
            }
          }
        }
      }
    } catch {
      // 清扫失败不阻断叛逃处置
    }
  }

  function reportAuthHeader(value: string): void {
    window.postMessage(
      { src: SRC_PAGE_TO_BRIDGE, payload: { op: 'authHeader', value } },
      '*',
    );
  }

  /* ---- fetch / XHR 出站 Authorization 头嗅探 + 页面层缓存分区 + 名称型 API 响应嗅探 ---- */

  /* ---- v3.11 名称嗅探：白名单 API 响应 → 名称↔ID 对 → 桥上行 ----
   * 白名单与映射表见 docs/FEASIBILITY-RECENT-PAGES.md §〇（实测）：
   * BasicObjectDetail（对象名）/ GetWorkflowBasic*（工作流名）/ GetUserMenuPermission +
   * MenuGroup/QueryList（菜单树/组）/ UserView/GetView（视图名）。
   * 只读不阻塞：fetch 走 clone 异步读取，XHR 挂 load 监听；任何异常静默吞掉。 */
  const NAME_API_RE =
    /api\/platform\/(.*BasicObjectDetail|.*GetWorkflowBasic|.*GetUserMenuPermission|MenuGroup\/QueryList|UserView\/GetView(\?|$))/i;

  function extractNamePairs(json: unknown, out: { name: string; id: string }[], depth = 0): void {
    if (depth > 4 || out.length >= 24 || !json || typeof json !== 'object') {
      return;
    }
    if (Array.isArray(json)) {
      for (const it of json.slice(0, 30)) {
        extractNamePairs(it, out, depth + 1);
      }
      return;
    }
    const rec = json as Record<string, unknown>;
    const name = rec.name ?? rec.Name ?? rec.title ?? rec.menuName ?? rec.objectName;
    const id = rec.id ?? rec.guid ?? rec.code ?? rec.viewId ?? rec.objectId;
    if (typeof name === 'string' && name.trim() && id !== undefined && id !== null && String(id).trim()) {
      out.push({ name: String(name).slice(0, 60), id: String(id).slice(0, 40) });
    }
    for (const v of Object.values(rec)) {
      if (v && typeof v === 'object') {
        extractNamePairs(v, out, depth + 1);
      }
    }
  }

  function reportPageNames(text: string, srcUrl: string): void {
    try {
      if (!text || text.length > 262_144) {
        return;
      }
      const json = JSON.parse(text) as unknown;
      const names: { name: string; id: string }[] = [];
      extractNamePairs(json, names);
      if (names.length) {
        window.postMessage(
          {
            src: SRC_PAGE_TO_BRIDGE,
            payload: { op: 'pageNames', names, src: srcUrl.slice(0, 200) },
          },
          '*',
        );
      }
    } catch {
      // 非 JSON / 解析失败：静默
    }
  }

  /**
   * 缓存分区：同源 GET 请求查询串追加 `_qlck=t<tabId>`。
   * 浏览器 HTTP 缓存按 URL 且全 profile 共享，第一个登录账号的权限/菜单响应会被
   * 后续账号直接命中（不出网，前三平面全部失明）。同标签键稳定、跨标签必然不同。
   * 仅 GET（可缓存）、仅同源（站内接口）、已含 _qlck 则幂等跳过。
   */
  function cachePartitionUrl(rawUrl: string): string {
    if (!CACHE_PARTITION_ENABLED || tabIdForCache === null) {
      return rawUrl;
    }
    try {
      const u = new URL(rawUrl, location.href);
      if (u.host !== location.host) {
        return rawUrl; // 仅站内接口；第三方/静态 CDN 不动
      }
      if (u.searchParams.has('_qlck')) {
        return rawUrl;
      }
      u.searchParams.set('_qlck', `t${tabIdForCache}`);
      return u.href;
    } catch {
      return rawUrl;
    }
  }

  function patchNetworkSniffers(): void {
    const nativeFetch = win.fetch;
    const sniffFetch = function (
      this: unknown,
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> {
      try {
        let method = 'GET';
        let urlStr: string;
        if (input instanceof Request) {
          method = input.method;
          urlStr = input.url;
        } else if (typeof input === 'string') {
          urlStr = input;
        } else if (input instanceof URL) {
          urlStr = input.href;
        } else {
          urlStr = String(input);
        }
        if (typeof init?.method === 'string') {
          method = init.method;
        }
        if (method.toUpperCase() === 'GET') {
          const partitioned = cachePartitionUrl(urlStr);
          if (partitioned !== urlStr) {
            if (input instanceof Request) {
              input = new Request(partitioned, input);
            } else {
              input = partitioned;
            }
          }
        }
        // 嗅探（只观察不修改）
        let headers: Headers | undefined;
        if (init && init.headers) {
          headers = new Headers(init.headers as HeadersInit);
        } else if (input instanceof Request) {
          headers = input.headers;
        }
        const auth = headers?.get('authorization');
        if (auth) {
          reportAuthHeader(auth);
        }
        // 名称嗅探（clone 异步读取，不阻塞响应流）
        if (method.toUpperCase() === 'GET' && NAME_API_RE.test(urlStr)) {
          try {
            const result = nativeFetch.call(this, input, init) as Promise<Response>;
            void result
              .then((res) => {
                try {
                  void res
                    .clone()
                    .text()
                    .then((t) => reportPageNames(t, urlStr))
                    .catch(() => undefined);
                } catch {
                  // clone 不可得（流已被消费等）忽略
                }
                return res;
              })
              .catch(() => undefined);
          } catch {
            // 嗅探失败忽略，走不到就跳过
          }
        }
      } catch {
        // 探测失败不影响请求本身
      }
      return nativeFetch.call(this, input, init);
    };
    win.fetch = sniffFetch as typeof win.fetch;

    const xhrProto = XMLHttpRequest.prototype as unknown as Record<string, unknown>;
    const nativeOpen = xhrProto.open as (m: string, u: string | URL, ...rest: unknown[]) => void;
    const nativeSetHeader = xhrProto.setRequestHeader as (n: string, v: string) => void;
    const nativeSend = xhrProto.send as (...args: unknown[]) => void;
    const AUTH_SLOT = Symbol('__ql_auth__');
    xhrProto.open = function (this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]): void {
      let u = String(url);
      if (String(method).toUpperCase() === 'GET') {
        const partitioned = cachePartitionUrl(u);
        if (partitioned !== u) {
          u = partitioned;
        }
      }
      nativeOpen.call(this, method, u, ...rest);
    } as typeof XMLHttpRequest.prototype.open;
    xhrProto.setRequestHeader = function (this: XMLHttpRequest, name: string, value: string): void {
      if (name.toLowerCase() === 'authorization') {
        (this as unknown as Record<symbol, string>)[AUTH_SLOT] = value;
      }
      nativeSetHeader.call(this, name, value);
    } as typeof XMLHttpRequest.prototype.setRequestHeader;
    xhrProto.send = function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null): void {
      const auth = (this as unknown as Record<symbol, string | undefined>)[AUTH_SLOT];
      if (auth) {
        reportAuthHeader(auth);
      }
      try {
        const u = String(this.responseURL || '');
        if (u && NAME_API_RE.test(u)) {
          this.addEventListener('load', () => {
            try {
              reportPageNames(String(this.responseText ?? ''), u);
            } catch {
              // 读取失败忽略
            }
          });
        }
      } catch {
        // 嗅探失败忽略
      }
      return nativeSend.apply(this, [body] as unknown[]);
    } as typeof XMLHttpRequest.prototype.send;
  }

  /* ================= Storage.prototype 补丁 ================= */

  function realLength(storage: Storage): number {
    if (origLengthDesc && origLengthDesc.get) {
      return origLengthDesc.get.call(storage);
    }
    return 0;
  }

  function namespaceKeys(storage: Storage): string[] {
    const out: string[] = [];
    const len = realLength(storage);
    for (let i = 0; i < len; i++) {
      const k = origKey.call(storage, i);
      if (k !== null && k.startsWith(ns)) {
        out.push(k);
      }
    }
    return out;
  }

  /** 命名空间内的写入：保持 Cookie 袋同步（单一事实源） */
  function onNamespaceWrite(storage: Storage, bareKey: string, rawKey: string, value: string | null): void {
    if (bareKey === TOKEN_KEY) {
      if (value === null) {
        bagSet(TOKEN_KEY, '');
        origRemoveItem.call(storage, rawKey);
      } else {
        bagSet(TOKEN_KEY, value);
      }
    }
    reportStorageWrite(bareKey, value);
  }

  function installStoragePatch(): void {
    Object.defineProperty(proto, 'getItem', {
      configurable: true,
      writable: true,
      value: function (this: Storage, key: string): string | null {
        return origGetItem.call(this, ns + String(key));
      },
    });
    Object.defineProperty(proto, 'setItem', {
      configurable: true,
      writable: true,
      value: function (this: Storage, key: string, value: string): void {
        const bare = String(key);
        const raw = ns + bare;
        const val = String(value);
        journalWrite(this, raw);
        origSetItem.call(this, raw, val);
        onNamespaceWrite(this, bare, raw, val);
      },
    });
    Object.defineProperty(proto, 'removeItem', {
      configurable: true,
      writable: true,
      value: function (this: Storage, key: string): void {
        const bare = String(key);
        const raw = ns + bare;
        journalWrite(this, raw);
        origRemoveItem.call(this, raw);
        onNamespaceWrite(this, bare, raw, null);
      },
    });
    Object.defineProperty(proto, 'clear', {
      configurable: true,
      writable: true,
      value: function (this: Storage): void {
        // 仅清空本账号命名空间 + Cookie 袋，等价「该标签页视角下的 clear」
        for (const raw of namespaceKeys(this)) {
          origRemoveItem.call(this, raw);
        }
        bag = {};
        saveBag();
      },
    });
    Object.defineProperty(proto, 'key', {
      configurable: true,
      writable: true,
      value: function (this: Storage, index: number): string | null {
        const keys = namespaceKeys(this).filter((k) => k !== ns + COOKIE_BAG_KEY);
        if (!Number.isInteger(index) || index < 0 || index >= keys.length) {
          return null;
        }
        return keys[index].slice(ns.length);
      },
    });
    Object.defineProperty(proto, 'length', {
      configurable: true,
      get: function (this: Storage): number {
        return namespaceKeys(this).filter((k) => k !== ns + COOKIE_BAG_KEY).length;
      },
    });

    installCookieVirtualization();
    patchNetworkSniffers();
  }

  /* ================= 第五平面：站点 Service Worker / CacheStorage 封控 ================= */

  /** SW/CacheStorage 封控开关（若目标站功能依赖其自有 SW 可置 false 快速回退） */
  const SW_SHIELD_ENABLED = true;

  /**
   * 站点自有 Service Worker 与其 Cache Storage 是全 profile / 全 origin 共享的缓存层，
   * 完全无视 DNR 改头与 `_qlck` 分区（请求根本不发网）。四象限泄漏若走此层，
   * 前三平面全部失明。对策：
   * 1. 阻止新注册（register → reject）+ 注销既有注册（清除历史受控关系）；
   * 2. CacheStorage 按账号命名空间键控（open/keys/has/delete 前缀 ns）；
   * 3. CacheStorage.match 一律 miss —— 页面自建缓存的读命中被屏蔽，回落网络
   *    （那里才有按标签的 DNR 改头与缓存分区；同命名空间内 Cache 实例的 match 不受影响）。
   * 仅 active（已绑定）模式安装；passthrough 模式不改变站点行为。
   */
  function installSwAndCacheShield(): void {
    try {
      const swProto = window.ServiceWorkerContainer?.prototype as
        | (ServiceWorkerContainer & { register?: unknown })
        | undefined;
      const swCtl = navigator.serviceWorker;
      if (SW_SHIELD_ENABLED && swProto && swCtl && typeof swProto.register === 'function') {
        const desc = Object.getOwnPropertyDescriptor(swProto, 'register');
        if (!desc || desc.configurable) {
          Object.defineProperty(swProto, 'register', {
            configurable: true,
            writable: true,
            // 阻断注册：站点回调 .catch 分支即可，不中断业务代码
            value: () => Promise.reject(new Error('QuickLogin: site Service Worker disabled for bound tab')),
          });
        }
        // 注销既有注册（SW 按 origin 共享：对全部标签页一致生效，消除共享缓存层）
        if (typeof swCtl.getRegistrations === 'function') {
          void Promise.resolve(swCtl.getRegistrations())
            .then((regs) => {
              for (const r of regs ?? []) {
                try {
                  void r.unregister();
                } catch {
                  // 单个注销失败不影响其余
                }
              }
            })
            .catch(() => undefined);
        }
      }

      const CacheStorageProto = window.CacheStorage?.prototype as
        | (CacheStorage & { open?: unknown })
        | undefined;
      if (SW_SHIELD_ENABLED && CacheStorageProto && typeof CacheStorageProto.open === 'function') {
        const oOpen = CacheStorageProto.open as typeof CacheStorageProto.open;
        const oKeys = CacheStorageProto.keys as typeof CacheStorageProto.keys;
        const oHas = CacheStorageProto.has as typeof CacheStorageProto.has;
        const oDelete = CacheStorageProto.delete as typeof CacheStorageProto.delete;
        const patch = (
          proto: object,
          key: string,
          value: (...args: any[]) => any,
        ): void => {
          const d = Object.getOwnPropertyDescriptor(proto, key);
          if (!d || d.configurable) {
            Object.defineProperty(proto, key, { configurable: true, writable: true, value });
          }
        };
        patch(CacheStorageProto, 'open', function (this: CacheStorage, name: string) {
          return oOpen.call(this, ns + String(name));
        });
        patch(CacheStorageProto, 'keys', function (this: CacheStorage) {
          return Promise.resolve(oKeys.call(this)).then((arr) =>
            (arr as string[]).filter((k) => k.startsWith(ns)).map((k) => k.slice(ns.length)),
          );
        });
        patch(CacheStorageProto, 'has', function (this: CacheStorage, name: string) {
          return oHas.call(this, ns + String(name));
        });
        patch(CacheStorageProto, 'delete', function (this: CacheStorage, name: string) {
          return oDelete.call(this, ns + String(name));
        });
        patch(CacheStorageProto, 'match', function () {
          // 跨命名空间搜索必读他人缓存：直接 miss，回落网络（受 DNR/_qlck 管辖）
          return Promise.resolve(undefined);
        });
      }
    } catch {
      // 封控失败不阻断其余隔离平面
    }
  }

  /* ================= 第六平面：IndexedDB 命名空间 ================= */

  /**
   * 平面 1.5：BroadcastChannel 命名空间。
   * BroadcastChannel 按 origin 全局共享——同源所有页签（无论账号）收发同一频道。
   * 平台用它广播「登出/会话失效」等事件时，任一账号登出会把其它账号的页签一并
   * 广播下线（v3.9.6 实测「退出一个账号全部同步退出」的最可能通路）。
   * 对策：频道名加账号命名空间前缀——同账号各页签仍可互通，跨账号互不可闻。
   * （storage 事件因键名已带命名空间前缀、平台监听的是裸键名，天然安全；SharedWorker
   *   未处理——脚本 URL 无法改名，观测到站点依赖时再评估阻断。）
   */
  function installBroadcastShield(): void {
    try {
      const BCtor = window.BroadcastChannel as unknown as
        | (new (name: string) => BroadcastChannel)
        | undefined;
      if (!BCtor) {
        return;
      }
      class NSBroadcastChannel extends BCtor {
        constructor(name: string) {
          super(ns + String(name));
        }
      }
      Object.defineProperty(window, 'BroadcastChannel', {
        configurable: true,
        writable: true,
        value: NSBroadcastChannel,
      });
    } catch {
      // 封控失败不阻断其余隔离平面
    }
  }

  /**
   * IndexedDB 是 origin 级共享存储，完全绕开 DNR/_qlck/CacheStorage 各平面。
   * v3.7 实测：目标站把 `isAdmin` 标志位与全量菜单树缓存在 IDB 库 `DBFetch`
   * （键 = URL 哈希，无账号维度）——后打开的标签页免密恢复时直接消费前一账号
   * 写入的条目，即四象限「普通用户获得管理员 / 管理员被同化」的载体。
   * 对策（与 CacheStorage 层同法）：
   * 1. open / deleteDatabase 重定向到 `ns + name`（每账号独立库）；
   * 2. databases() 仅返回本命名空间内的库（剥离前缀，页面视图不变）；
   * 3. 激活时清扫历史无前缀共享库（毒源；fire-and-forget，删除幂等）。
   * 残余风险：站点若在 Web Worker 内开 IDB 则不受此补丁（主世界专用），待观测。
   */
  const IDB_SHIELD_ENABLED = true;

  function installIdbShield(): void {
    try {
      if (!IDB_SHIELD_ENABLED || typeof IDBFactory === 'undefined') {
        return;
      }
      const idbProto = IDBFactory.prototype as unknown as Record<string, unknown>;
      const oOpen = idbProto.open as (name: string, version?: number) => IDBOpenDBRequest;
      const oDelete = idbProto.deleteDatabase as (name: string) => IDBOpenDBRequest;
      const oDatabases = idbProto.databases as () => Promise<IDBDatabaseInfo[]>;
      const patch = (proto: object, key: string, value: (...args: any[]) => any): void => {
        const d = Object.getOwnPropertyDescriptor(proto, key);
        if (!d || d.configurable) {
          Object.defineProperty(proto, key, { configurable: true, writable: true, value });
        }
      };
      patch(idbProto, 'open', function (this: IDBFactory, name: string, version?: number) {
        return version === undefined
          ? oOpen.call(this, ns + String(name))
          : oOpen.call(this, ns + String(name), version);
      });
      patch(idbProto, 'deleteDatabase', function (this: IDBFactory, name: string) {
        return oDelete.call(this, ns + String(name));
      });
      if (typeof oDatabases === 'function') {
        patch(idbProto, 'databases', function (this: IDBFactory) {
          return Promise.resolve(oDatabases.call(this)).then((infos) =>
            (infos ?? [])
              .filter((i) => typeof i.name === 'string' && i.name.startsWith(ns))
              .map((i) => ({ ...i, name: i.name!.slice(ns.length) })),
          );
        });
      }
      // 注意：不做「无前缀历史库清扫」。v3.7.0 的清扫会误伤 passthrough 标签页
      // （ns 未生效 → 其库无前缀）正持有的连接：deleteDatabase 被连接阻塞挂起，
      // 标签页一刷新连接关闭、删除立即执行、库凭空蒸发（v3.7.1 移除）。
      // 隔离后旧库已成孤儿（补丁页一律读写带前缀库），保留无害。
    } catch {
      // 第六平面失败不阻断其余隔离平面
    }
  }

  /* ================= 绑定处理 ================= */

  /** 袋权威同步（v3.12.1）：把 Cookie 袋整体重置为账号快照视图（保留种子写入的 token）。
   *  命名空间里残留的上一会话陈旧袋值若不清，会经页内读取/袋回流毒化登录 POST。 */
  function resetBagTo(view: Record<string, string>): void {
    const token = loadBag()[TOKEN_KEY] ?? null;
    bag = { ...view };
    if (token) {
      bag[TOKEN_KEY] = token;
    }
    saveBag();
  }

  function activate(accountId: string, seed?: Record<string, string>, bagView?: Record<string, string>): void {
    if (mode === 'active') {
      return;
    }
    mode = 'active';
    ns = `${NS_TAG}${accountId}__`;
    installStoragePatch();
    installSwAndCacheShield();
    installBroadcastShield();
    installIdbShield();
    // 顺序敏感：先重置袋为快照权威视图（清陈旧残留），再灌种子（token 回到袋里）
    if (bagView) {
      resetBagTo(bagView);
    }
    applySeed(seed);
  }

  /** 种子直灌：不经补丁层静默写入命名空间（并同步 token 进 Cookie 袋）。
   *  值为空字符串 = 显式清除该键（打开时清掉可能被叛逃会话污染的持久化身份键） */
  function applySeed(seed?: Record<string, string>): void {
    if (!seed) {
      return;
    }
    for (const [key, value] of Object.entries(seed)) {
      if (typeof value !== 'string') {
        continue;
      }
      try {
        if (value === '') {
          journalWrite(window.localStorage, ns + key);
          origRemoveItem.call(window.localStorage, ns + key);
          if (key === TOKEN_KEY) {
            bagSet(TOKEN_KEY, '');
          }
          continue;
        }
        if (!value) {
          continue;
        }
        journalWrite(window.localStorage, ns + key);
        origSetItem.call(window.localStorage, ns + key, value);
        if (key === TOKEN_KEY) {
          bagSet(TOKEN_KEY, value);
        }
      } catch {
        // 单键失败不阻断其余种子
      }
    }
  }

  function handleBind(
    accountId: string,
    seed?: Record<string, string>,
    tabId?: number,
    bagView?: Record<string, string>,
  ): void {
    if (typeof tabId === 'number') {
      tabIdForCache = tabId;
    }
    if (settled) {
      if (mode === 'active') {
        // 已激活：幂等重灌种子（覆盖命名空间键，防御性）+ 袋权威同步
        applySeed(seed);
        if (bagView) {
          resetBagTo(bagView);
        }
      }
      // passthrough（已判 unbound）收到迟到的旧 bind 应答：必须忽略——
      // 此时 ns 未定，灌种子会把账号身份写进真实裸层（原始页签泄漏账号会话）
      return;
    }
    settled = true;

    if (document.readyState === 'loading') {
      activate(accountId, seed, bagView);
      return;
    }

    // 页面 bundle 已在无命名空间状态下执行：刷新一次重来（守卫防循环）。
    if (window.sessionStorage.getItem(BOOT_GUARD_KEY) === '1') {
      window.sessionStorage.removeItem(BOOT_GUARD_KEY);
      activate(accountId, seed, bagView);
      return;
    }
    window.sessionStorage.setItem(BOOT_GUARD_KEY, '1');
    window.location.reload();
  }

  /* ---- 下行监听 + 握手重试 ---- */
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.source !== window) {
      return;
    }
    const data = event.data as {
      src?: string;
      payload?: { op?: string; accountId?: string; tabId?: number; seed?: Record<string, string>; bag?: Record<string, string> };
    } | null;
    if (!data || data.src !== SRC_BRIDGE_TO_PAGE) {
      return;
    }
    const payload = data.payload;
    if (payload && payload.op === 'bind' && typeof payload.accountId === 'string') {
      handleBind(
        payload.accountId,
        payload.seed,
        typeof payload.tabId === 'number' ? payload.tabId : undefined,
        payload.bag,
      );
    } else if (payload && payload.op === 'unbound') {
      settled = true; // 显式未绑定：保持直通，不再等待
    } else if (payload && payload.op === 'journalRollback') {
      journalRollback(); // 身份叛逃处置：恢复命名空间到页签打开前
    } else if (payload && payload.op === 'nsWipeShared') {
      void nsWipeShared();
    }
  });

  function hello(attempt: number): void {
    if (attempt <= 0) {
      return;
    }
    window.setTimeout(() => {
      if (!settled) {
        window.postMessage(
          { src: SRC_PAGE_TO_BRIDGE, payload: { op: 'hello', url: location.href } },
          '*',
        );
        hello(attempt - 1);
      }
    }, 120);
  }

  window.postMessage({ src: SRC_PAGE_TO_BRIDGE, payload: { op: 'hello', url: location.href } }, '*');
  hello(6);
})();
