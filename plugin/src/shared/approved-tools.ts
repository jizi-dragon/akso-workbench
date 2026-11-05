/**
 * 实测台账 —— 只有**用户在测试中逐条确认过**的接口 id 才会进这里。
 *
 * 这份名单同时被两半边读取：
 *   - node 半边（`config/capabilities.ts`）把它写进能力目录的 `testStatus`；
 *   - 浏览器半边（`client/tools.tsx`）直接用它渲染卡片左下角的徽标。
 * 放成独立小模块是为了改名单后**只重建客户端就能生效**，不用重启 host。
 *
 * 加一条 = 卡片从红色「过期」变成绿色「已验证」。
 * 流程：先把输入（method / path / 参数）给用户看 → 执行 → 把输出给用户看 →
 * 用户点头后再往这里追加 id，重建插件，刷新页面。
 */
export const APPROVED_TOOL_IDS: ReadonlySet<string> = new Set<string>([
  // 例：'query.object.get',
])
