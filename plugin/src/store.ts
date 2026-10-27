/**
 * 本机持久化：只存**令牌**与**UI 偏好**，不存任何平台口令。
 *
 *   `$DSH_HOME/akso-workbench/cloud.json` —— akso-cloud 会话（0600，原子写）
 *   `$DSH_HOME/akso-workbench/prefs.json` —— 当前选中的账号 id（纯 UI 偏好）
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export interface StoredSession {
  baseUrl: string
  token: string
  email: string
  displayName: string
  savedAt: number
}

export interface Prefs {
  currentAccountId: string | null
}

/**
 * 配置池：允许 agent 登录的账号集合。
 *
 * `pool.json` 不存在 = 还没配置过 ⇒ **空池**：账号不会自己进来，
 * 首次使用要人在「账号中心 → 配置池 → 添加账号」里自己挑（用户 2026-10-27 定稿）。
 * 读失败/文件损坏同样回落空池 —— 宁少放行，不默认全放行。
 */
export interface Pool {
  accountIds: string[]
}

export function dataDir(dshHome: string): string {
  return join(dshHome, 'akso-workbench')
}

function readJson<T>(file: string, fallback: T): T {
  if (!existsSync(file)) return fallback
  const raw = readFileSync(file, 'utf8')
  return JSON.parse(raw) as T
}

function writeJson(file: string, value: unknown): void {
  mkdirSync(join(file, '..'), { recursive: true, mode: 0o700 })
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, file)
  try {
    chmodSync(file, 0o600)
  } catch {
    // Windows 上 chmod 基本是空操作
  }
}

export function readSession(dshHome: string): StoredSession | null {
  try {
    const parsed = readJson<Partial<StoredSession> | null>(join(dataDir(dshHome), 'cloud.json'), null)
    if (parsed === null || typeof parsed.token !== 'string' || parsed.token === '') return null
    return {
      baseUrl: String(parsed.baseUrl ?? ''),
      token: parsed.token,
      email: String(parsed.email ?? ''),
      displayName: String(parsed.displayName ?? ''),
      savedAt: Number(parsed.savedAt ?? 0),
    }
  } catch {
    // 文件坏了 = 没登录；不静默删除（留给用户/排障看）
    return null
  }
}

export function writeSession(dshHome: string, session: StoredSession): void {
  writeJson(join(dataDir(dshHome), 'cloud.json'), session)
}

export function clearSession(dshHome: string): void {
  const file = join(dataDir(dshHome), 'cloud.json')
  if (existsSync(file)) writeJson(file, null)
}

export function readPrefs(dshHome: string): Prefs {
  try {
    const parsed = readJson<Partial<Prefs>>(join(dataDir(dshHome), 'prefs.json'), {})
    const id = typeof parsed.currentAccountId === 'string' && parsed.currentAccountId !== '' ? parsed.currentAccountId : null
    return { currentAccountId: id }
  } catch {
    return { currentAccountId: null }
  }
}

export function writePrefs(dshHome: string, prefs: Prefs): void {
  writeJson(join(dataDir(dshHome), 'prefs.json'), prefs)
}

/** `null` = 从来没配置过（调用方按**空池**处理，不是"全部账号"）。 */
export function readPool(dshHome: string): Pool | null {
  try {
    const file = join(dataDir(dshHome), 'pool.json')
    if (!existsSync(file)) return null
    const parsed = readJson<{ accountIds?: unknown }>(file, {})
    const list = Array.isArray(parsed.accountIds) ? parsed.accountIds : []
    const accountIds = list.filter((id): id is string => typeof id === 'string' && id !== '')
    return { accountIds: [...new Set(accountIds)] }
  } catch {
    return null
  }
}

export function writePool(dshHome: string, pool: Pool): void {
  writeJson(join(dataDir(dshHome), 'pool.json'), { accountIds: [...new Set(pool.accountIds)] })
}
