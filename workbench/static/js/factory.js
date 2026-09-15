import { api, el, logLine, fmtBytes, badgeFor } from './modules-common.js';

const log = el('fac-log');
let currentBlueprintId = '';

async function checkHealth() {
  const badge = el('module-health');
  try {
    const data = await api('/api/modules/factory');
    badge.className = `status-badge ${badgeFor(data.status)}`;
    const fail = (data.checks || []).filter((c) => !c.ok);
    badge.textContent = fail.length
      ? `配置工厂 ${data.status}：${fail.map((c) => c.name).join('、')} 未就绪`
      : `配置工厂就绪（${data.status}）`;
  } catch (e) {
    badge.className = 'status-badge err';
    badge.textContent = `体检失败：${e.message}`;
  }
}

async function loadAccounts() {
  // 分配池语义：默认只列配置池账号；池为空时回退全量并提示
  let data = await api('/api/accounts?pool=config');
  if (!data.accounts.length) {
    data = await api('/api/accounts');
  }
  const sel = el('fac-account');
  sel.innerHTML = '';
  if (!data.accounts.length) {
    sel.innerHTML = '<option value="">（请先在账号中心添加账号并加入配置池）</option>';
    return;
  }
  const note = data.accounts[0].pool ? '' : '（提示：分配池为空，已回退显示全部账号）';
  if (note) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = note;
    opt.disabled = true;
    sel.appendChild(opt);
  }
  for (const a of data.accounts) {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = `${a.username} · ${a.env_name}`;
    sel.appendChild(opt);
  }
}

async function loadBlueprints() {
  const data = await api('/api/factory/blueprints');
  const box = el('fac-blueprints');
  const sel = el('fac-blueprint');
  sel.innerHTML = '';
  if (!data.blueprints.length) {
    box.innerHTML = '<div class="empty">暂无暂存蓝图</div>';
    return;
  }
  box.innerHTML = `<table class="mtable"><thead><tr><th>蓝图</th><th>时间</th><th>id</th></tr></thead><tbody></tbody></table>`;
  const tbody = box.querySelector('tbody');
  for (const b of data.blueprints) {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.innerHTML = `<td>${b.name}</td><td>${new Date(b.mtime).toLocaleString()}</td><td class="mono">${b.blueprint_id}</td>`;
    tr.onclick = () => { sel.value = b.blueprint_id; };
    tbody.appendChild(tr);
    const opt = document.createElement('option');
    opt.value = b.blueprint_id;
    opt.textContent = `${b.name}（${b.blueprint_id}）`;
    sel.appendChild(opt);
  }
  if (currentBlueprintId) sel.value = currentBlueprintId;
}

