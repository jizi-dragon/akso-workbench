// 模拟扩展端 sync.ts 消费逻辑的离线验收：快照过滤/映射/指令解析
// 通过标准：2 个账号全部通过 host 过滤进入映射；par.open 能解析出扩展 accountId
const DESKTOP = 'http://127.0.0.1:18765';

const getJson = async (path) => {
  const resp = await fetch(`${DESKTOP}${path}`, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${path}`);
  return resp.json();
};

// —— 复刻 sync.ts applySnapshot 的过滤与映射（第 92-149 行语义）——
const snap = await getJson('/extension/snapshot');
if (snap.format !== 'akso-workbench-snapshot' || !snap.fernetKey) throw new Error('快照格式不对');
const map = {}; // desktopId → extension accountId（此处用 desktopId 代指 extId）
const dropped = [];
for (const item of snap.accounts) {
  if (!item.host || !item.username || !item.passwordEnc || !item.desktopId) { dropped.push(item.username); continue; }
  map[item.desktopId] = `ext:${item.username}`;
}
console.log(`快照账号总数: ${snap.accounts.length}`);
console.log(`通过过滤进入映射: ${Object.keys(map).length} → ${Object.values(map).join(', ')}`);
console.log(`被丢弃: ${dropped.length ? dropped.join(', ') : '无'}`);
if (Object.keys(map).length !== snap.accounts.length) throw new Error('有账号被过滤丢弃——同步链路不通');

// —— 复刻 pollCommands 的 par.open 解析（第 173-176 行语义）——
await fetch(`${DESKTOP}/extension/commands`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ type: 'par.open', payload: { accountId: Object.keys(map)[0] } }),
});
const data = await getJson('/extension/commands?after=0');
const open = data.commands.find((c) => c.type === 'par.open');
if (!open) throw new Error('指令队列没有 par.open');
const extId = map[String(open.payload?.accountId)];
if (!extId) throw new Error(`par.open accountId=${open.payload?.accountId} 无法映射到扩展账号——键不匹配`);
console.log(`par.open 解析: desktopId=${open.payload.accountId} → ${extId} ✓`);
await fetch(`${DESKTOP}/extension/ack`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ seqs: [open.seq] }),
});
console.log('SIMULATION_OK：数据面 → 指令面全通');
