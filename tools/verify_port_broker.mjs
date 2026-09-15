// 固定端口 + 占用判定的离线验收（ADR-0012）——不需要 Electron、不需要真跑壳。
//
// 为什么必须单独验：`desktop/ports.js` 的判定直接决定"能不能起来"。两种失败形态都是**静默**的：
//   a) 只看"端口有人应答 / pid 还活着" → 旧实例占端口时认错服务（PITFALLS #20）、
//      pid 回收复用时误报"另一个实例在跑"而把应用锁死（#25）；
//   b) 误判"端口空闲" → 服务端 bind 失败 → 界面是旧的。
// 本脚本用**真占用一个端口**的方式复现这些场景。
//
// 用法：node tools\verify_port_broker.mjs   （**先退出 Workbench**，见下方前置检查）

import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

let pass = 0;
let fail = 0;
const check = (name, ok, extra = '') => {
  if (ok) {
    pass += 1;
    console.log(`PASS  ${name}`);
  } else {
    fail += 1;
    console.log(`FAIL  ${name}${extra ? `  — ${extra}` : ''}`);
  }
};

/** 真占住一个端口（模拟"别的程序/旧实例"），返回释放函数 */
function occupy(port) {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(port, '127.0.0.1', () => resolve(() => new Promise((r) => srv.close(r))));
  });
}

// ports.js 会在 require 时解析数据目录 —— 指到临时目录，绝不碰真实 %APPDATA%
const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'akso-ports-'));
process.env.WORKBENCH_DATA = sandbox;

const broker = require(path.join('..', 'desktop', 'ports.js'));

const PORTS = broker.PORTS;

// ---------------------------------------------------------------- 0. 前置：固定端口必须空闲
// 本脚本靠"真占住固定端口"来验占用判定，因此**必须先确认它们没被占**——
// 否则最容易被"边跑着工作台边验收"触发：判定会把真实占用当成测试占用，
// 后续 list 时直接抛未捕获的 EADDRINUSE 崩掉，报出来的是栈而不是结论。
{
  const busy = [];
  for (const key of ['server', 'cdp', 'control']) {
    // eslint-disable-next-line no-await-in-loop —— 只有三个端口，逐个探
    if (!(await broker.canBind(PORTS[key]))) busy.push(`${key}=${PORTS[key]}`);
  }
  if (busy.length) {
    console.error(
      `✗ 固定端口被占用，无法进行本项验收：${busy.join('、')}\n` +
      '  本脚本用"真占住固定端口"的方式验占用判定，故必须在没有工作台运行时跑。\n' +
      '  请先退出 Workbench（托盘右键退出，或 taskkill /IM Workbench.exe /F ' +
      '/IM WorkbenchServer.exe /F），再重跑本脚本。',
    );
    process.exit(1);
  }
}

// ---------------------------------------------------------------- 1. 端口是固定的
{
  check('三个端口是固定值 18765 / 18766 / 18767',
    PORTS.server === 18765 && PORTS.cdp === 18766 && PORTS.control === 18767,
    JSON.stringify(PORTS));
  check('不再导出协商/顺延 API（ADR-0012 撤回了顺延）',
    broker.negotiate === undefined && broker.MAX_DRIFT === undefined,
    Object.keys(broker).join(','));
  check('全部空闲时 unavailable() 为空',
    (await broker.unavailable()).length === 0);
}

// ---------------------------------------------------------------- 2. 被占时要能点名
{
  const release = await occupy(PORTS.server);
  const busy = await broker.unavailable();
  await release();
  check('server 被占时能报出来', busy.length === 1 && busy[0].key === 'server',
    JSON.stringify(busy));
  check('报的是固定端口号本身（不顺延、不给替代号）',
    busy.length === 1 && busy[0].port === PORTS.server, JSON.stringify(busy));
}

{
  const releases = [];
  for (const key of ['server', 'cdp', 'control']) {
    // eslint-disable-next-line no-await-in-loop
    releases.push(await occupy(PORTS[key]));
  }
  const busy = await broker.unavailable();
  for (const r of releases) {
    // eslint-disable-next-line no-await-in-loop
    await r();
  }
  check('三个端口都被占时全部报出来（一次说清，不逐个试）',
    busy.length === 3, JSON.stringify(busy));
}

// ---------------------------------------------------------------- 3. 公布 / 读取 / 清理
{
  const written = broker.publish('9.9.9-test');
  check('publish 写出公布文件', Boolean(written) && fs.existsSync(broker.PORTS_PATH));
  const back = broker.readPublished();
  check('readPublished 读回同一份端口',
    back && back.ports.server === 18765 && back.ports.cdp === 18766 && back.ports.control === 18767,
    JSON.stringify(back));
  check('公布文件带 app 标识（供他人核对身份）', back.app === 'Workbench', JSON.stringify(back));
  check('公布文件带 pid（供判定"写它的实例还活着吗"）', back.pid === process.pid);
  check('公布文件显式声明"端口固定"（供扩展/工具判断无需探测窗口）', back.fixed === true);

  const cleaned = broker.unpublish();
  check('unpublish 清掉公布文件', cleaned && !fs.existsSync(broker.PORTS_PATH));
}

