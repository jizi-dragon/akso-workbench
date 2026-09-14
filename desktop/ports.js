// 端口固定 + 占用判定（ADR-0012 取代 ADR-0011 §D-1）
//
// 历史上这里做过"壳当仲裁者、被占则顺延"（ADR-0011 §D-1）。**2026-09-14 撤回**：
// 顺延带来的一整套活动部件（协商窗口、实际端口公布、扩展按窗口探测、书签失效）
// 换来的收益，抵不上它引入的故障面——用户定稿：**就固定端口号**，
// 端口被占时直接给出可执行的错误，而不是悄悄换个号继续跑。
//
// 本模块现在只做三件事，且都是"只读判定 + 固定事实"：
//   1. 声明三个固定端口（与 Python 侧 config.py 的默认值一致）；
//   2. 启动前核对**谁占着端口**——是我们自己的另一个实例？还是无关程序？给出可执行提示；
//   3. 把固定端口**公布**到 runtime/desktop-ports.json（供服务端自证、工具与排障读取）。
//
// ⚠ 仍然保留"核对身份"这条判据（ADR-0011 §D-4 的遗产，与顺延无关）：
// 只看"端口有人应答 / pid 还活着"会踩两种坑——旧实例占着端口导致新壳认错服务
// （docs/PITFALLS.md #20），以及 pid 回收复用导致误报"另一个实例在跑"而把应用锁死
// （#25）。所以判定必须问"**是不是我们**"，不能只问"有没有东西在"。

const fs = require('fs');
const net = require('net');
const path = require('path');

const shellState = require('./shell-state');

/**
 * 三个固定端口。**改了就是破坏性变更**：扩展的兜底地址、既有书签、运维脚本都指向它们。
 * 唯一真相来源仍是这里（Python 侧 `workbench/config.py` 的默认值必须与之相同）。
 */
const PORTS = { server: 18765, cdp: 18766, control: 18767 };
/** 兼容旧调用点的别名（ADR-0011 时代叫 BASE） */
const BASE = PORTS;

const PORTS_PATH = path.join(shellState.dataDir(), 'runtime', 'desktop-ports.json');

/** 探测单个端口是否可绑（绑上立刻放掉——真正的监听由各自的进程/服务去做） */
function canBind(port) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once('error', () => resolve(false));
    s.once('listening', () => s.close(() => resolve(true)));
    s.listen(port, '127.0.0.1');
  });
}

/** 三个端口里谁不可绑（空数组 = 全部可用）。顺序即报错时的优先提示顺序。 */
async function unavailable() {
  const busy = [];
  for (const key of ['server', 'cdp', 'control']) {
    // 串行：只有三个端口，且报错信息要稳定（并发会让"谁先失败"变得不确定）
    if (!(await canBind(PORTS[key]))) busy.push({ key, port: PORTS[key] });
  }
  return busy;
}

/** 读上一次公布的文件（可能不存在 / 半截 / 不是我们写的） */
function readPublished() {
  try {
    const data = JSON.parse(fs.readFileSync(PORTS_PATH, 'utf8'));
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

/**
 * 本进程的"映像名"集合——用于判定"这个 pid 是不是我们自己人"。
 *
 * 为什么必须核对**身份**而不只是"活着"：Windows 的 pid 会被回收复用，所以
 * "公布文件里的 pid 仍存在" **不等于** "那个实例还在跑"。一旦撞上一个无关进程，
 * 判据就永久为真 → 每次启动都弹「另一个实例在运行」，用户选了「退出」就再也起不来。
 * 实测就是这个形态：对话框报的 PID 属于系统/无关进程。
 *
 * 打包态：映像名 = 产品名；开发态（`npm start` / `electron .`）：映像是 electron，
 * 而 `node desktop/main.js` 则是 node —— 三种都放进来，宁可漏报也不误报。
 */
function ownImageNames() {
  const names = new Set([path.basename(process.execPath).toLowerCase()]);
  if (!process.defaultApp) names.add('aksoworkbench.exe');
  names.add('electron.exe');
  names.add('node.exe');
  return names;
}

/**
 * 查一个 pid 的映像名（Windows 用 tasklist；其余平台返回 null 表示"未知"）。
 * 与 `isAlive` 合并成一次调用，避免每个判据各起一次进程。
 */
function imageNameOf(pid) {
  try {
    const { execFileSync } = require('child_process');
    const out = execFileSync('tasklist', ['/FI', `PID eq ${pid}`, '/NH', '/FO', 'CSV'], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 5000,
    });
    const m = /^"([^"]+)"/.exec(out.trim());
    return m ? m[1].toLowerCase() : null;
  } catch {
    return null; // 非 Windows / tasklist 不可用 → 身份未知，交给 isAlive 兜底
  }
}

/**
 * pid 是否活着（公布文件是不是"某个进程"写的）。
 *
 * ⚠ 这只是**必要条件，不是充分条件**——要判定"是我们另一个实例"，必须再过一遍
 * `looksLikeOurs()`。单用本函数就是上面所说的 pid 复用假阳性。
 */
function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  if (process.platform === 'win32') {
    // Windows 走 tasklist：`process.kill(pid, 0)` 在 Node 里对同用户进程**恒不抛错**，
    // 对已消失的 pid 也不一定抛，判不准；tasklist 的输出同时给出映像名，一次调用两用。
    return imageNameOf(pid) !== null;
  }
  try {
    process.kill(pid, 0); // 信号 0 = 只探测存在性
    return true;
  } catch (e) {
    // EPERM = 进程存在但没权限（跨用户）→ 仍算活着
    return e && e.code === 'EPERM';
  }
}

