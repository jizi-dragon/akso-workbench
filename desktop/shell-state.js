// 壳状态落盘（跨进程单向通道：Electron 写 → Python 服务读）
//
// 为什么要这个文件：账号中心的版本角标/更新提示需要「壳自己才知道」的事实
// （当前版本、是否发现新版、下载进度、更新是否已就绪）。渲染层与 sidecar 同源
// （18765），而壳的控制服务在 18767 —— 让 sidecar 直接读一个 JSON 文件，
// 比再多一条跨端口 HTTP 链路更简单也更耐故障（壳没跑时文件是陈旧的 → 标记 live=false）。
//
// 路径必须与 workbench/config.py 的 DATA_DIR 保持同一套规则（WORKBENCH_DATA >
// MOGUL_DATA > %APPDATA%/Workbench），否则开发态（WORKBENCH_DATA 指向别处）
// 会读到另一个目录。
//
// ⚠️ 2026-09-16 更名：目录名 AksoWorkbench → Workbench。**数据库由 Python 侧自动接管**
// （config.legacy_db_candidates 会复制旧库），但壳自己写在旧目录里的三样东西**不迁移**：
// `proxy.txt`（强制代理/直连开关）、`shell-state.json`（陈旧即可，会被新目录覆盖）、
// `runtime/browser-states/`（浏览器登录态，重登即可）。旧目录不会自动删除。

const fs = require('fs');
const path = require('path');

function dataDir() {
  const env = process.env.WORKBENCH_DATA || process.env.MOGUL_DATA;
  if (env) return env;
  if (process.env.APPDATA) return path.join(process.env.APPDATA, 'Workbench');
  return path.join(require('os').homedir(), '.workbench');
}

const STATE_PATH = path.join(dataDir(), 'shell-state.json');
let last = null;
let lastWriteAt = 0;

/** 原子写：先写临时文件再 rename，避免服务端读到半截 JSON（它每次刷新都会读） */
function write(payload) {
  const body = { ...payload, at: Date.now() };
  const text = JSON.stringify(body, null, 2);
  if (text === last && Date.now() - lastWriteAt < 30_000) return; // 内容不变则不重复写盘
  try {
    fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true });
    const tmp = `${STATE_PATH}.tmp`;
    fs.writeFileSync(tmp, text, 'utf8');
    fs.renameSync(tmp, STATE_PATH);
    last = text;
    lastWriteAt = Date.now();
  } catch {
    // 写盘失败不影响壳功能（UI 退化为「未知」）
  }
}

module.exports = { write, dataDir, STATE_PATH };
