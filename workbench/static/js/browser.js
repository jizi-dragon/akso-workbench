import { api, el, logLine, badgeFor } from './modules-common.js';

const log = el('al-log');
// quick-login SESSION_COLORS（轮盘色环语义）
const COLORS = ['#1E6FFF', '#0FA3B1', '#7C5CFF', '#FF7A1A', '#22C55E'];

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

function colorOf(index) { return COLORS[index % COLORS.length]; }

async function loadAccountsAndSessions() {
  const [accData, sessData] = await Promise.all([
    api('/api/accounts'),
    api('/api/browser/sessions'),
  ]);
  const sessions = new Map(sessData.sessions.map((s) => [s.account_id, s]));
  const savedMap = new Map(await Promise.all(accData.accounts.map(async (a) => {
    const saved = await api(`/api/browser/saved/${a.id}`).catch(() => ({ saved: false }));
    return [a.id, saved.saved];
  })));
  renderWheel(accData.accounts, sessions);
  renderCards(accData.accounts, sessions, savedMap);
}

function renderWheel(accounts, sessions) {
  const wheel = el('wheel');
  if (!accounts.length) {
    wheel.innerHTML = '<div class="wheel-empty">暂无账号——先到统一账号库添加</div>';
    return;
  }
  const onlineCount = accounts.filter((a) => {
    const s = sessions.get(a.id);
    return s && s.status === 'online';
  }).length;
  const segments = accounts
    .map((a, i) => `${colorOf(i)} ${(i / accounts.length) * 360}deg ${((i + 1) / accounts.length) * 360}deg`)
    .join(', ');
  const dots = accounts.map((a, i) => {
    const angle = (i / accounts.length) * Math.PI * 2 - Math.PI / 2;
    const radius = 78;
    const x = Math.cos(angle) * radius;
    const y = Math.sin(angle) * radius;
    const s = sessions.get(a.id);
    const isOnline = s && s.status === 'online';
    return `<button class="wheel-dot" data-id="${a.id}" title="启动 ${a.username}"
      style="transform: translate(${x.toFixed(1)}px, ${y.toFixed(1)}px); background: ${colorOf(i)}">${a.username.slice(0, 1).toUpperCase()}
      ${isOnline ? '<span class="dot-badge"></span>' : ''}</button>`;
  }).join('');
  wheel.style.setProperty('--wheel-segments', segments);
  wheel.innerHTML = `${dots}
    <div class="wheel-hub">
      <span class="hub-num">${onlineCount}/${accounts.length}</span>
      <span class="hub-label">在线 / 账号</span>
    </div>`;
  const wrap = wheel.parentElement;
  const oldLegend = wrap.querySelector('.wheel-legend');
  if (oldLegend) oldLegend.remove();
  const legend = accounts.map((a, i) => {
    const s = sessions.get(a.id);
    const status = s ? s.status : 'stopped';
    return `<div class="legend-item" data-id="${a.id}" title="启动 ${a.username}">
      <span class="legend-dot" style="background:${colorOf(i)}"></span>
      <span>${a.username}</span>
      <span class="status-badge ${badgeFor(status) || ''}">${status}</span>
    </div>`;
  }).join('');
  wheel.insertAdjacentHTML('afterend', `<div class="wheel-legend">${legend}</div>`);
  wrap.querySelectorAll('[data-id]').forEach((node) => {
    node.onclick = () => openAccount(node.dataset.id);
  });
}

async function openAccount(accountId) {
  const head = accountId.slice(0, 6);
  logLine(log, `▶ 启动托管会话 ${head}…`, 'sys');
  try {
    const result = await api('/api/browser/open', { method: 'POST', body: { account_id: accountId } });
    logLine(log, `■ ${head}：${result.detail || result.status}`, result.status === 'error' ? 'err' : 'ok');
  } catch (e) {
    logLine(log, `✗ ${head}：${e.message}`, 'err');
  }
  loadAccountsAndSessions().catch(() => {});
}

function renderCards(accounts, sessions, savedMap) {
  const wall = el('session-wall');
  wall.innerHTML = '';
  if (!accounts.length) {
    wall.innerHTML = '<div class="empty" style="grid-column:1/-1">暂无账号——先到统一账号库添加</div>';
    return;
  }
  accounts.forEach((a, i) => {
    const s = sessions.get(a.id);
    const status = s ? s.status : 'stopped';
    const badgeCls = badgeFor(status) || (s ? 'warn' : '');
    const color = colorOf(i);
    const saved = savedMap.get(a.id);
    const chips = [];
    if (saved) chips.push('<span class="chip chip-active">已保存登录态</span>');
    if (s && s.has_token) chips.push('<span class="chip">已捕获 token</span>');
    const card = document.createElement('div');
    card.className = 'acard';
    card.innerHTML = `
      <div class="row1">
        <div class="avatar" style="background:${color}; --ql-ring: ${color}55">${a.username.slice(0, 1).toUpperCase()}</div>
        <div>
          <div class="name">${a.username}</div>
          <div class="env">${a.env_name}${a.env_base_url ? ` · ${a.env_base_url}` : ''}</div>
        </div>
        <div style="flex:1"></div>
        <span class="status-badge ${badgeCls}">${status}</span>
      </div>
      ${chips.length ? `<div class="chip-row">${chips.join('')}</div>` : ''}
      ${s ? `<div class="env">标题：${s.title || '—'}</div>` : ''}
      ${s && s.detail ? `<div class="env">${s.detail}</div>` : ''}
      <div class="actions">
        ${s && s.status !== 'stopped'
          ? `<button class="mbtn ghost" data-act="close" data-id="${a.id}">关闭会话</button>`
          : `<button class="mbtn" data-act="open" data-id="${a.id}">启动并自动登录</button>`}
        ${saved ? `<button class="mbtn ghost" data-act="forget" data-id="${a.id}" data-name="${a.username}">忘记会话</button>` : ''}
      </div>`;
    card.querySelectorAll('button[data-act]').forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          if (btn.dataset.act === 'open') {
            logLine(log, `▶ 启动 ${a.username} 的托管浏览器…`, 'sys');
            const result = await api('/api/browser/open', { method: 'POST', body: { account_id: a.id } });
            logLine(log, `■ ${a.username}：${result.detail || result.status}`,
              result.status === 'error' ? 'err' : 'ok');
          } else if (btn.dataset.act === 'close') {
            await api(`/api/browser/close/${a.id}`, { method: 'POST' });
            logLine(log, `■ 已关闭 ${a.username}（登录态已保存）`, 'sys');
          } else if (btn.dataset.act === 'forget') {
            if (!confirm(`清除 ${a.username} 的持久登录态？下次打开将重新走自动登录。`)) return;
            await api(`/api/browser/forget/${a.id}`, { method: 'POST' });
            logLine(log, `■ 已清除 ${a.username} 的持久登录态`, 'sys');
          }
        } catch (e) {
          logLine(log, `✗ ${a.username}：${e.message}`, 'err');
        } finally {
          btn.disabled = false;
          loadAccountsAndSessions().catch(() => {});
        }
      };
    });
    wall.appendChild(card);
  });
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
