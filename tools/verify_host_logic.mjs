/**
 * host/端口工具的行为断言（`background/core/host.ts`）。
 *
 * 为什么需要：端口口径是**三平面分工**（身份平面比端口 / DNR 规则与 Cookie 作用域不比端口），
 * 且 0.2.21 出过「守卫写对了、调用方把端口丢了」的半修事故——纯逻辑回归只能靠断言锁住。
 * 本脚本用 esbuild 把该 TS 模块单独打包成临时 ESM 再逐条断言，不依赖扩展运行时。
 *
 * 用法： node tools/verify_host_logic.mjs
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..');
const EXT_PKG = path.join(ROOT, 'extensions', 'quick-login', 'package.json');
const HOST_TS = path.join(ROOT, 'extensions', 'quick-login', 'src', 'background', 'core', 'host.ts');

// esbuild 只装在扩展工作区里：用 createRequire 从该 package 解析
const require = createRequire(EXT_PKG);
const esbuild = require('esbuild');

const outDir = mkdtempSync(path.join(tmpdir(), 'ql-host-'));
const outFile = path.join(outDir, 'host.mjs');
try {
  await esbuild.build({
    entryPoints: [HOST_TS],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    outfile: outFile,
    logLevel: 'warning',
  });
  const { hostNoPortOf, hostRelated, parentDomainOf, portOf, urlHostOf } = await import(pathToFileURL(outFile).href);

  const cases = [
    // 基础工具
    ['portOf 有端口', () => assert.equal(portOf('192.0.2.10:8080'), '8080')],
    ['portOf 无端口', () => assert.equal(portOf('example.com'), '')],
    ['hostNoPortOf 剥端口', () => assert.equal(hostNoPortOf('192.0.2.10:8080'), '192.0.2.10')],
    ['hostNoPortOf 幂等', () => assert.equal(hostNoPortOf('example.com'), 'example.com')],

    // urlHostOf：带端口 + 默认端口规范化
    ['urlHostOf 保留非默认端口', () => assert.equal(urlHostOf('https://example.com:8080/a'), 'example.com:8080')],
    ['urlHostOf 规范化 https 默认端口', () => assert.equal(urlHostOf('https://example.com/a'), 'example.com')],
    ['urlHostOf 规范化 http:80', () => assert.equal(urlHostOf('http://example.com:80/a'), 'example.com')],
    ['urlHostOf 非法 URL → 空串', () => assert.equal(urlHostOf('not a url'), '')],

    // parentDomainOf：返回值必须不含端口（DNR requestDomains 契约）
    ['父域 双段域名返回自身', () => assert.equal(parentDomainOf('example.com'), 'example.com')],
    ['父域 三段取后两段', () => assert.equal(parentDomainOf('a.b.example.com'), 'example.com')],
    ['父域 IP 返回自身', () => assert.equal(parentDomainOf('192.0.2.10'), '192.0.2.10')],
    ['父域 IP 带端口 → 不含端口', () => assert.equal(parentDomainOf('192.0.2.10:8080'), '192.0.2.10')],
    ['父域 域名带端口 → 不含端口', () => assert.equal(parentDomainOf('a.b.example.com:8080'), 'example.com')],

    // hostRelated：身份平面（端口参与比较）
    ['同 host 同端口', () => assert.equal(hostRelated('192.0.2.10:8080', '192.0.2.10:8080'), true)],
    ['同 host 端口不同 → 不同站点（桌面 18765 vs 18996）', () => assert.equal(hostRelated('127.0.0.1:18765', '127.0.0.1:18996'), false)],
    ['同 host 端口不同（内网）', () => assert.equal(hostRelated('192.0.2.10:9090', '192.0.2.10:8080'), false)],
    ['绑定无端口 → 任意端口均相关', () => assert.equal(hostRelated('example.com:8080', 'example.com'), true)],
    ['子域相关', () => assert.equal(hostRelated('a.example.com', 'example.com'), true)],
    ['父域方向亦相关（方向无关语义）', () => assert.equal(hostRelated('example.com', 'a.example.com'), true)],
    ['同父域相关', () => assert.equal(hostRelated('x.example.com', 'y.example.com'), true)],
    ['无关域不相关', () => assert.equal(hostRelated('foo.com', 'example.com'), false)],
    ['绑定 host 为空 → false', () => assert.equal(hostRelated('example.com', ''), false)],
  ];

  let pass = 0;
  for (const [name, fn] of cases) {
    try {
      fn();
      pass++;
    } catch (e) {
      console.log(`FAIL  ${name}  →  ${e.message}`);
    }
  }
  // 已知局限（记录，不作失败）：
  //  - 无公共后缀表：foo.co.uk 会归约成 co.uk（对目标 .com 平台无影响）
  //  - 无端口表：端口只做字符串相等比较
  console.log(`KNOWN  parentDomainOf('foo.co.uk') = ${parentDomainOf('foo.co.uk')}（无 PSL，已知局限）`);
  console.log(`\nHOST_CHECKS: ${pass}/${cases.length}`);
  process.exitCode = pass === cases.length ? 0 : 1;
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
