import { build, context as createContext } from 'esbuild';
import { copyFileSync, cpSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const extDir = path.join(root, 'packages', 'extension');
const watch = process.argv.includes('--watch');

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

/** 仅复制 UI 静态文件（html/css），不含源码 */
function copyUiStatics(from, to) {
  for (const entry of readdirSync(from)) {
    const src = path.join(from, entry);
    const out = path.join(to, entry);
    if (statSync(src).isDirectory()) {
      mkdirSync(out, { recursive: true });
      copyUiStatics(src, out);
    } else if (entry.endsWith('.html') || entry.endsWith('.css')) {
      copyFileSync(src, out);
    }
  }
}

function copyExtensionStatics() {
  cpSync(path.join(root, 'assets'), path.join(dist, 'assets'), { recursive: true });
  copyFileSync(path.join(extDir, 'manifest.json'), path.join(dist, 'manifest.json'));
  copyUiStatics(path.join(extDir, 'src', 'ui'), path.join(dist, 'ui'));
}

const extensionOptions = {
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: ['chrome110'],
  outbase: path.join(extDir, 'src'),
  outdir: dist,
  logLevel: 'info',
  entryPoints: {
    'background': path.join(extDir, 'src', 'background', 'service-worker.ts'),
    'content/shield-bridge': path.join(extDir, 'src', 'content', 'shield-bridge.ts'),
    'content/shield-main': path.join(extDir, 'src', 'content', 'shield-main.ts'),
    'content/title-hook': path.join(extDir, 'src', 'content', 'title-hook.ts'),
    'content/auto-login': path.join(extDir, 'src', 'content', 'auto-login.ts'),
    'content/wheel-overlay': path.join(extDir, 'src', 'content', 'wheel-overlay.ts'),
    'content/pages-overlay': path.join(extDir, 'src', 'content', 'pages-overlay.ts'),
    'ui/popup/popup': path.join(extDir, 'src', 'ui', 'popup', 'popup.ts'),
    // ui/parallel 并行管理页已随桌面化退役（0.2.13）：账号数据由桌面账号中心统一管理
    'ui/wheel/wheel': path.join(extDir, 'src', 'ui', 'wheel', 'wheel.ts'),
  },
};

copyExtensionStatics();
if (watch) {
  const ctx = await createContext(extensionOptions);
  ctx.watch();
}
await build(extensionOptions);
console.log('BUILD_OK → dist/');
