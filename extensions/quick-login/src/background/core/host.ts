/**
 * host / 端口工具（**单一真源**）。
 *
 * 此前这些函数分居两处（`hostNoPortOf` / `parentDomainOf` 在 tab-rules.ts，
 * `portOf` / `urlHostOf` / `hostRelated` 在 parallel-session.ts），口径容易漂移；
 * 且 `urlHostOf` 的引入教训说明"函数对不对"和"调用方传什么"必须放在一起看，故收归本模块。
 *
 * ## 口径分工（勿混）
 * 这是本项目最容易出错的一处——0.2.21 曾出现"守卫写对了、调用方却把端口丢了"的半修：
 *   - **身份平面**（下载/归属判定、停用名单、页签收编）：端口**参与**比较，
 *     入参必须来自 `urlHostOf`（带端口）；
 *   - **规则覆盖平面**（DNR `requestDomains`）与 **Cookie 作用域**（RFC 6265）：
 *     端口天然不参与，那类判定请用 `hostNoPortOf` 比较。
 */

/** 取端口（无端口返回空串） */
export function portOf(host: string): string {
  const idx = host.indexOf(':');
  return idx >= 0 ? host.slice(idx + 1) : '';
}

/** host 去端口。DNR `requestDomains` 不含端口；siteHost 带 ":port" 时必须先剥离（v3.10.6 加固） */
export function hostNoPortOf(host: string): string {
  const idx = host.indexOf(':');
  return idx >= 0 ? host.slice(0, idx) : host;
}

/**
 * 父域（`example.com`）：DNR `requestDomains` 语义为「该域及其全部子域」，覆盖网关/接口子域。
 *
 * - IP 字面量（全数字段，内网站点）没有父域概念，返回原 host（在 requestDomains 里重复无害），
 *   避免把 `192.0.2.10` 拼出 `0.105` 这类无意义域；
 * - 入参先剥端口（幂等：调用方已剥也无害），**返回值同样保证不含端口**——带端口的内网 host
 *   既会让 IP 判定失配，也会把 `192.0.2.10:8080` 拼出 `0.105:8080`，而调用方（DNR 规则）
 *   需要的是无端口域。
 */
export function parentDomainOf(host: string): string {
  const bare = hostNoPortOf(host);
  const parts = bare.split('.');
  if (parts.length > 2 && parts.every((p) => /^\d+$/.test(p))) {
    return bare;
  }
  return parts.length > 2 ? parts.slice(-2).join('.') : bare;
}

/**
 * URL → host **带端口**（URL API 已把默认端口规范化掉：http:80 / https:443 不会造成假不匹配）。
 *
 * ⚠ 站点身份判定一律走本函数，**不要用 `URL.hostname`**：它永远不含端口，会让
 * `hostRelated` 的「两端都带端口才比端口」守卫恒不触发（守卫里 `up` 恒为空）。
 */
export function urlHostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return '';
  }
}

/**
 * URL 的 host 是否与绑定 host 同族。**方向无关**（父域方向同样成立）：
 *   - 同 host；或 URL 是绑定 host 的子域（`a.example.com` ⊆ `example.com`）；
 *   - 或二者共享父域（`x.example.com` ↔ `y.example.com`）。
 *
 * 两端都带端口时必须端口一致——同主机的不同端口是**不同站点**
 * （否则桌面自己的页面 `127.0.0.1:18765` 会被当成内网站点 `127.0.0.1:18996` 而串号）。
 */
export function hostRelated(urlHost: string, bindHost: string): boolean {
  const uh = hostNoPortOf(urlHost);
  const bh = hostNoPortOf(bindHost);
  if (!bh) {
    return false;
  }
  const up = portOf(urlHost);
  const bp = portOf(bindHost);
  if (up && bp && up !== bp) {
    return false;
  }
  if (uh === bh || uh.endsWith(`.${bh}`)) {
    return true;
  }
  const parent = parentDomainOf(bh);
  return parent !== bh && (uh === parent || uh.endsWith(`.${parent}`));
}
