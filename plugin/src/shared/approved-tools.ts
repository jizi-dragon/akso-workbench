/**
 * 实测台账 —— 只有**在验证环境里真跑通过**的接口 id 才会进这里。
 *
 * 这份名单同时被两半边读取：
 *   - node 半边（`config/capabilities.ts`）把它写进能力目录的 `testStatus`；
 *   - 浏览器半边（`client/tools.tsx`）直接用它渲染卡片左下角的徽标。
 * 放成独立小模块是为了改名单后**只重建客户端就能生效**，不用重启 host。
 *
 * 加一条 = 卡片从红色「过期」变成绿色「已验证」。
 * 流程：先把输入（method / path / 参数）给用户看 → 执行 → 把输出给用户看 →
 * 用户点头后再往这里追加 id，重建插件，刷新页面。
 *
 * 最近一次全量核对：2026-11-09，standard-val（公司配置环境），账号 liyulong。
 * 当时目录 47 条，43 条跑通；另有 2 条 OAuth 接口（auth.oauth.login / auth.oauth.refresh）
 * 要浏览器 OAuth 链路、脚本化不了，已按用户要求在 2026-11-09 从目录里删掉，不再出现在卡片上。
 * 同一天又按用户要求删掉 `auth.login`（改为只走 akso-cloud 代登录，agent 不再接触明文口令），
 * 目录 47 → 45 → 44，本名单 43 → 42。
 * 2026-11-09 晚 `layout.save` 经抓包复验通过（LayoutDetail 读全量 → 原地改 → 整份回写），
 * 本名单 42 → 43；当时只剩 `menu.sort` 未验证（全量替换且无读整棵树的接口，误操作会把子菜单移出层级）。
 * 同一天新增 `layout.stable.ids`（GetStableComponentIds）并实测通过：临时 id → 稳定 id，
 * 目录 44 → 45、本名单 43 → 44。工作流建流程与布局落地的实测记录见
 * `docs/CAPABILITY-VERIFICATION-2026-11-09.md`。
 * 2026-11-09 夜新增 3 条生命周期动作能力（`lifecycle.useraction.list`、`lifecycle.enteraction.create`、
 * `lifecycle.enteraction.list`，当时仅抓包确认，未进本名单），并按用户要求删除 `menu.sort`。
 * 2026-11-11 在 standard-val 上真调通过三条能力，用户核对输入/输出后确认；三条均已进本名单。
 * 当前目录 47 条、本名单 47 条，卡片不再有「过期」。
 */
export const APPROVED_TOOL_IDS: ReadonlySet<string> = new Set<string>([
  // 对象
  'object.save',
  'object.field.page',
  'object.fields.outfield',
  // 查询
  'query.object.get',
  'query.fields.list',
  'query.picklist.get',
  'query.lifecycle.status.list',
  // 字段 / 选项集
  'field.save',
  'picklist.save',
  // 布局
  'layout.list',
  'layout.detail',
  'layout.stable.ids',
  'layout.save',
  // 列表布局
  'listlayout.list',
  'listlayout.columns.get',
  'listlayout.columns.set',
  'listlayout.save',
  'listlayout.filter.save',
  'listlayout.filter.get',
  // 生命周期
  'lifecycle.status.create',
  'lifecycle.status.update',
  'lifecycle.useraction.create',
  'lifecycle.useraction.get',
  'lifecycle.useraction.list',
  'lifecycle.enteraction.create',
  'lifecycle.enteraction.list',
  // 工作流
  'workflow.create',
  'workflow.get',
  'workflow.update',
  'workflow.page.list',
  'workflow.status.change',
  'workflow.delete',
  'workflow.steps.list',
  'workflow.step.get',
  'workflow.step.copy',
  'workflow.step.delete',
  'workflow.start.update',
  'workflow.task.add',
  'workflow.task.update',
  'workflow.decision.add',
  'workflow.decision.update',
  'workflow.action.add',
  'workflow.action.update',
  'workflow.controls.list',
  // 菜单
  'menu.submit',
  'menu.group.list',
  // 通知
  'notice.template.list',
])
