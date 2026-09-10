/**
 * 桌面同步桥（Akso Workbench）——数据面外移的扩展侧接线。v2：直连 parallelStore。
 *
 * - 每 2s 轮询桌面快照（账号/盒子/站点，凭据为 Fernet 密文 + 密钥）→ 内存解密 →
 *   直连 parallelStore 增删改（本地仍 AES-GCM 加密落盘）；
 * - 幂等：snapshotId（内容哈希）未变则跳过；
 * - 指令：拉取桌面指令（wheel.toggle / par.open）→ parallelSession.open 切换；
 * - 桌面不可达时静默跳过：本地数据保持可用（离线回退）。
 *
 * 安全语义（用户定稿）：凭据以「密文 + 密钥」经 127.0.0.1 回环下发，
 * 扩展端仅在内存解密为明文交给既有加密存储，不做任何落盘明文。
 */

import { credentials } from './core/credentials';
import { parallelSession } from './core/parallel-session';
import { parallelStore } from './core/parallel-store';
import { toggleAccountWheel } from './account-wheel';

const DESKTOP = 'http://127.0.0.1:18765';
const SYNC_INTERVAL_MS = 2000;
const ACCT_MAP_KEY = 'akso:acctMap'; // desktopId → extension accountId
const SNAPSHOT_ID_KEY = 'akso:snapshotId';

let syncing = false;
let tickCount = 0;

/** Fernet 解密（WebCrypto）：token = b64(0x80 | ts8 | iv16 | ct | hmac32)。
 *  Fernet 规范：sign-key = key[0:16]（HMAC-SHA256），enc-key = key[16:32]（AES-128-CBC）——
 *  曾写反两半导致扩展端全部账号解密失败被静默跳过（0.2.6 实锤断点）。 */
async function fernetDecrypt(tokenB64: string, keyB64: string): Promise<string> {
  const b64u = tokenB64.replace(/-/g, '+').replace(/_/g, '/');
  const pad = b64u.length % 4 ? b64u + '='.repeat(4 - (b64u.length % 4)) : b64u;
  const raw = Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
  if (raw.length < 57 || raw[0] !== 0x80) throw new Error('fernet: bad token');

  const keyRaw = Uint8Array.from(
    atob(keyB64.replace(/-/g, '+').replace(/_/g, '/')),
    (c) => c.charCodeAt(0)
  );
  if (keyRaw.length !== 32) throw new Error('fernet: bad key length');

  const payload = raw.subarray(0, raw.length - 32);
  const mac = raw.subarray(raw.length - 32);

  const hmacKey = await crypto.subtle.importKey(
    'raw',
    keyRaw.subarray(0, 16),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );
  const macOk = await crypto.subtle.verify('HMAC', hmacKey, mac, payload);
  if (!macOk) throw new Error('fernet: HMAC mismatch');

  const aesKey = await crypto.subtle.importKey(
    'raw',
    keyRaw.subarray(16, 32),
    { name: 'AES-CBC' },
    false,
    ['decrypt']
  );
  const iv = payload.subarray(9, 25);
  const ct = payload.subarray(25);
  const plain = await crypto.subtle.decrypt({ name: 'AES-CBC', iv }, aesKey, ct);
  // WebCrypto AES-CBC 已自动去除 PKCS7 填充——再按尾字节手工剥离会把口令尾字符当
  // 填充长度剥掉（曾把 "88888888" 剥成空串，0.2.6 实锤断点之二）
  return new TextDecoder().decode(plain);
}

async function getJson(path: string): Promise<any | null> {
  try {
    const resp = await fetch(`${DESKTOP}${path}`, { cache: 'no-store' });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null; // 桌面不可达 → 离线回退
  }
}

async function getMap(): Promise<Record<string, string>> {
  const stored = await chrome.storage.local.get(ACCT_MAP_KEY);
  return (stored[ACCT_MAP_KEY] as Record<string, string>) ?? {};
}

async function saveMap(map: Record<string, string>): Promise<void> {
  await chrome.storage.local.set({ [ACCT_MAP_KEY]: map });
}

