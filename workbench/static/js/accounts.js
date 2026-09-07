import { api, el, logLine, badgeFor } from './modules-common.js';

const COLORS = ['#1E6FFF', '#0FA3B1', '#7C5CFF', '#FF7A1A', '#22C55E'];

async function loadEnvs() {
  const data = await api('/api/accounts/envs');
  const sel = el('acc-env');
  sel.innerHTML = '';
  for (const env of data.envs) {
    const opt = document.createElement('option');
    opt.value = env.id;
    opt.textContent = `${env.name}${env.base_url ? ` · ${env.base_url}` : ''}（${env.account_count} 账号）`;
    sel.appendChild(opt);
  }
  const box = el('env-list');
  if (!data.envs.length) { box.innerHTML = '<div class="empty">暂无平台环境</div>'; return; }
  box.innerHTML = '<table class="mtable"><thead><tr><th>环境</th><th>baseUrl</th><th>账号数</th><th></th></tr></thead><tbody></tbody></table>';
  const tbody = box.querySelector('tbody');
  for (const env of data.envs) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${env.name}</td><td class="mono">${env.base_url || '—'}</td><td>${env.account_count}</td><td></td>`;
    const del = document.createElement('button');
    del.className = 'mbtn danger';
    del.style.padding = '4px 10px';
    del.textContent = '删除';
    del.onclick = async () => {
      if (!env.account_count || confirm(`删除环境「${env.name}」？（其下 ${env.account_count} 个账号将一并删除）`)) {
        await api(`/api/accounts/envs/${env.id}`, { method: 'DELETE' });
        refresh();
      }
    };
    tr.children[3].appendChild(del);
    tbody.appendChild(tr);
  }
}

async function loadAccounts() {
  const data = await api('/api/accounts');
  const wall = el('acc-wall');
  wall.innerHTML = '';
  if (!data.accounts.length) {
    wall.innerHTML = '<div class="empty" style="grid-column:1/-1">暂无账号——先新增环境与账号，或从原项目 env 导入</div>';
    return;
  }
  data.accounts.forEach((a, i) => {
    const color = COLORS[i % COLORS.length];
    const card = document.createElement('div');
    card.className = 'acard';
    card.innerHTML = `
      <div class="row1">
        <div class="avatar" style="background:${color}">${a.username.slice(0, 1).toUpperCase()}</div>
        <div>
          <div class="name">${a.username}</div>
          <div class="env">${a.env_name}${a.env_base_url ? ` · ${a.env_base_url}` : ''}</div>
        </div>
        <div style="flex:1"></div>
        <span class="status-badge ${a.has_password ? 'ok' : 'warn'}">${a.has_password ? '凭据就绪' : '无凭据'}</span>
      </div>
      <div class="env">${a.role || '未设角色'}${a.tags?.length ? ` · ${a.tags.join(' / ')}` : ''}</div>
      <div class="actions">
        <a class="mbtn ghost" href="/static/pages/browser.html?account=${a.id}">🚀 托管浏览器</a>
        <button class="mbtn danger">删除</button>
      </div>`;
    card.querySelector('button.danger').onclick = async () => {
      if (confirm(`删除账号「${a.username}」？`)) {
        await api(`/api/accounts/${a.id}`, { method: 'DELETE' });
        refresh();
      }
    };
    wall.appendChild(card);
  });
}

async function addEnv() {
  const name = el('env-name').value.trim();
  if (!name) return alert('请填写环境名称');
  await api('/api/accounts/envs', { method: 'POST', body: { name, base_url: el('env-url').value.trim() } });
  el('env-name').value = '';
  el('env-url').value = '';
  refresh();
}

async function addAccount() {
  const env_id = el('acc-env').value;
  const username = el('acc-username').value.trim();
  const password = el('acc-password').value;
  if (!env_id) return alert('请先新增平台环境');
  if (!username || !password) return alert('用户名与密码必填');
  const tags = el('acc-tags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  await api('/api/accounts', {
    method: 'POST',
    body: { env_id, username, password, role: el('acc-role').value.trim(), tags },
  });
  el('acc-username').value = '';
  el('acc-password').value = '';
  el('acc-tags').value = '';
  refresh();
}

async function openImport() {
  const dlg = el('import-dialog');
  const preview = el('import-preview');
  preview.innerHTML = '<div class="sys">扫描中…</div>';
  dlg.showModal();
  try {
    const data = await api('/api/accounts/import/preview');
    preview.innerHTML = '';
    for (const line of data.preview) logLine(preview, line.text, line.cls || '');
  } catch (e) {
    preview.innerHTML = '';
    logLine(preview, `✗ 扫描失败：${e.message}`, 'err');
  }
}

async function runImport() {
  const preview = el('import-preview');
  preview.innerHTML = '';
  try {
    const data = await api('/api/accounts/import', { method: 'POST', body: {} });
    for (const line of data.result) logLine(preview, line.text, line.cls || '');
    refresh();
  } catch (e) {
    logLine(preview, `✗ 导入失败：${e.message}`, 'err');
  }
}

function refresh() { return Promise.all([loadEnvs(), loadAccounts()]); }

el('btn-env-add').onclick = addEnv;
el('btn-acc-add').onclick = addAccount;
el('btn-import').onclick = openImport;
el('btn-import-close').onclick = () => el('import-dialog').close();
el('btn-import-run').onclick = runImport;
refresh().catch((e) => alert(`加载失败：${e.message}`));
