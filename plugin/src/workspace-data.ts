/**
 * 工作区数据目录：所有**生成物**统一落在 `<workspace>/akso-workbench-data`。
 *
 * 这里与 `store.ts` 的 `dataDir(dshHome)` 分工不同：
 *   - 生成物：对象分析历史、配置计划/任务、报告、审核文档 → 工作区；
 *   - 账号/会话/偏好等**状态** → `$DSH_HOME/akso-workbench`（不进项目，避免凭据散落）；
 *   - 运行日志/写入审计/审批记录 → `$DSH_HOME/akso-workbench`（用户 2026-11-25 明确日志系统例外）。
 */
import { join } from 'node:path'

export const WORKSPACE_DATA_DIR = 'akso-workbench-data'

/** `<workspace>/akso-workbench-data`。 */
export function workspaceDataDir(workspaceRoot: string): string {
  return join(workspaceRoot, WORKSPACE_DATA_DIR)
}

/** `<workspace>/akso-workbench-data/<parts...>`。 */
export function workspaceDataPath(workspaceRoot: string, ...parts: string[]): string {
  return join(workspaceDataDir(workspaceRoot), ...parts)
}

/**
 * 取工作区根；没有就抛错，避免静默写回 `$DSH_HOME`。
 * 调用方应把 `undefined` 当成“未定位到工作区”并给出明确提示。
 */
export function requireWorkspaceRoot(workspaceRoot: string | undefined, hint = '未定位到当前工作区，生成数据不落盘'): string {
  const value = workspaceRoot?.trim()
  if (value === undefined || value === '') throw new Error(hint)
  return value
}
