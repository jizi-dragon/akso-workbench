// Node 逐步复刻 sync.ts fernetDecrypt，定位失败步骤
const BASE = 'http://127.0.0.1:18765';

const snap = await (await fetch(`${BASE}/extension/snapshot`)).json();

async function fernetDecryptDebug(tokenB64, keyB64, verbose = false) {
  const b64u = tokenB64.replace(/-/g, '+').replace(/_/g, '/');
  const pad = b64u.length % 4 ? b64u + '='.repeat(4 - (b64u.length % 4)) : b64u;
  const raw = Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
  if (verbose) console.log('  token bytes:', raw.length, 'first:', raw[0]);
  if (raw.length < 57 || raw[0] !== 0x80) throw new Error('fernet: bad token');

  const keyRaw = Uint8Array.from(
    atob(keyB64.replace(/-/g, '+').replace(/_/g, '/')),
    (c) => c.charCodeAt(0)
  );
  if (verbose) console.log('  key bytes:', keyRaw.length);
  if (keyRaw.length !== 32) throw new Error('fernet: bad key length');

  const payload = raw.subarray(0, raw.length - 32);
  const mac = raw.subarray(raw.length - 32);

  const hmacKey = await crypto.subtle.importKey(
    'raw', keyRaw.subarray(0, 16), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']
  );
  const macOk = await crypto.subtle.verify('HMAC', hmacKey, mac, payload);
  if (verbose) console.log('  hmacOk:', macOk);
  if (!macOk) throw new Error('fernet: HMAC mismatch');

  const aesKey = await crypto.subtle.importKey(
    'raw', keyRaw.subarray(16, 32), { name: 'AES-CBC' }, false, ['decrypt']
  );
  const iv = payload.subarray(9, 25);
  const ct = payload.subarray(25);
  const plain = await crypto.subtle.decrypt({ name: 'AES-CBC', iv }, aesKey, ct);
  const view = new Uint8Array(plain);
  if (verbose) console.log('  明文hex:', Buffer.from(view).toString('hex'), 'len:', view.length);
  // WebCrypto 已自动去 PKCS7 填充，无需手工剥离
  return new TextDecoder().decode(view);
}

const a = snap.accounts[0];
try {
  const pwd = await fernetDecryptDebug(a.passwordEnc, snap.fernetKey, true);
  console.log('RESULT: 解密成功, len =', pwd.length);
} catch (e) {
  console.log('RESULT: 失败 →', e.message);
}
