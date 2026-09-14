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
{
  broker.publish({ server: 18765, cdp: 18766, control: 18767 }, '0.0.1-old');
  const raw = JSON.parse(fs.readFileSync(broker.PORTS_PATH, 'utf8'));

  raw.pid = process.pid; // 自己写的文件不算"另一个实例"
  fs.writeFileSync(broker.PORTS_PATH, JSON.stringify(raw));
  check('自己写的公布文件不被当成"另一个实例"', broker.detectRunningInstance() === null);

  raw.pid = 999999999; // 一个几乎不可能存在的 pid
  fs.writeFileSync(broker.PORTS_PATH, JSON.stringify(raw));
  check('pid 已死时不再报"另一个实例在跑"', broker.detectRunningInstance() === null);

  raw.pid = process.pid === 4 ? 5 : 4; // 现成存在的 pid（system）；Windows 上 4 = System
  fs.writeFileSync(broker.PORTS_PATH, JSON.stringify(raw));
  const detected = broker.detectRunningInstance();
  check('pid 仍活着时能识别出"另一个实例在跑"', detected !== null, JSON.stringify(raw.pid));

  fs.unlinkSync(broker.PORTS_PATH);
  check('公布文件缺失时不误报', broker.detectRunningInstance() === null);

  fs.writeFileSync(broker.PORTS_PATH, '{ 半截 json');
  check('公布文件损坏时不炸、不误报', broker.detectRunningInstance() === null);
  fs.unlinkSync(broker.PORTS_PATH);
}

// ---------------------------------------------------------------- 6. 提示文案可执行
{
  const text = broker.describeOccupant('server', { pid: 1234, version: '0.3.5', startedAt: '2026-09-14T10:00:00+08:00', ports: {} });
  check('占用提示含 PID 与版本（用户据此找到该退哪个）',
    text.includes('1234') && text.includes('0.3.5'), text);
  check('占用提示给出动作', text.includes('退出'), text);
}

fs.rmSync(sandbox, { recursive: true, force: true });

console.log(`\nPORT_BROKER_CHECKS: ${pass}/${pass + fail}`);
process.exit(fail ? 1 : 0);
