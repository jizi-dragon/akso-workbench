# DeepSeek API 能力核查（模型 / 缓存 / 联网搜索）

_2026-09-15 核对官方文档。**只记录有出处的事实**，每条带链接；推断与建议单独标注。_

> **这份文档回答三个问题**（都来自实际决策，不是泛泛调研）：
> ① `deepseek-v4.1-flash` 是什么、我们该用哪个模型名；
> ② DeepSeek API 有没有**联网搜索**能力（用户问过，且这决定要不要自建）；
> ③ 上下文缓存怎么命中、怎么度量——它决定我们**花多少钱**。

## 1. 模型：`deepseek-flash` = DeepSeek-V4.1-Flash

| 项 | 事实 | 出处 |
|---|---|---|
| 现行模型名 | **`deepseek-flash`**（V4.1-Flash）、`deepseek-v4-pro`（V4-Pro-0813） | [模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) |
| 退役情况 | V4 Flash 与 V4 Flash Vision Exp **已退役**；为兼容，`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` **临时**路由到 V4.1-Flash，按 Flash 价格计费 | [更新日志 2026-09-10](https://api-docs.deepseek.com/zh-cn/updates) |
| 上下文长度 | 1M（最大输出 384K） | [模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) |
| 思考模式 | 默认开启；`reasoning.effort` 支持 `none/low/high/max` | [Responses API 参考](https://api-docs.deepseek.com/api/create-response) |
| 多模态 | 支持图片输入（`input_image`） | [使用 Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api) |
| 工具调用 | 支持 `function`（**仅** `function`） | 同上 |

**本工作台的做法**：`workbench/services/deepseek.py` 把默认模型改为 `deepseek-flash`，
并加 `normalize_model()` 把历史名（`deepseek-v4-flash` 等）**在读设置时就归一**。
理由不是"新版更好"，而是**旧名靠一条"临时"兼容路由活着**——它随时可能被撤掉，
而撤掉的当天表现为 404，排查成本远高于现在改一行。归一化对用户行为无影响
（同一个模型、同一个价格），只是名字对了。

## 2. 联网搜索：**API 不支持**（两处文档互证）

| 接口 | 官方原话 | 出处 |
|---|---|---|
| Responses API | Tools 表：`web_search` / `file_search` / `code_interpreter` / `computer_use` / `mcp` 等内置工具 → **忽略** | [使用 Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api) |
| Responses API 参考 | `tools` 参数："**Built-in tool types are ignored**" | [Responses API 参考](https://api-docs.deepseek.com/api/create-response) |
| Chat Completions | 只支持 `function` 类型的工具 | 同上 |

**一处容易误读的注记**（原文）：

> 注：`input` 中回传的 `web_search_call` item（例如**旧模型**此前请求产生的搜索结果）
> 仍会被还原并拼接进上下文。

这句话**不是**"支持联网搜索"，而是三条信息的合体：
① 搜索结果作为**输入**被容忍（拼接进上下文）；
② 提到的是"**旧模型**此前请求产生的"——说明这部分能力**曾经存在、现已移除**；
③ 服务端**不会**因为你在 `tools` 里写 `web_search` 就去搜（那一格明写"忽略"）。

> **结论（对决策直接可用）**：想让 Agent 联网，只有一条路——**自己实现一个 `function` 工具**。
> 本工作台因此自建 `web_search`（`workbench/plugins/websearch.py`）。

### 2.1 自建检索通道的实测选型（2026-09-15，本机国内网络）

DeepSeek 不给搜索，就得自己找一条能用的检索通道。各后端打 6 次的结果：

| 后端 | 成功率 | 平均耗时 | 结论 |
|---|---|---|---|
| `www.bing.com` RSS | 4/6 | ~3.0s | ✅ **主通道**：结构化 XML + **真实直链** |
| `cn.bing.com` RSS | 2/6 | ~8.2s | ❌ 换域名不解决问题 |
| `www.baidu.com/s` | 6/6 | ~0.4s | ❌ 返回 **1.4KB 反爬页**（无结果结构） |
| `www.sogou.com/web` | 6/6 | ~0.6s | ❌ 同上，5.4KB 反爬页 |
| `www.so.com/s`（360） | 6/6 | ~0.7s | ⚠️ **兜底**：466KB 真结果，但链接是需解跳转的 `so.com/link?m=…` |
| `lite.duckduckgo.com` | 1/6 | ~3.1s | ❌ 基本不可用 |

**"成功率高"不等于"能用"**：百度/搜狗是 6/6，但返回的是反爬页——它们没有失败，
只是**给了你一个没有任何结果的页面**。这个区别很关键：只看状态码会得出完全错误的结论。

### 2.2 三个反直觉的实测结论（都改掉了初版实现）

1. **重试反而更糟。** 初版看到 ~20% 失败率就加了 3 次重试 + 0.4s 退避，结果成功率
   没提高（仍 80%），失败耗时却从 ~4s 涨到 ~10s。原因是失败**突发成簇**
   （连打 8 次可能前 4 次全 ERR 然后全 OK），重试全落在同一个窗口里。
   改成 2 次重试后连打 8 次实测 **8/8 成功、平均 1.1s**。
2. **不能把"解不开的跳转地址"当结果交出去。** 360 的 `so.com/link?m=…` 里那个 `m`
   会过期（事后重放实测 HEAD/GET 都返回 400）。若把废链接交给模型，它会拿去调
   `read_web_page` 再失败一次，还会据此引用一个自己根本没读到的页面——
   **一次检索失败变成两次，外加一次幻觉引用**。现在解不开的条目直接丢弃。
3. **"没有结果"与"通道挂了"必须分开。** 都返回空列表的话，模型会把"通道不可达"
   读成"网上没有这东西"，然后据此下结论。现在通道失败**必须明确报错**，
   并在错误里给出 `read_web_page` 兜底建议。

> 兜底通道（360）的诚实评价：它几乎总是返回**很少甚至零条**可用链接（跳转多已失效），
> 所以它的真实价值是"主通道挂了时，给一个明确的降级答复而不是硬错误"。
> 这一点写进了工具返回的 `note` 字段，模型看得到。


## 3. 上下文硬盘缓存：命中规则与度量

出处：[上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)、
[模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)。

### 3.1 价格差（`deepseek-flash`，1M input tokens）

| | 缓存命中 | 缓存未命中 | 倍率 |
|---|---|---|---|
| 非高峰 | $0.003 | $0.15 | **50×** |
| 高峰 | $0.006 | $0.30 | **50×** |

（高峰时段：周一至周五 01:00–04:00 与 06:00–10:00 UTC；其余为非高峰，价格为高峰的一半。）

**这 50 倍就是"缓存机制值得做"的全部理由。** 命中率从 0 提到 70%，
输入成本降到约三分之一。

### 3.2 命中条件（硬约束）

> 后续请求必须**完整匹配**一个已持久化的**缓存前缀单元**（cache prefix unit）。

前缀单元在三个地方产生：
1. **请求边界**：每次请求在**用户输入末尾**与**模型输出末尾**各产生一个；
2. **公共前缀检测**：系统发现多次请求的公共前缀时，把它持久化为独立单元；
3. **固定 token 间隔**：长输入/输出按间隔切分，避免长前缀永远无法命中。

> ⚠️ **前缀里改一个字节，从那一字节起全部算 miss。** 这条决定了 Agent 循环的写法。

### 3.3 度量（官方给了字段，别丢）

| 接口 | 字段 |
|---|---|
| Chat Completions | `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `prompt_tokens` |
| Responses API | `usage.input_tokens` + `usage.input_tokens_details.cached_tokens` |

流式响应要**显式打开** `stream_options.include_usage` 才会带上 usage。
`prompt_cache_key` / `prompt_cache_retention` **不支持**（缓存全自动管理）——
也就是说**没有"手动打缓存标记"这回事，只能靠保持前缀稳定**。

官方也明说缓存是 **best-effort**、不保证 100% 命中，且缓存几小时到几天不用就清。

## 4. 本工作台的落地（对应 `workbench/cache.py`）

| 官方规则 | 我们的做法 |
|---|---|
| 前缀必须完整匹配 | 请求组装成 **append-only**：`[system] + [冻结摘要] + [原文历史] + [新消息]`，已发出的前缀永不改写（`cache.plan_prefix`） |
| 每轮重算摘要 = 前缀每轮都变 | 摘要**按边界冻结**（`conversation_context.summarized_upto`，只前进），只在超预算时折叠一次 |
| usage 里有命中量 | 强制 `include_usage`，`UsageStats` 累计并落 `deepseek_usage` 表；前端回答旁显示命中率 |
| 看不到就没法优化 | 设置页「缓存与用量」面板 + `GET /api/agent/cache`，含摘要重算次数（前缀被打断的直接证据） |

**一条纪律**（写在这里防止将来被"顺手优化"掉）：
**不要在运行时按轮次拼装 system 提示词**。system 是每个请求的第一个字节，
按轮次拼 = 每轮都让缓存从头失效。用户改提示词是低频动作，可以接受一次失效；
按轮次动态拼装则是每轮都失效。

## 5. 未验证 / 需要真机确认的事项

- **实际命中率**：本机尚未用真实 API Key 跑过完整对话，设置页显示的数字要等真机跑起来才作数。
- **`stream_options.include_usage` 在 DeepSeek 上的确切行为**：本实现按 OpenAI 兼容协议处理
  （最后一帧只带 usage），并且**同时**接受 usage 出现在带 choices 的帧里；两种形态都能吃下。
- **公共前缀检测的粒度**：官方未给出"多少次请求算公共"，因此本项目不依赖它——
  我们靠的是**自己保证前缀稳定**，不赌服务端的检测。
- **200 万 token 以上长会话**：官方提到"固定 token 间隔"切分缓存单元，但未给间隔值；
  超长会话的命中行为未实测。
