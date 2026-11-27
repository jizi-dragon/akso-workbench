/**
 * 插件自己的版本号 —— **唯一实现**。
 *
 * 为什么单独一个文件：host 侧现在有三处要用它（发给云端的 `X-Akso-Client-Version`、
 * 运行日志 meta、检查更新的"当前版本"）。三处各写一份
 * `readFileSync(join(here, '..', 'package.json'))`，就等于留了三份**路径假设**：
 * 源码跑（`plugin/src/*.ts`）与打包后（`plugin/lib/index.js`）只要有一处算错，
 * 页面上就会显示一个不存在的版本号，而且不会有任何报错提示。
 *
 * 路径假设只有一条：本文件位于 `plugin/src/` 或打包进 `plugin/lib/`，
 * 两种位置往上找一级都是 `plugin/package.json`。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

let cached: string | null = null

/** 当前插件版本；读不到时返回 `''`（调用方负责别把空串当成版本）。 */
export function pluginVersion(): string {
  if (cached !== null) return cached
  try {
    const here = dirname(fileURLToPath(import.meta.url))
    const pkg = JSON.parse(readFileSync(join(here, '..', 'package.json'), 'utf8')) as { version?: unknown }
    cached = typeof pkg.version === 'string' ? pkg.version : ''
  } catch {
    cached = ''
  }
  return cached
}
