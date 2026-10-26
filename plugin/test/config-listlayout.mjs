// 开发自测脚本（Node 24 直跑 .ts）：AKSO_ENV_JSON 缺省读 %TEMP%/akso-skill/env.json；DSH_HOME 缺省用临时目录。
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import fs from 'node:fs'
const { planBlueprint, applyPlan } = await import('../src/config/engine.ts')
const { login } = await import('../src/config/session.ts')
const env = JSON.parse(fs.readFileSync((process.env.AKSO_ENV_JSON ?? join(tmpdir(), 'akso-skill', 'env.json')),'utf8'))
const e = env.environments.find(x=>x.id===env.defaultEnvId) ?? env.environments[0]
const dshHome = (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe'))
const session = await login(e.baseUrl, e.username, e.password)
const bp = { version:2, resources: [ { kind:'listLayout', object:'p1_probe_demo__c', columns:[{fieldCode:'no__c'},{fieldCode:'type__c'}] } ] }
const plan = await planBlueprint(session, bp, dshHome)
console.log('plan', plan.planId, JSON.stringify(plan.summary), plan.steps.map(s=>s.action+':'+s.kind).join(','))
const applied = await applyPlan(session, plan.planId, dshHome)
console.log('status=', applied.status)
for (const r of applied.results) console.log(`  ${r.status} ${r.title} — ${r.message}`)