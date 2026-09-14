// 端口协商的离线验收（ADR-0011）——不需要 Electron、不需要真跑壳。
//
// 为什么必须单独验：`desktop/ports.js` 的选号逻辑直接决定"能不能起来"，而它的失败形态是
// **静默**的（挑到已被占用的号 → 服务端 bind 失败 → 旧实例应答 → 界面是旧的）。
// 本脚本用**真占用一个端口**的方式复现该场景，验证顺延与"旧实例在跑"的判定。
//
// 用法：node tools\verify_port_broker.mjs

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

/** 真占住一个端口（模拟"旧实例"），返回释放函数 */
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

const BASE = broker.BASE;

// ---------------------------------------------------------------- 0. 前置：基准端口必须是空闲的
// 本脚本靠「真占住基准端口」来验顺延，因此**必须先确认基准端口没被占**——
// 否则最容易被"边跑着工作台边验收"触发：第 1 节会把真实占用误判成顺延、
// 第 3 节 list 时直接抛未捕获的 EADDRINUSE 崩掉，报出来的是栈而不是结论。
{
  const busy = [];
  for (const key of ['server', 'cdp', 'control']) {
    // eslint-disable-next-line no-await-in-loop —— 只有三个端口，逐个探
    if (!(await broker.canBind(BASE[key]))) busy.push(`${key}=${BASE[key]}`);
  }
  if (busy.length) {
    console.error(
      `✗ 基准端口被占用，无法进行本项验收：${busy.join('、')}\n` +
      '  本脚本用"真占住基准端口"的方式验顺延，故必须在没有工作台运行时跑。\n' +
      '  请先退出 Akso Workbench（托盘右键退出，或 taskkill /IM AksoWorkbench.exe /F ' +
      '/IM AksoServer.exe /F），再重跑本脚本。',
    );
    process.exit(1);
  }
}

// ---------------------------------------------------------------- 1. 无占用时取基准号
{
  const res = await broker.negotiate();
  check('无占用时三个端口都取基准值',
    res.ok && res.ports.server === BASE.server && res.ports.cdp === BASE.cdp
      && res.ports.control === BASE.control,
    JSON.stringify(res));
  check('无占用时不报告顺延', res.drifted.length === 0, JSON.stringify(res.drifted));
  check('三个端口互不相同',
    new Set(Object.values(res.ports)).size === 3, JSON.stringify(res.ports));
}

// ---------------------------------------------------------------- 2. 基准被占 → 只动该端口
{
  // 占一个**不在三个基准里**、但落在顺延窗口内的号：它不该影响任何一个端口的首选值
  const release = await occupy(BASE.control + 1);
  const res = await broker.negotiate();
  await release();
  check('窗口内被占用的无关号不影响首选端口',
    res.ok && res.ports.server === BASE.server && res.ports.cdp === BASE.cdp
      && res.ports.control === BASE.control,
    JSON.stringify(res));
  check('没被影响就不报告顺延', res.drifted.length === 0, JSON.stringify(res.drifted));
}

// ---------------------------------------------------------------- 3. 基准被占 → 整块上移且互不撞号
{
  const release = await occupy(BASE.server);
  const res = await broker.negotiate();
  await release();
  check('server 基准被占时顺延到下一个可用号',
    res.ok && res.ports.server === BASE.server + 1, JSON.stringify(res));
  check('三个端口仍互不相同（顺延不得让两个端口撞号）',
    new Set(Object.values(res.ports)).size === 3, JSON.stringify(res.ports));
  check('顺延会如实报告', res.drifted.some((d) => d.startsWith('server')), JSON.stringify(res.drifted));
  check('所有端口都落在基准窗口内',
    Object.entries(res.ports).every(([k, v]) => v >= BASE[k] && v <= BASE[k] + broker.MAX_DRIFT),
    JSON.stringify(res.ports));
}

