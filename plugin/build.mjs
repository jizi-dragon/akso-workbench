/**
 * 构建两个半边。
 *
 * - `lib/index.js`  —— node 半边：普通 ESM，零运行时依赖（只用 node: 内建）。
 * - `lib/client.js` —— 浏览器半边：**必须**是 `window.__ModuleLoader__.load({ id, factory })`
 *   这套 factory 形态（规范 §5 规则 2）。esbuild 先出 CJS，再套壳；react / react-dom
 *   走 external —— 基线已经提供，打第二份会双实例（规范 §5 规则 7）。
 */
import { build } from 'esbuild'
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pkg = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8'))
const outdir = join(here, 'lib')
mkdirSync(outdir, { recursive: true })

const watch = process.argv.includes('--watch')

/* ── 浏览器半边 ───────────────────────────────────────────────────────── */
const tmpClient = join(outdir, '.client.tmp.cjs')
await build({
  entryPoints: [join(here, 'src/client/index.tsx')],
  outfile: tmpClient,
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'chrome110',
  jsx: 'automatic',
  external: ['react', 'react-dom', 'react/jsx-runtime', 'react-dom/client'],
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'none',
  logLevel: 'warning',
})

const body = readFileSync(tmpClient, 'utf8')
const indented = body
  .split('\n')
  .map((line) => (line === '' ? line : `\t\t${line}`))
  .join('\n')
writeFileSync(
  join(outdir, 'client.js'),
  `window.__ModuleLoader__.load({\n`
  + `\tid: ${JSON.stringify(pkg.name)},\n`
  + `\tfactory: (require) => {\n`
  + `\t\tvar module = { exports: {} };\n`
  + `\t\tvar exports = module.exports;\n`
  + `${indented}\n`
  + `\t\treturn module.exports;\n`
  + `\t}\n`
  + `});\n`,
)
unlinkSync(tmpClient)

/* ── node 半边 ────────────────────────────────────────────────────────── */
await build({
  entryPoints: [join(here, 'src/index.ts')],
  outfile: join(outdir, 'index.js'),
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  external: ['node:*'],
  legalComments: 'none',
  logLevel: 'warning',
})

console.log(`[akso-workbench] built lib/index.js + lib/client.js${watch ? ' (watch 未启用)' : ''}`)