async function upload() {
  const input = el('fac-file');
  if (!input.files?.length) { logLine(log, '✗ 请选择 blueprint.json', 'err'); return; }
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const resp = await fetch('/api/factory/blueprint', { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    currentBlueprintId = data.blueprint_id;
    el('fac-upload-result').textContent = `已暂存：${data.blueprint_id}（顶层键：${data.top_keys.join(', ') || '—'}）`;
    await loadBlueprints();
  } catch (e) {
    el('fac-upload-result').textContent = `上传失败：${e.message}`;
  }
}

async function run() {
  const blueprint_id = el('fac-blueprint').value;
  const account_id = el('fac-account').value;
  const command = el('fac-command').value;
  if (!blueprint_id) { logLine(log, '✗ 请先上传/选择蓝图', 'err'); return; }
  if (!account_id) { logLine(log, '✗ 请先在统一账号库添加账号', 'err'); return; }
  if (!el('fac-confirmed').checked) {
    logLine(log, '✗ 违反「环境强制确认」原则：请先勾选确认框', 'err');
    return;
  }
  log.innerHTML = '';
  logLine(log, `$ 配置工厂 ${command} <blueprint.json> · 账号 ${account_id}`, 'sys');

  // ⚠️ 这里的字段名必须与 `POST /api/factory/run` 的**实际响应体**一致：
  //   {job_id, status, result:{success,message,artifact_dir,steps}, log_tail}
  // 2026-09-15 前的版本读的是 `stdout_tail` / `stderr_tail` / `code` / `duration_s`
  // ——那四个字段这个接口**从来不返回**（它们是 `services/proc.py` 的进程结果形状）。
  // 后果：跑完什么都不显示、收尾行写着 `exit=undefined`，而**页面不报错**，
  // 所以一直没被发现。改这里时请对照 API.md §7。
  el('btn-fac-run').disabled = true;
  try {
    const data = await api('/api/factory/run', {
      method: 'POST',
      body: { blueprint_id, account_id, command, confirmed: true },
    });
    for (const line of data.log_tail || []) logLine(log, line);
    renderOutcome(log, data);
    await loadJobs();
    // 详情（含断点续跑的 checkpoint）在任务详情里，不在 run 的响应里
    await showJob(data.job_id);
  } catch (e) {
    // 失败路径：路由抛 HTTPException，前端只能拿到 detail 文本；
    // 任务行仍然入库了（status=failed），所以照样刷新列表让用户能看到它
    logLine(log, `✗ ${e.message}`, 'err');
    await loadJobs();
  } finally {
    el('btn-fac-run').disabled = false;
  }
}

/** 结果摘要：讲"建成了什么 / 卡在哪 / 还要手工做什么"，而不是把 JSON 甩出来。
 *
 *  字段名对照 `orchestrate.run_full_workflow` 的**实际返回**（已实测）：
 *    {success, objects:[{code,name,success,steps:{object,fields,picklists,verify}}],
 *     manualItems:[…], checkpoint:{totalSteps,…}, precheck:{…}}
 *  注意：`message` 只在失败时才有值，成功时为 null——所以成功态不能只靠它。
 */
function renderOutcome(log, data) {
  const result = data.result || {};
  const ok = data.status === 'succeeded' && result.success !== false;
  logLine(log, `■ ${ok ? '成功' : '失败'}：${result.message || (ok ? '已按蓝图完成创建' : data.status)}`,
    ok ? 'ok' : 'err');
  if (result.artifact_dir) logLine(log, `产物：${result.artifact_dir}`, 'sys');
  if (result.complexity) {
    logLine(log, `复杂度：${result.complexity.level ?? JSON.stringify(result.complexity)}`, 'sys');
  }
  for (const issue of result.issues || []) {
    logLine(log, `· [${issue.level}] ${issue.path || ''} ${issue.message}`,
      issue.level === 'error' ? 'err' : 'sys');
  }

  // 每个对象建到哪一步：有人只看"成功"两个字，但真正的信息在 steps 里
  for (const obj of result.objects || []) {
    const steps = Object.entries(obj.steps || {}).map(([name, value]) => {
      if (value == null) return `${name}:?`;
      if (typeof value !== 'object') return `${name}:${value}`;
      const kind = value.status || (value.success === true ? 'ok' : value.success === false ? 'failed' : '?');
      return `${name}:${kind}`;
    }).join('  ');
    logLine(log, `· ${obj.code || obj.name || '?'}${obj.success === false ? '（失败）' : ''} —— ${steps || '（无步骤信息）'}`,
      obj.success === false ? 'err' : 'sys');
  }

  // ⚠️ manualItems 是最容易被忽略、后果最实在的一段：平台建不出来的东西
  //（例如某些布局/权限项）要人手工补，漏读就等于"以为建完了"
  const manual = result.manualItems || [];
  if (manual.length) {
    logLine(log, `⚠ 需要手工处理 ${manual.length} 项：`, 'err');
    for (const item of manual) {
      const text = typeof item === 'string' ? item : (item.message || JSON.stringify(item));
      logLine(log, `   · ${text}`, 'err');
    }
  } else if (ok) {
    logLine(log, '· 无手工遗留项（平台上该建的都已建成）', 'ok');
  }

  const cp = result.checkpoint || {};
  if (cp.totalSteps) {
    // 字段名以 `orchestrate.CheckpointManager.summary()` 为准：totalSteps / completedCount /
    // skippedCount / failedStep。**没有 doneSteps** —— 猜字段名的后果是永远显示 "?"。
    logLine(log, `· 断点：${cp.completedCount ?? 0}/${cp.totalSteps} 步成功` +
      `${cp.skippedCount ? `，跳过 ${cp.skippedCount} 步（幂等）` : ''}` +
      `${cp.failedStep ? `，失败于 ${cp.failedStep}：${cp.failedReason || ''}` : ''}`, 'sys');
  }
  logLine(log, `任务 id：${data.job_id}（下方任务表可查完整日志与断点）`, 'sys');
}

/** 展示某任务的完整日志与断点（任务表与"跑完自动跟随"共用）。 */
async function showJob(jobId) {
  if (!jobId) return;
  const detail = await api(`/api/factory/jobs/${jobId}`);
  log.innerHTML = '';
  logLine(log, `— 任务 ${jobId} · ${detail.command} · ${detail.status} —`, 'sys');
  if (detail.checkpoint && Object.keys(detail.checkpoint).length) {
    logLine(log, `checkpoint：${JSON.stringify(detail.checkpoint)}`, 'sys');
  }
  for (const line of detail.log_tail || []) logLine(log, line);
}

async function loadJobs() {
  const data = await api('/api/factory/jobs?limit=15');
  const tbody = el('fac-jobs').querySelector('tbody');
  tbody.innerHTML = '';
  for (const j of data.jobs) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${new Date(j.created_at).toLocaleString()}</td><td>${j.command}</td>` +
      `<td><span class="status-badge ${badgeFor(j.status)}">${j.status}</span></td><td></td>`;
    const td = tr.children[3];
    const link = document.createElement('a');
    link.textContent = '日志/断点';
    link.style.color = '#4d7cfe';
    link.href = 'javascript:void(0)';
    link.onclick = () => showJob(j.id);
    td.appendChild(link);
    tbody.appendChild(tr);
  }
}

el('btn-fac-upload').onclick = upload;
el('btn-fac-run').onclick = run;
loadAccounts().then(loadBlueprints).then(loadJobs).then(checkHealth)
  .catch((e) => logLine(log, `✗ 初始化失败：${e.message}`, 'err'));
