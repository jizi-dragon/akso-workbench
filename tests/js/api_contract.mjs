// 前端 API 客户端的行为契约（Node 直跑，不需要浏览器）。
//
// ## 为什么需要这个文件
//
// `workbench/static/js/api.js` 是 Python→JS 的无类型接口。2026-10-19 之前
// `jsonFetch` 不序列化对象 body，于是 `stopChat` 实际发出 `"[object Object]"`：
// 服务端 422，前端 catch 后把按钮退回"停止"，**用户看到的是点了没反应**。
// 973 条 Python 测试与全部端到端脚本都没有覆盖这一个字节。
//
// 这里用 Node 的 `Response` + 假 fetch 直接驱动**真实的 api.js**：
// 断言的是"发出去的请求长什么样"，不是"源码里有没有 JSON.stringify"。
//
// 用法：node tests/js/api_contract.mjs
// 输出：一行 JSON（由 tests/test_frontend_contract.py 解析并断言）
// 失败：抛异常 → 非零退出码。

import { api } from "../../workbench/static/js/api.js";

const calls = [];

globalThis.fetch = async (url, options = {}) => {
  calls.push({
    url: String(url),
    method: options.method || "GET",
    headers: options.headers || {},
    body: options.body === undefined ? null : String(options.body),
  });
  return new Response(JSON.stringify({ ok: true, running: false }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

await api.stopChat("conv-1");
await api.saveSettings({ model: "deepseek-flash" });

// 已序列化的字符串 body 不得被二次编码（回归：统一入口容易顺手 JSON.stringify 两次）
await api.savePrompt("请只回一句话");

console.log(JSON.stringify({ calls }));
