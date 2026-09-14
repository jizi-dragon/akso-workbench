// 端口协商与公布（ADR-0011）——壳是唯一仲裁者
//
// 为什么需要这个文件：改造前 18765/18766/18767 三个端口被壳、Python 服务端、浏览器扩展
// **各自硬编码**，没有任何一方负责裁决"谁在用"。后果是实测过的故障：旧实例占着 18765，
// 新壳探到"有 HTTP 200"就认为就绪，于是主窗打开的是**旧实例**的页面 —— 表现成
// "代码改了但 UI 没变"，排查成本极高（完整复盘见 docs/adr/0011）。
//
// 本模块的职责边界：
//   1. 选端口（优先固定基准，被占则顺延）——**不做全局随机**：固定端口对使用者更友好，
//      随机只会把"我要打开工作台"变成"我得先查它这次在哪个口"；
//   2. 把实际选中值**公布**到 runtime/desktop-ports.json（服务端、工具、排障都读它）；
//   3. 启动时读旧文件，识别"已有另一个实例在跑"并给出可执行提示。

const fs = require('fs');
const net = require('net');
const path = require('path');

const shellState = require('./shell-state');

/** 端口基准：与历史版本保持一致（改了会让既有书签/扩展失效） */
const BASE = { server: 18765, cdp: 18766, control: 18767 };
/** 顺延上限：每个端口最多试这么多个号。够躲开偶发占用，又不会把号段铺得太远 */
const MAX_DRIFT = 8;

const PORTS_PATH = path.join(shellState.dataDir(), 'runtime', 'desktop-ports.json');

/** 探测单个端口是否可绑（绑上立刻放掉——真正的监听由各自的进程/服务去做） */
function canBind(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

/**
 * 从基准端口起找一个能绑且尚未被本进程预留的号。
 *
 * 顺序有讲究：**先探 canBind、再看 taken**。反过来（先看 taken 就 continue）会漏探——
 * 而 taken 里的号通常正是"另一个端口已经占了"的那个，于是出现三级连带顺延：
 * server 被占 → 拿到 18766 → cdp 因 18766 在 taken 里而跳到 18767 → control 再跳到 18768。
 * 实测复现过（tools/verify_port_broker.mjs 有一条专门钉住它）。
 *
 * @returns {Promise<{port:number, drifted:boolean}>}
 */
async function pickPort(base, taken) {
  for (let i = 0; i <= MAX_DRIFT; i += 1) {
    const port = base + i;
    if (taken.has(port)) continue; // 本进程已预留（等价于已被占，无需再探）
    // 顺延是串行的：必须按顺序取第一个可用，故这里刻意不并行
    if (await canBind(port)) return { port, drifted: i > 0 };
  }
  return { port: 0, drifted: true };
}

/**
 * 选齐三个端口。**顺序有意义**：先服务端（扩展默认找它）→ CDP → 控制服务，
 * 且用 taken 集合互斥，避免三个端口挑到同一个号（号段相近，不互斥就会撞）。
 */
async function negotiate() {
  const taken = new Set();
  const ports = {};
  const drifted = [];
  for (const key of ['server', 'cdp', 'control']) {
    // 同上：需要按序取号
    const { port, drifted: moved } = await pickPort(BASE[key], taken);
    if (!port) return { ok: false, failedAt: key, ports: { ...BASE } };
    ports[key] = port;
    if (moved) drifted.push(`${key} ${BASE[key]}→${port}`);
    taken.add(port);
  }
  return { ok: true, ports, drifted };
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
 * 实测就是这个形态：对话框报 PID 3572 / System(pid 4) 之类的无关进程占着 18765。
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
function publish(ports, version) {
  const payload = {
    app: 'AksoWorkbench',
    schemaVersion: 1,
    pid: process.pid,
    version: version || null,
    startedAt: new Date().toISOString(),
    ports,
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
 * 给用户看的占用提示。**两条硬要求**：
 *   1. 报**实际被占的那个端口号**（从 `prev.ports` 取），不是 `BASE[key]`。
 *      实测踩过：端口顺延后对话框仍印 18765，用户照它去查会查到空气——
 *      而"端口信息不可信"正是 ADR-0011 要根治的病，不能在提示文案里复现。
 *   2. 给出**可执行**的动作（退出哪个进程）。
 */
function describeOccupant(key, prev) {
  if (!prev) return `端口 ${BASE[key]} 已被其它程序占用。`;
  const ports = (prev.ports || {})[key];
  const actual = Number.isInteger(ports) && ports > 0 ? ports : BASE[key];
  const from = actual === BASE[key] ? '' : `（基准 ${BASE[key]} 被占后顺延至此）`;
  const all = prev.ports || {};
  return (
    `端口 ${actual}${from} 已被另一个 AksoWorkbench 实例占用：\n` +
    `  PID ${prev.pid}，版本 ${prev.version || '未知'}，启动于 ${prev.startedAt || '未知'}\n` +
    `  它占用的端口：server=${all.server} cdp=${all.cdp} control=${all.control}\n\n` +
    `请先退出那个实例再启动本程序。`
  );
}

module.exports = {
  BASE,
  MAX_DRIFT,
  PORTS_PATH,
  canBind,
  pickPort,
  negotiate,
  readPublished,
  detectRunningInstance,
  clearStale,
  publish,
  unpublish,
  describeOccupant,
  isAlive,
  looksLikeOurs,
};
