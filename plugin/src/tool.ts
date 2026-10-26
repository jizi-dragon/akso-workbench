/**
 * 给 **agent** 用的只读工具：把「与 GUI 同一份快照」交给模型。
 *
 * ★ 为什么不 `import { defineTool } from '@deepseek-ai/dsh-tools'`：
 *   本包的 node 半边是**自带 bundle**（只 external `node:*`），而 profile 里不会为插件再装一份
 *   DSH 的工具包。`defineTool()` 的产物其实就是一个普通对象
 *   （`{ name, description, parameters(JSON Schema), output:{schema,render}, execute }`），
 *   所以这里直接按这个形状手写 —— 零运行时依赖，也不存在"插件里第二份 registry"的错位。
 *
 * ★ 工具只返回**快照里的公开字段**：没有口令、没有令牌（那些本来也不在这份快照里）。
 */
import type { CloudAccount, CloudBox, CloudSite } from './cloud.ts'

export interface SnapshotView {
  loggedIn: boolean
  revision: number | null
  syncedAt: number | null
  accounts: CloudAccount[]
  sites: CloudSite[]
  boxes: CloudBox[]
  defaultBox: string
  counts: { accounts: number; sites: number; boxes: number }
  /** 已连接到 agent 的账号 id（界面里那个「已连接」） */
  currentAccountId: string | null
}

export interface SnapshotToolDeps {
  read: () => SnapshotView
  /** `refresh:true` 时先回拉一次云端（agent 想确保数据新鲜） */
  refresh: () => Promise<void>
  /** 没登录时给的提示（告诉模型该让人去登录） */
  loginHint: string
}

const PARAMETERS = {
  type: 'object',
  properties: {
    section: {
      type: 'string',
      enum: ['all', 'accounts', 'sites', 'boxes'],
      description: '要看哪一段：all（默认）/ accounts / sites / boxes',
    },
    query: {
      type: 'string',
      description: '按账号、站点、备注、盒子做大小写不敏感的模糊过滤',
    },
    limit: {
      type: 'integer',
      minimum: 1,
      maximum: 200,
      description: '每段最多返回多少条（默认 50）',
    },
    refresh: {
      type: 'boolean',
      description: '先向 akso-cloud 回拉一次再读（默认 false，直接读当前快照）',
    },
  },
  required: [],
  additionalProperties: false,
} as const

function matches(haystack: Array<string | undefined>, needle: string): boolean {
  if (needle === '') return true
  return haystack.some((value) => (value ?? '').toLowerCase().includes(needle))
}

/**
 * 构造工具定义。返回值直接喂给 `ctx.tools.register(...)`。
 */
export function buildSnapshotTool(deps: SnapshotToolDeps) {
  return {
    name: 'akso_workbench_accounts',
    description:
      '读取 akso-workbench 已同步的 akso-cloud 账号库快照（与界面里看到的**同一份**数据）：'
      + '账号（平台账号 / 站点 / 盒子 / 备注）、站点、盒子及其数量。'
      + '只读，不含平台口令与任何令牌。未登录 akso-cloud 时会返回 loggedIn:false 与登录提示。',
    parameters: PARAMETERS,
    output: {
      schema: { type: 'object' },
      render(_args: unknown, value: unknown) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
      },
    },
    async execute(args: unknown): Promise<unknown> {
      const input = (args ?? {}) as { section?: string; query?: string; limit?: number; refresh?: boolean }
      if (input.refresh === true) await deps.refresh()

      const view = deps.read()
      if (!view.loggedIn) {
        return { loggedIn: false, hint: deps.loginHint }
      }

      const q = String(input.query ?? '').trim().toLowerCase()
      const limit = Number.isFinite(input.limit) ? Math.min(200, Math.max(1, Number(input.limit))) : 50
      const want = String(input.section ?? 'all')

      const accounts = view.accounts
        .filter((a) => matches([a.username, a.siteName, a.siteHost, a.note, a.box], q))
        .slice(0, limit)
        .map((a) => ({
          username: a.username,
          tabName: a.tabName,
          site: a.siteName || a.siteHost,
          box: a.box,
          note: a.note,
          status: a.status,
          hasPassword: a.hasPassword,
          connected: a.id === view.currentAccountId,
          id: a.id,
        }))
      const sites = view.sites
        .filter((s) => matches([s.name, s.host, s.note], q))
        .slice(0, limit)
        .map((s) => ({ name: s.name, host: s.host, accountCount: s.accountCount, note: s.note, id: s.id }))
      const boxes = view.boxes
        .filter((b) => matches([b.name, b.siteName], q))
        .slice(0, limit)
        .map((b) => ({ name: b.name, count: b.count, default: b.isDefault, disabled: b.disabled, site: b.siteName ?? '' }))

      const payload: Record<string, unknown> = {
        loggedIn: true,
        revision: view.revision,
        syncedAt: view.syncedAt,
        counts: view.counts,
        defaultBox: view.defaultBox,
        connectedAccountId: view.currentAccountId,
      }
      if (want === 'all' || want === 'accounts') payload.accounts = accounts
      if (want === 'all' || want === 'sites') payload.sites = sites
      if (want === 'all' || want === 'boxes') payload.boxes = boxes
      return payload
    },
  }
}