/** 应用快照（幂等）：创建/更新/删除账号 + 盒子清单与默认盒名 */
async function applySnapshot(snap: any): Promise<void> {
  if (!snap || snap.format !== 'akso-workbench-snapshot' || !snap.fernetKey) return;
  const stored = await chrome.storage.local.get(SNAPSHOT_ID_KEY);
  if (stored[SNAPSHOT_ID_KEY] === snap.snapshotId) return; // 内容未变，跳过

  const map = await getMap();
  const snapshotIds = new Set<string>();

  for (const item of snap.accounts ?? []) {
    if (!item.host || !item.username || !item.passwordEnc || !item.desktopId) continue;
    snapshotIds.add(item.desktopId);
    let password: string;
    try {
      password = await fernetDecrypt(item.passwordEnc, snap.fernetKey);
    } catch (e) {
      // 凭据解不开（密钥轮换/篡改）→ 跳过该账号；务必留痕，否则密钥轮换后全员停更无迹象
      console.warn('[akso-sync] 凭据解密失败，跳过账号:', item.username, e);
      continue;
    }
    const tabName = item.tabName || item.username; // tabName 缺失护栏：undefined 会写坏档案

    const knownId = map[item.desktopId];
    const cur = knownId ? await parallelStore.get(knownId).catch(() => undefined) : undefined;

    if (cur) {
      // 原位更新：盒名/页签名可原位；用户名或密码变更走删除重建（store 无用户名更新接口）
      if (cur.box !== (item.box || '')) {
        await parallelStore.updateBox(cur.id, item.box || '');
      }
      if (cur.tabName !== tabName) {
        await parallelStore.updateTabName(cur.id, tabName);
      }
      if (cur.username !== item.username) {
        await parallelStore.delete(cur.id);
        const account = await parallelStore.create({
          siteHost: item.host, tabName, username: item.username,
          password, box: item.box || '', scheme: item.scheme === 'http' ? 'http' : 'https',
        });
        map[item.desktopId] = account.id;
        await saveMap(map);
      } else {
        const current = await credentials.decryptCredentials(cur.credentials!).catch(() => undefined);
        if (!current || current.password !== password) {
          await parallelStore.updateCredentials(
            cur.id,
            await credentials.encryptCredentials(item.username, password)
          );
        }
      }
    } else {
      const account = await parallelStore.create({
        siteHost: item.host, tabName, username: item.username,
        password, box: item.box || '', scheme: item.scheme === 'http' ? 'http' : 'https',
      });
      map[item.desktopId] = account.id;
      await saveMap(map);
    }
  }

  // 删除同步：桌面已移除的账号（映射存在但快照不再包含）。
  // 方向性护栏：快照为空且本地仍有映射时按"瞬时异常"处理，不清删
  //（曾会因桌面瞬时空/半量快照把本地账号全量删光）
  if ((snap.accounts ?? []).length > 0) {
    for (const [desktopId, extId] of Object.entries(map)) {
      if (!snapshotIds.has(desktopId)) {
        await parallelStore.delete(extId).catch(() => undefined);
        delete map[desktopId];
      }
    }
  }
  await saveMap(map);

  // 盒子清单 / 默认盒名 / 禁用盒（以桌面为准）
  const boxes = snap.boxes ?? {};
  const patch: Record<string, unknown> = {};
  if (Array.isArray(boxes.remembered)) patch['ql:boxes'] = boxes.remembered;
  if (boxes.defaultName != null) patch['ql:defaultBox'] = boxes.defaultName;
  if (Array.isArray(boxes.disabled)) patch['ql:disabledBoxes'] = boxes.disabled;
  if (Object.keys(patch).length) await chrome.storage.local.set(patch);

  await chrome.storage.local.set({ [SNAPSHOT_ID_KEY]: snap.snapshotId });
}

async function pollCommands(): Promise<void> {
  const stored = await chrome.storage.local.get('akso:cmdCursor');
  const after = Number(stored['akso:cmdCursor'] ?? 0);
  const data = await getJson(`/extension/commands?after=${after}`);
  if (!data || !Array.isArray(data.commands)) return;
  let cursor = after;
  const map = await getMap();

  for (const cmd of data.commands) {
    cursor = Math.max(cursor, Number(cmd.seq) || 0);
    // 逐条隔离：单条失败不得阻塞后继指令，更不能阻止 cursor 持久化 + ack
    //（曾因 open 抛错穿出循环 → 每 2s 无限重试、队列整体卡死）
    try {
      if (cmd.type === 'wheel.toggle') {
        // SW 内 chrome.runtime.sendMessage 不投递给自身上下文（死链）→ 直调
        await toggleAccountWheel();
      } else if (cmd.type === 'par.open') {
        const extId = map[String(cmd.payload?.accountId)];
        if (extId) {
          await parallelSession.open(extId, false);
        } else {
          console.warn('[akso-sync] par.open 映射缺失，指令作废:', cmd.payload?.accountId);
        }
      }
    } catch (e) {
      console.warn('[akso-sync] 指令执行失败（已跳过）:', cmd.type, e);
    }
  }
  if (cursor !== after) {
    await chrome.storage.local.set({ 'akso:cmdCursor': cursor });
    await fetch(`${DESKTOP}/extension/ack`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seqs: [cursor] }),
    }).catch(() => undefined);
  }
}

async function tick(): Promise<void> {
  if (syncing) return;
  syncing = true;
  try {
    const snap = await getJson('/extension/snapshot');
    if (snap) await applySnapshot(snap);
    await pollCommands();
    // 状态回传（每 3 个 tick ≈6s）：桌面账号中心四态徽标的数据源
    tickCount += 1;
    if (tickCount % 3 === 0) await reportState();
  } catch {
    // 静默：桌面不可达是常态（离线回退本地数据）
  } finally {
    syncing = false;
  }
}

/** 执行面状态上报：desktopId → 绑定页签数 / token / 授权暂停（四态徽标数据源） */
async function reportState(): Promise<void> {
  const map = await getMap();
  const rev = new Map<string, string>();
  for (const [desktopId, extId] of Object.entries(map)) rev.set(extId, desktopId);
  if (!rev.size) return;
  const accounts = await parallelStore.list();
  const items = [];
  for (const account of accounts) {
    const desktopId = rev.get(account.id);
    if (!desktopId) continue;
    const st = parallelSession.statusOf(account);
    // 不上报 enforcementOff：桌面无授权管理入口，该徽标徒增困惑（用户定稿移除）
    items.push({
      desktopId,
      tabs: st.tabIds.length,
      hasToken: st.hasToken,
    });
  }
  if (!items.length) return;
  await fetch(`${DESKTOP}/extension/state`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  }).catch(() => undefined);
}

export function startDesktopSync(): void {
  // MV3 SW 空闲 ~30s 会被杀，setInterval 随之消失 → 指令滞留队列。
  // chrome.alarms 是唯一的复活通道：alarm 触发时 SW 被拉起并跑一次同步。
  void chrome.alarms.create('akso:sync', { periodInMinutes: 0.5, delayInMinutes: 0.5 });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'akso:sync') void tick();
  });
  void tick();
  setInterval(tick, SYNC_INTERVAL_MS);
}
