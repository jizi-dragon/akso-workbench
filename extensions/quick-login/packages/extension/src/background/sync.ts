/**
 * 桌面同步桥（Akso Workbench）——数据面外移的扩展侧接线。
 *
 * - 每 2s 轮询桌面快照（账号/盒子/站点，凭据为 Fernet 密文 + 密钥），
 *   解密后经消息总线并入 parallelStore（扩展本地仍 AES-GCM 加密落盘）；
 * - 拉取桌面指令（wheel.toggle / par.open）→ 自发消息走现有总线执行；
 * - 桌面不可达时静默跳过：本地数据保持可用（离线回退）。
 *
 * 安全语义（用户定稿）：凭据以「密文 + 密钥」经 127.0.0.1 回环下发，
 * 扩展端仅在内存解密为明文交给既有加密存储，不做任何落盘明文。
 */

const DESKTOP = 'http://127.0.0.1:18765';
const SYNC_INTERVAL_MS = 2000;
const ACCT_MAP_KEY = 'akso:acctMap'; // desktopId → extension accountId
const CURSOR_KEY = 'akso:cmdCursor';

let syncing = false;

/** Fernet 解密（WebCrypto）：token = b64(0x80 | ts8 | iv16 | ct | hmac32)，key[0:16]=AES-128-CBC，key[16:]=HMAC-SHA256 */
async function fernetDecrypt(tokenB64: string, keyB64: string): Promise<string> {
  const b64u = tokenB64.replace(/-/g, '+').replace(/_/g, '/');
  const pad = b64u.length % 4 ? b64u + '='.repeat(4 - (b64u.length % 4)) : b64u;
  const raw = Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
  if (raw.length < 57 || raw[0] !== 0x80) throw new Error('fernet: bad token');

  const keyRaw = Uint8Array.from(atob(keyB64.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
  if (keyRaw.length !== 32) throw new Error('fernet: bad key length');

  const payload = raw.subarray(0, raw.length - 32);
  const mac = raw.subarray(raw.length - 32);

  const hmacKey = await crypto.subtle.importKey(
    'raw', keyRaw.subarray(16), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']
  );
  const macOk = await crypto.subtle.verify('HMAC', hmacKey, mac, payload);
  if (!macOk) throw new Error('fernet: HMAC mismatch');

  const aesKey = await crypto.subtle.importKey('raw', keyRaw.subarray(0, 16), { name: 'AES-CBC' }, false, ['decrypt']);
  const iv = payload.subarray(9, 25);
  const ct = payload.subarray(25);
  const plain = await crypto.subtle.decrypt({ name: 'AES-CBC', iv }, aesKey, ct);
  // PKCS7 去填充
  const view = new Uint8Array(plain);
  const padLen = view[view.length - 1];
  return new TextDecoder().decode(view.subarray(0, view.length - padLen));
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

async function sendRuntime(req: any): Promise<any | null> {
  try {
    return await chrome.runtime.sendMessage(req);
  } catch {
    return null;
  }
}

async function getMap(): Promise<Record<string, string>> {
  const stored = await chrome.storage.local.get(ACCT_MAP_KEY);
  return (stored[ACCT_MAP_KEY] as Record<string, string>) ?? {};
}

async function applySnapshot(snap: any): Promise<void> {
  if (!snap || snap.format !== 'akso-workbench-snapshot' || !snap.fernetKey) return;
  const map = await getMap();
  const applied = await chrome.storage.local.get('akso:appliedSnapshot');
  if (applied['akso:appliedSnapshot'] === snap.generatedAt) return; // 同一快照不重复应用
  await chrome.storage.local.set({ 'akso:appliedSnapshot': snap.generatedAt });

  const existing = await sendRuntime({ kind: 'par.list' });
  const rows: any[] = existing?.kind === 'par.list' && existing.result?.ok ? existing.result.data ?? [] : [];

  for (const item of snap.accounts ?? []) {
    if (!item.host || !item.username || !item.passwordEnc) continue;
    let password: string;
    try {
      password = await fernetDecrypt(item.passwordEnc, snap.fernetKey);
    } catch {
      continue; // 凭据解不开（密钥轮换/篡改）→ 跳过该账号
    }
    const knownId = map[item.desktopId];
    const known = knownId ? rows.find((r) => r.id === knownId) : undefined;

    if (known) {
      await sendRuntime({
        kind: 'par.update',
        id: known.id,
        tabName: item.tabName,
        username: item.username,
        password,
        box: item.box || '',
      });
    } else {
      const res = await sendRuntime({
        kind: 'par.create',
        siteHost: item.host,
        tabName: item.tabName,
        username: item.username,
        password,
        box: item.box || '',
        open: false,
      });
      if (res?.kind === 'par.create' && res.result?.ok) {
        const fresh = await sendRuntime({ kind: 'par.list' });
        const rows2: any[] = fresh?.kind === 'par.list' && fresh.result?.ok ? fresh.result.data ?? [] : [];
        const hit = rows2.find((r) => r.siteHost === item.host && r.username === item.username);
        if (hit) map[item.desktopId] = hit.id;
      }
    }
  }
  await chrome.storage.local.set({ [ACCT_MAP_KEY]: map });

  // 盒子清单 / 默认盒名（以桌面为准）
  const boxes = snap.boxes ?? {};
  const patch: Record<string, unknown> = {};
  if (Array.isArray(boxes.remembered)) patch['ql:boxes'] = boxes.remembered;
  if (boxes.defaultName != null) patch['ql:defaultBox'] = boxes.defaultName;
  if (Object.keys(patch).length) await chrome.storage.local.set(patch);
}

async function pollCommands(): Promise<void> {
  const stored = await chrome.storage.local.get(CURSOR_KEY);
  const after = Number(stored[CURSOR_KEY] ?? 0);
  const data = await getJson(`/extension/commands?after=${after}`);
  if (!data || !Array.isArray(data.commands)) return;
  let cursor = after;
  for (const cmd of data.commands) {
    cursor = Math.max(cursor, Number(cmd.seq) || 0);
    if (cmd.type === 'wheel.toggle') {
      await sendRuntime({ kind: 'wheel.toggle' });
    } else if (cmd.type === 'par.open') {
      const map = await getMap();
      const extId = map[String(cmd.payload?.accountId)];
      if (extId) await sendRuntime({ kind: 'par.open', accountId: extId });
    }
  }
  if (cursor !== after) {
    await chrome.storage.local.set({ [CURSOR_KEY]: cursor });
    await fetch(`${DESKTOP}/extension/ack`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seqs: [cursor] }),
    }).catch(() => undefined);
  }
}

export function startDesktopSync(): void {
  const tick = async () => {
    if (syncing) return;
    syncing = true;
    try {
      const snap = await getJson('/extension/snapshot');
      if (snap) await applySnapshot(snap);
      await pollCommands();
    } catch {
      // 静默：桌面不可达是常态（离线回退本地数据）
    } finally {
      syncing = false;
    }
  };
  void tick();
  setInterval(tick, SYNC_INTERVAL_MS);
}