// ---------------------------------------------------------------- 4. 占用者身份判定
// ⚠ 本节曾有一条用 `System`(pid 4) 当"活着的进程"来验"能识别出另一个实例"——
// 那其实是**假阳性**：pid 4 是系统进程，不是 Workbench。Windows 的 pid 会被回收复用，
// 只验"活着"就会永久误报、应用再也起不来（实测踩到过）。故判据已升级为
// "活着 **且** 映像名是我们"，用例也据此重写。
{
  broker.publish('0.0.1-old');
  const raw = JSON.parse(fs.readFileSync(broker.PORTS_PATH, 'utf8'));
  const write = (pid) => fs.writeFileSync(broker.PORTS_PATH, JSON.stringify({ ...raw, pid }));

  write(process.pid); // 自己写的文件不算"另一个实例"
  check('自己写的公布文件不被当成"另一个实例"', broker.detectRunningInstance() === null);

  write(999999999); // 一个几乎不可能存在的 pid
  check('pid 已死时不再报"另一个实例在跑"', broker.detectRunningInstance() === null);

  // 核心回归：活着的**无关**进程不得被认成另一个实例（pid 复用假阳性）
  const foreignPid = process.pid === 4 ? 5 : 4; // Windows 上 4 = System，恒存在
  write(foreignPid);
  check('活着的无关进程（如 System）不被误认成另一个实例',
    broker.detectRunningInstance() === null, `pid=${foreignPid}`);
  check('陈旧公布文件会被 clearStale 清掉（强杀后 will-quit 不触发的补救）',
    broker.clearStale() === true && !fs.existsSync(broker.PORTS_PATH));

  // 正例：注入"那个 pid 看起来是我们"的探测器（真实映像名不可造假，故用注入验证分支）
  broker.publish('0.0.1-old');
  write(foreignPid);
  check('确实是我们另一个实例时仍能识别（注入探测器）',
    broker.detectRunningInstance(() => true) !== null);
  check('确实是我们另一个实例时 clearStale 不碰它（不能误删活实例的文件）',
    broker.clearStale(() => true) === false && fs.existsSync(broker.PORTS_PATH));

  // 真实身份判据：一个活着的系统进程不是我们
  check('looksLikeOurs 对系统进程判否（真实映像名）',
    broker.isAlive(foreignPid) === true && broker.looksLikeOurs(foreignPid) === false,
    `pid=${foreignPid}`);

  fs.unlinkSync(broker.PORTS_PATH);
  check('公布文件缺失时不误报', broker.detectRunningInstance() === null);

  fs.writeFileSync(broker.PORTS_PATH, '{ 半截 json');
  check('公布文件损坏时不炸、不误报', broker.detectRunningInstance() === null);
  fs.unlinkSync(broker.PORTS_PATH);
}

// ---------------------------------------------------------------- 5. 提示文案可执行
{
  const ours = broker.describeOccupant('server', {
    pid: 1234, version: '0.3.5', startedAt: '2026-09-14T10:00:00+08:00',
    ports: { server: 18765, cdp: 18766, control: 18767 },
  });
  check('占用提示含 PID 与版本（用户据此找到该退哪个）',
    ours.includes('1234') && ours.includes('0.3.5'), ours);
  check('占用提示给出可执行动作', ours.includes('退出'), ours);
  check('占用提示报固定端口号', ours.includes('18765'), ours);

  const foreign = broker.describeOccupant('cdp', null);
  check('非本实例占用时如实说明并给出排查命令',
    foreign.includes('18766') && foreign.includes('Get-NetTCPConnection'), foreign);
  check('非本实例占用时不谎称"是 Workbench 实例"',
    !foreign.includes('正在运行的 Workbench 实例'), foreign);

  // 旧版本留下的顺延记录：如实报出来（那是用户要找的进程）
  const legacy = broker.describeOccupant('server', {
    pid: 42, version: '0.3.4', startedAt: 'x',
    ports: { server: 18770, cdp: 18771, control: 18772 },
  });
  check('旧版本记录的顺延端口会如实提示（不掩盖）',
    legacy.includes('18765') && legacy.includes('18770'), legacy);
}

// ---------------------------------------------------------------- 6. Python 侧一致性
// 由 `tests/test_ports_contract.py::test_python_defaults_match_shell_ports` 覆盖：
// 那边能直接 `import workbench.config`（真值），不必在这里用正则解析 Python 源码
// （试过：`PORT` 那条是 `env or env or "默认值"` 的跨行写法，正则越改越脆）。
// 本节只确认"壳侧声明了固定端口"，跨语言一致性交给 pytest。

fs.rmSync(sandbox, { recursive: true, force: true });

console.log(`\nPORT_BROKER_CHECKS: ${pass}/${pass + fail}`);
process.exit(fail ? 1 : 0);
