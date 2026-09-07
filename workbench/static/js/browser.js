import { api, el, logLine, badgeFor } from './modules-common.js';

const log = el('al-log');
const ACCOUNT_ID = new URLSearchParams(location.search).get('account') || '';

async function checkPlaywright() {
  const badge = el('pw-health');
  try {
    const data = await api('/api/browser/check');
    badge.className = `status-badge ${data.ok ? 'ok' : 'err'}`;
    badge.textContent = data.ok ? 'chromium 就绪' : data.detail;
  } catch (e) {
    badge.className = 'status-badge err';
    badge.textContent = `体检失败：${e.message}`;
  }
}

async function loadAccountsAndSessions() {
  const [accData, sessData] = await Promise.all([
    api('/api/accounts'),
    api('/api/browser/sessions'),
  ]);
  const sessions = new Map(sessData.sessions.map((s) => [s.account_id, s]));
  const wall = el('session-wall');
  wall.innerHTML = '';
  if (!accData.accounts.length) {
    wall.innerHTML = '<div class="empty" style="grid-column:1/-1">暂无账号——先到统一账号库添加</div>';
    return;
  }
  for (const a of accData.accounts) {
    const s = sessions.get(a.id);
    const status = s ? s.status : 'stopped';
    const badgeCls = badgeFor(status) || (s ? 'warn' : '');
    const card = document.createElement('div');
    card.className = 'acard';
    card.innerHTML = `
      <div class="row1">
        <div class="avatar">${a.username.slice(0, 1).toUpperCase()}</div>
        <div>
          <div class="name">${a.username}</div>
          <div class="env">${a.env_name}${a.env_base_url ? ` · ${a.env_base_url}` : ''}</div>
        </div>
        <div style="flex:1"></div>
        <span class="status-badge ${badgeCls}">${status}</span>
      </div>
      ${s ? `<div class="env">标题：${s.title || '—'}${s.has_token ? ' · 已捕获 token' : ''}</div>` : ''}
      ${s && s.detail ? `<div class="env">${s.detail}</div>` : ''}
      <div class="actions">
        ${s && s.status !== 'stopped'
          ? '<button class="mbtn ghost" data-act="close">■ 关闭会话</button>'
          : '<button class="mbtn" data-act="open">🚀 启动并自动登录</button>'}
      </div>`;
    const btn = card.querySelector('button');
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        if (btn.dataset.act === 'open') {
          logLine(log, `▶ 启动 ${a.username} 的托管浏览器…`, 'sys');
          const result = await api('/api/browser/open', { method: 'POST', body: { account_id: a.id } });
          logLine(log, `■ ${a.username}：${result.detail || result.status}`,
            result.status === 'error' ? 'err' : 'ok');
        } else {
          await api(`/api/browser/close/${a.id}`, { method: 'POST' });
          logLine(log, `■ 已关闭 ${a.username}`, 'sys');
        }
      } catch (e) {
        logLine(log, `✗ ${a.username}：${e.message}`, 'err');
      } finally {
        btn.disabled = false;
        loadAccountsAndSessions().catch(() => {});
      }
    };
    wall.appendChild(card);
  }
}

async function refreshLoop() {
  try {
    const [sessData] = await Promise.all([api('/api/browser/sessions'), loadAccountsAndSessions()]);
    for (const s of sessData.sessions) {
      if (s.autologin && s.status !== 'stopped') {
        logLine(log,
          `[${s.account_id.slice(0, 6)}] ${s.status} · phase=${s.autologin.phase} ` +
          `attempts=${s.autologin.attempts} errors=${s.autologin.errors}` +
          (s.autologin.reason ? ` · ${s.autologin.reason}` : ''),
          s.status === 'error' ? 'err' : s.status === 'online' ? 'ok' : '');
      }
    }
  } catch { /* 服务暂不可达，下轮再试 */ }
}

checkPlaywright().then(refreshLoop).catch((e) => logLine(log, `✗ 初始化失败：${e.message}`, 'err'));
setInterval(refreshLoop, 2000);
