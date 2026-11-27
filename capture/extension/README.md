# capture 最小翻译扩展

这是给 `akw-capture launch` 用的**翻译专用**扩展，不是完整 akso-pass。

演示时想要的只有一件事：在输入框里按一下快捷键，把中文（或英文）翻好并复制到剪贴板，直接粘贴。

## 保留的能力

- 输入框、文本域、`contenteditable` 获得焦点后按 **Ctrl+X**（macOS 为 Cmd+X）。
- 有选中文本时翻译选中部分；没有选中时翻译整个输入。
- 译文只写入剪贴板，**不改原输入**；用户自己 Ctrl+V 粘贴。
- 方向自动判断：
  - 含中文 → 中文译英文
  - 不含中文 → 英文译中文
- 右下角提示：翻译中 / 成功（带一小段译文预览，超过 160 字截断，剪贴板里仍是全文）/ 失败原因。

## 刻意去掉的能力

账号中心、账号计数、换源、并行页、翻译面板、浮标、引擎设置、使用历史、使用统计、登录态、页面数据采集。

## 翻译引擎（2026-11-27 与 akso-pass 4.6.6 同步）

只剩两路，按顺序串行，前一路失败才用下一路：

1. **有道词典**（`dict.youdao.com/jsonapi`，取网络释义首条）
   查词用，20–50ms。它是**词典**不是翻译器：查不到、或给出 `take action /measures /steps to do sth`
   这种斜杠并列 / `sth`·`sb` 占位符的结果时，判定不可用，继续下一路。超时 3s。
2. **MyMemory**（`api.mymemory.translated.net`）
   翻整句用，实测 0.4–2.1s。会把「额度用尽」当成正常结果返回，代码里拦住 `MYMEMORY WARNING` / `QUOTA`。超时 8s。

**已移除**（原 1.0.0 里的）：

- 腾讯 TranSmart —— 服务 2026 年停运，`transmart.qq.com:443` 现在连不上，每次翻译要白等满 8 秒超时。
- Lingva —— `lingva.ml` 现在 20/20 返回 Cloudflare 403。

## 输出整形（命名规范）

只有**短词/短语**才转命名规范，整句保持句子形态 —— 演示时这两种场景都会遇到：

- 短词（单行、≤12 字、无句读，译文不超过 4 个单词）⇒ 转 `snake_case`，可以直接当字段名 / API 参数名；
- 整句 / 多行 / 含句读 ⇒ 保持英文句子，只把首字母大写；
- 目标是中文时不做任何整形，原样返回。

`snake_case` 的规则（与 akso-pass 一致）：

- 含换行的段落原样保留（多行是段落，不是字段名）；
- 括号内容拆成独立词：`Quality Deviation (QD)` → `quality_deviation_qd`；
- 连字符 / 斜杠 / 点 / 空白 → 下划线，只保留 `a-z0-9_`；
- 超过 60 字符按完整单词截断；短于 3 字符保留原文；
- `gmp` / `sop` / `capa` / `qa` / `api` 这类词整体保留，不会被拆开。

## 结果校验（这些情况算失败，不是成功）

- 返回为空；
- 返回与原文完全相同（引擎没识别出源语言时会这样）；
- 只有词典释义（斜杠并列 / `sth`·`sb`）；
- 目标是英语但结果里仍有中文。

## 加载方式

`akw-capture launch` 默认加载本目录：

```powershell
node capture/bin/akw-capture.mjs launch
```

也可以显式指定：

```powershell
node capture/bin/akw-capture.mjs launch --extension D:\ai_assistant\Akso-AI\akso-workbench\capture\extension
```

如需临时使用完整 akso-pass，仍可：

```powershell
node capture/bin/akw-capture.mjs launch --extension D:\ai_assistant\Akso-AI\akso-pass\dist
```

## 权限说明

- `clipboardWrite`：写译文到剪贴板。
- `host_permissions`：只包含两个翻译接口域名（`dict.youdao.com`、`api.mymemory.translated.net`）。
- `content_scripts` 只注入 http/https 页面；Chrome 内部页、扩展页不注入。

只发送当前输入框的文本；不发送页面 URL、cookie、账号信息。

## 手工加载

1. `chrome://extensions` 打开开发者模式。
2. 「加载已解压的扩展程序」选择本目录。
3. 回到页面，在输入框按 Ctrl+X。

## 版本

- 1.0.0：TranSmart → MyMemory → Lingva 三路。
- 1.1.0（2026-11-27）：与 akso-pass 4.6.6 同步 —— 移除 TranSmart / Lingva，改有道 → MyMemory；
  英语结果按命名规范转 `snake_case`；成功提示带译文预览。
