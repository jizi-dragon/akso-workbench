import type { EncryptedCredentials } from '../../shared/types';

/**
 * 加密存储账号密码。
 * 使用 crypto.subtle AES-GCM 加密，密钥派生自设备绑定标识（chrome.storage.local 中的随机种子）。
 * 注意：此方案为设备绑定密钥，更换设备或清除扩展数据后密码将无法恢复。
 */
export const credentials = {
  /** 获取或生成加密密钥种子 */
  async getKeySeed(): Promise<string> {
    const stored = await chrome.storage.local.get('sb:encryptionSeed');
    let seed = stored['sb:encryptionSeed'] as string | undefined;
    if (!seed) {
      seed = crypto.randomUUID();
      await chrome.storage.local.set({ 'sb:encryptionSeed': seed });
    }
    return seed;
  },

  /** 从种子派生加密密钥 */
  async deriveKey(seed: string): Promise<CryptoKey> {
    const encoder = new TextEncoder();
    const keyMaterial = await crypto.subtle.importKey(
      'raw',
      encoder.encode(seed),
      { name: 'PBKDF2' },
      false,
      ['deriveKey'],
    );
    const salt = encoder.encode('sessionbox-salt-v1');
    return crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt,
        iterations: 100000,
        hash: 'SHA-256',
      },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
  },

  /** 加密单个字符串 */
  async encryptValue(plaintext: string, key: CryptoKey): Promise<{ encrypted: string; iv: string }> {
    const encoder = new TextEncoder();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      key,
      encoder.encode(plaintext),
    );
    return {
      encrypted: btoa(String.fromCharCode(...new Uint8Array(ciphertext))),
      iv: btoa(String.fromCharCode(...iv)),
    };
  },

  /** 解密单个字符串 */
  async decryptValue(encrypted: string, iv: string, key: CryptoKey): Promise<string> {
    const ciphertext = Uint8Array.from(atob(encrypted), (c) => c.charCodeAt(0));
    const ivBytes = Uint8Array.from(atob(iv), (c) => c.charCodeAt(0));
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: ivBytes },
      key,
      ciphertext,
    );
    return new TextDecoder().decode(plaintext);
  },

  /** 加密账号密码 */
  async encryptCredentials(username: string, password: string): Promise<EncryptedCredentials> {
    const seed = await this.getKeySeed();
    const key = await this.deriveKey(seed);
    const { encrypted: encUsername, iv: ivUsername } = await this.encryptValue(username, key);
    const { encrypted: encPassword, iv: ivPassword } = await this.encryptValue(password, key);
    return {
      encryptedUsername: encUsername,
      encryptedPassword: encPassword,
      iv: ivUsername,
      ivPassword,
      encryptedAt: Date.now(),
    };
  },

  /** 解密账号密码 */
  async decryptCredentials(creds: EncryptedCredentials): Promise<{ username: string; password: string }> {
    const seed = await this.getKeySeed();
    const key = await this.deriveKey(seed);
    const username = await this.decryptValue(creds.encryptedUsername, creds.iv, key);
    const password = await this.decryptValue(creds.encryptedPassword, creds.ivPassword, key);
    return { username, password };
  },
};