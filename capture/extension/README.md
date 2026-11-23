# capture 最小翻译扩展

这是给 `akw-capture launch` 用的**翻译专用**扩展，不是完整 akso-pass。

## 保留的能力

- 输入框、文本域、`contenteditable` 获得焦点后按 **Ctrl+X**（macOS 为 Cmd+X）。
- 有选中文本时翻译选中部分；没有选中时翻译整个输入。
- 译文只写入剪贴板，**不改原输入**；用户自己 Ctrl+V 粘贴。
- 方向自动判断：
  - 含中文 → 中文译英文
  - 不含中文 → 英文译中文
- 右下角只显示一行状态提示（翻译中/成功/失败）。

## 刻意去掉的能力

账号中心、账号计数、换源、并行页、翻译面板、浮标、引擎设置、使用统计、登录态、页面数据采集。

## 翻译引擎

依次尝试，前一失败才用下一路：

1. 腾讯 TranSmart（主）
2. MyMemory（海外备用）
3. Lingva（最终兜底）

只发送当前输入框的文本；不发送页面 URL、cookie、账号信息。

## 加载方式

`akw-capture launch` 默认加载本目录：

```powershell
node capture/bin/akw-capture.mjs launch
```

也可以显式指定：

```powershell
node capture/bin/akw-capture.mjs launch --extension D:\DDUP\Akso_AI\akso-workbench\capture\extension
```

如需临时使用完整 akso-pass，仍可：

```powershell
node capture/bin/akw-capture.mjs launch --extension D:\DDUP\Akso_AI\akso-pass\dist
```

## 权限说明

- `clipboardWrite`：写译文到剪贴板。
- `host_permissions`：只包含三个翻译接口域名。
- `content_scripts` 只注入 http/https 页面；Chrome 内部页、扩展页不注入。

## 手工加载

1. `chrome://extensions` 打开开发者模式。
2. 「加载已解压的扩展程序」选择本目录。
3. 回到页面，在输入框按 Ctrl+X。
