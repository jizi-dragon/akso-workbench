/**
 * 注册给 DSH 的每个工具都必须有稳定 UID。
 *
 * 规则（仓库根 AGENTS.md「工具 UID」）：
 *   · 格式 `AKW-TOOL-<SLUG>-V<n>`，全大写、连字符分隔、全局唯一；
 *   · 新增/改名工具时先在这里登记，再调 `withToolUid()` 注册 —— 没登记会直接抛错；
 *   · UID 一旦用于排查就不复用到别的工具，删除工具时保留并标注 retired；
 *   · 工具的返回值里带 `_uid`，日志里也带同一个 UID，便于把 DSH 侧一次调用对上 host 日志。
 */

export const TOOL_UIDS = {
  akso_workbench_accounts: 'AKW-TOOL-ACCOUNTS-V1',
  akso_workbench_config_plan: 'AKW-TOOL-CONFIG-PLAN-V1',
  akso_workbench_config_apply: 'AKW-TOOL-CONFIG-APPLY-V1',
  akso_workbench_object_analysis: 'AKW-TOOL-OBJECT-ANALYSIS-V1',
} as const

/** 取工具 UID；未登记返回 null（调用方应拒绝注册）。 */
export function toolUid(name: string): string | null {
  return Object.prototype.hasOwnProperty.call(TOOL_UIDS, name) ? TOOL_UIDS[name as keyof typeof TOOL_UIDS] : null
}

/** 最小可用形状：只要 name / execute，其余字段原样透传。 */
export interface ToolDefinitionLike {
  name: string
  description?: string
  execute: (...args: never[]) => unknown
  [key: string]: unknown
}

interface ToolLog {
  info?: (message: string) => void
  warn?: (message: string) => void
}

function tagResult(value: unknown, uid: string): unknown {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return value
  const record = value as Record<string, unknown>
  if (record._uid === uid) return record
  return { _uid: uid, ...record }
}

/**
 * 给工具定义套上 UID：
 *   · description 末尾追加 UID，agent/排查人看工具列表就能对上；
 *   · `execute` 包一层，日志带 `[UID]`，返回对象注入 `_uid`；
 *   · 未登记 UID 直接抛错 —— 这是注册期的硬门禁，不允许“先上线再补”。
 */
export function withToolUid<T extends ToolDefinitionLike>(definition: T, log?: ToolLog): T {
  const uid = toolUid(definition.name)
  if (uid === null) {
    throw new Error(`工具 ${definition.name} 没有登记 UID：请先在 plugin/src/tool-uids.ts 里补一条再注册`)
  }
  const description = `${definition.description ?? ''}\n\nUID: ${uid}（排查用；本工具返回的 _uid 与 host 日志里的 UID 一致）`
  const original = definition.execute.bind(definition)
  const execute = async (...args: never[]): Promise<unknown> => {
    const startedAt = Date.now()
    log?.info?.(`[${uid}] invoke ${definition.name}`)
    try {
      const value = await original(...args)
      const ok = !(value !== null && typeof value === 'object' && (value as { ok?: unknown }).ok === false)
      log?.info?.(`[${uid}] done ${definition.name} ${Date.now() - startedAt}ms ok=${ok}`)
      return tagResult(value, uid)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      log?.warn?.(`[${uid}] failed ${definition.name} ${Date.now() - startedAt}ms: ${message}`)
      throw error
    }
  }
  return { ...definition, description, uid, execute } as unknown as T
}