/** 那个 pid 是否**看起来是我们自己的另一个实例**（活着 + 映像名对得上） */
function looksLikeOurs(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  if (!isAlive(pid)) return false;
  if (process.platform !== 'win32') return true; // 非 Windows 拿不到映像名，退回"活着即认"
  const name = imageNameOf(pid);
  return Boolean(name) && ownImageNames().has(name);
}

/**
 * 启动前的自检：上一次的公布文件还在，且写它的进程**确实是我们的另一个实例**？
 *
 * 判据升级史（都是真机踩出来的）：
 *   v1「端口上有人回 200」→ 旧实例占端口时新壳认错服务（ADR-0011 §1）；
 *   v2「公布文件里的 pid 还活着」→ pid 被回收复用时**永久误报**，应用再也起不来；
 *   v3「活着 **且** 映像名是我们」→ 本函数。宽松侧仍有残留（开发态 node 进程会误认），
 *      但那需要"我们自己 publish 过、进程却已不是工作台"这一小概率组合；
 *      而**误报**的代价是应用完全打不开，远大于漏报。
 */
function detectRunningInstance(probe) {
  const prev = readPublished();
  if (!prev) return null;
  if (prev.pid === process.pid) return null;
  const looks = probe || looksLikeOurs;
  if (!looks(prev.pid)) return null;
  return prev;
}

/**
 * 清理**陈旧**的公布文件：文件还在，但写它的实例已经不在了（或那个 pid 已被无关进程复用）。
 *
 * 为什么不能只靠 `unpublish()`：它挂在 `will-quit`，而应用被强杀（taskkill /F、崩溃）时
 * **不会触发** → 文件残留成陈旧数据，下次启动就可能触发误报对话框（本函数就是为此而生）。
 * 返回 true 表示确实清掉了一份陈旧文件。
 */
function clearStale(probe) {
  const prev = readPublished();
  if (!prev) return false;
  if (prev.pid === process.pid) return false;
  const looks = probe || looksLikeOurs;
  if (looks(prev.pid)) return false; // 真的有实例在跑 → 不碰
  try {
    fs.unlinkSync(PORTS_PATH);
    return true;
  } catch {
    return false;
  }
}

/** 原子写公布文件（先写临时文件再 rename —— 服务端可能随时来读） */
function publish(version) {
  const payload = {
    app: 'AksoWorkbench',
    schemaVersion: 1,
    pid: process.pid,
    version: version || null,
    startedAt: new Date().toISOString(),
    ports: { ...PORTS },
    fixed: true, // 显式声明：端口是固定的，不因占用而顺延（ADR-0012）
  };
  try {
    fs.mkdirSync(path.dirname(PORTS_PATH), { recursive: true });
    const tmp = `${PORTS_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf8');
    fs.renameSync(tmp, PORTS_PATH);
    return payload;
  } catch {
    return null; // 写盘失败不阻断启动（服务端会回落到环境变量/默认值）
  }
}

/**
 * 退出时清理公布文件。**必须核对 pid**：否则"新实例启动后旧实例才退出"的场景下，
 * 旧实例会把新实例刚写的文件删掉（又制造一次"端口信息不可信"）。
 */
function unpublish() {
  try {
    const cur = readPublished();
    if (cur && cur.pid !== process.pid) return false;
    fs.unlinkSync(PORTS_PATH);
    return true;
  } catch {
    return false;
  }
}

/**
 * 端口被占时的可执行提示：说清**是哪个端口、被谁占、该怎么办**。
 *
 * `prev` = 公布文件内容（若那个实例确实是我们的）。端口号取自 `PORTS`（固定端口，
 * 不存在"实际协商值"这种第二真相），但如果 `prev` 里记的端口与当前不一致
 * （例如旧版本留下 0.3.5 之前的顺延值），**如实报出来**——那正是用户要找的进程。
 */
function describeOccupant(key, prev) {
  const port = PORTS[key];
  const lines = [`端口 ${port}（${key}）已被占用。`];
  if (prev) {
    const theirs = (prev.ports || {})[key];
    const extra = Number.isInteger(theirs) && theirs > 0 && theirs !== port
      ? `（该实例记录自己用的是 ${theirs}）` : '';
    lines.push(
      `  占用者是一个正在运行的 Akso Workbench 实例：`,
      `  PID ${prev.pid}，版本 ${prev.version || '未知'}，启动于 ${prev.startedAt || '未知'}${extra}`,
      '',
      '请先退出那个实例再启动本程序（托盘图标右键 → 退出）。',
    );
  } else {
    lines.push(
      '  占用者不是 Akso Workbench 实例（公布文件里没有指向它的记录）。',
      '',
      `请先找出占用 ${port} 的程序（管理员 PowerShell：`,
      `  Get-NetTCPConnection -LocalPort ${port} | Select-Object OwningProcess`,
      '  Get-Process -Id <上一步的 OwningProcess>）并结束它，或改用别的端口段后重试。',
    );
  }
  return lines.join('\n');
}

module.exports = {
  PORTS,
  BASE,
  PORTS_PATH,
  canBind,
  unavailable,
  readPublished,
  detectRunningInstance,
  clearStale,
  publish,
  unpublish,
  describeOccupant,
  isAlive,
  looksLikeOurs,
};
