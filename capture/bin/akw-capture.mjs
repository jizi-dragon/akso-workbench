#!/usr/bin/env node
/**
 * 入口：直接跑 TS 源码（Node ≥ 24 自带类型擦除），不引入构建步骤。
 * 用法见 capture/README.md 或 akw-capture help。
 */
import { main } from '../src/cli.ts'

main(process.argv.slice(2)).catch((error) => {
  console.error(error instanceof Error ? (error.stack ?? error.message) : String(error))
  process.exitCode = 1
})