// ---------------------------------------------------------------- 4. 全窗口被占 → 明确失败
{
  const releases = [];
  for (let i = 0; i <= broker.MAX_DRIFT; i += 1) {
    // eslint-disable-next-line no-await-in-loop —— 逐个占号，必须串行
    releases.push(await occupy(BASE.server + i));
  }
  const res = await broker.negotiate();
  for (const r of releases) {
    // eslint-disable-next-line no-await-in-loop
    await r();
  }
  check('整个顺延窗口都被占时明确失败（而不是挑一个假可用）', res.ok === false, JSON.stringify(res));
  check('失败时点名是哪个端口', res.failedAt === 'server', JSON.stringify(res));
}

// ---------------------------------------------------------------- 5. 公布 / 读取 / 清理
{
  const ports = { server: 19001, cdp: 19002, control: 19003 };
  const written = broker.publish(ports, '9.9.9-test');
  check('publish 写出公布文件', Boolean(written) && fs.existsSync(broker.PORTS_PATH));
  const back = broker.readPublished();
  check('readPublished 读回同一份端口', back && back.ports.server === 19001, JSON.stringify(back));
  check('公布文件带 app 标识（供他人核对身份）', back.app === 'AksoWorkbench', JSON.stringify(back));
  check('公布文件带 pid（供判定"写它的实例还活着吗"）', back.pid === process.pid);

  const cleaned = broker.unpublish();
  check('unpublish 清掉公布文件', cleaned && !fs.existsSync(broker.PORTS_PATH));
}

// ---------------------------------------------------------------- 5. 旧实例判定
// ⚠ 本节原有一条用 `System`(pid 4) 当"活着的进程"来验"能识别出另一个实例"——
// 那其实是**假阳性**：pid 4 是系统进程，不是 AksoWorkbench。Windows 的 pid 会被回收复用，
// 只验"活着"就会永久误报、应用再也起不来（实测踩到过）。故本节的判据已升级为
// "活着 **且** 映像名是我们"，用例也据此重写。
{
  broker.publish({ server: 18765, cdp: 18766, control: 18767 }, '0.0.1-old');
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
  broker.publish({ server: 18765, cdp: 18766, control: 18767 }, '0.0.1-old');
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

// ---------------------------------------------------------------- 6. 提示文案可执行
{
  const text = broker.describeOccupant('server', {
    pid: 1234, version: '0.3.5', startedAt: '2026-09-14T10:00:00+08:00',
    ports: { server: 18765, cdp: 18766, control: 18767 },
  });
  check('占用提示含 PID 与版本（用户据此找到该退哪个）',
    text.includes('1234') && text.includes('0.3.5'), text);
  check('占用提示给出动作', text.includes('退出'), text);
}

// ---------------------------------------------------------------- 7. 提示里的端口号必须是**实际**那个
{
  // 顺延后的实例：它实际在 18770/18771/18772，提示必须报 18770 而不是基准 18765
  const text = broker.describeOccupant('server', {
    pid: 4242, version: '0.3.5', startedAt: 'x',
    ports: { server: 18770, cdp: 18771, control: 18772 },
  });
  check('顺延实例的提示报实际端口（不是基准号）', text.includes('18770'), text);
  // 只对**第一行**（真正报告"哪个端口被占"的那行）判，并剔除刻意保留的来由说明
  // 「（基准 18765 被占后顺延至此）」——那是解释，不是谎报。
  const headLine = text.split('\n')[0].replace(/（[^）]*）/g, '');
  check('顺延实例的提示行不把基准号当成被占端口',
    headLine.includes('18770') && !headLine.includes(String(BASE.server)),
    headLine);
  check('顺延实例的提示标明"顺延"来由', text.includes('顺延'), text);

  const baseText = broker.describeOccupant('server', {
    pid: 4242, version: '0.3.5', startedAt: 'x',
    ports: { server: 18765, cdp: 18766, control: 18767 },
  });
  check('未顺延时提示不画蛇添足说"顺延"', !baseText.includes('顺延'), baseText);

  const legacyText = broker.describeOccupant('server', { pid: 7, version: null, ports: {} });
  check('公布文件无端口信息时回落到基准号且不炸', legacyText.includes(String(BASE.server)), legacyText);
}

fs.rmSync(sandbox, { recursive: true, force: true });

console.log(`\nPORT_BROKER_CHECKS: ${pass}/${pass + fail}`);
process.exit(fail ? 1 : 0);
