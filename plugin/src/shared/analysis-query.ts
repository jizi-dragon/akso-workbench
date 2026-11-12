/**
 * 对象分析页的输入解析：一次可以输入 1–3 个对象 id / code。
 *
 * 只做解析，不发请求。分隔符同时支持中英文逗号、分号、空白和换行，
 * 方便从表格或聊天记录里直接粘贴一串 id。
 */

export const MAX_ANALYSIS_OBJECTS = 3

export interface ParsedAnalysisQueries {
  /** 去重后的查询，保持输入顺序。 */
  queries: string[]
  /** 输入级错误；非空时不应发起分析。 */
  error: string
}

export function parseAnalysisQueries(raw: string): ParsedAnalysisQueries {
  const tokens = raw
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter((item) => item !== '')

  const queries: string[] = []
  const seen = new Set<string>()
  for (const token of tokens) {
    // 对象 code / id 理论上大小写敏感，但同一批里只差大小写的重复输入没有意义。
    const key = token.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    queries.push(token)
  }

  if (queries.length === 0) {
    return { queries, error: '请输入对象 id 或 code' }
  }
  if (queries.length > MAX_ANALYSIS_OBJECTS) {
    return {
      queries,
      error: `一次最多分析 ${MAX_ANALYSIS_OBJECTS} 个对象（当前 ${queries.length} 个）`,
    }
  }
  return { queries, error: '' }
}
