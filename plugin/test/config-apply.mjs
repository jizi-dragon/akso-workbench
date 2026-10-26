// 开发自测脚本（Node 24 直跑 .ts）：AKSO_ENV_JSON 缺省读 %TEMP%/akso-skill/env.json；DSH_HOME 缺省用临时目录。
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
import { applyPlan, planBlueprint } from '../src/config/engine.ts'
import { login } from '../src/config/session.ts'

const env = JSON.parse(readFileSync((process.env.AKSO_ENV_JSON ?? join(tmpdir(), 'akso-skill', 'env.json')), 'utf8'))
const e = env.environments.find((x) => x.id === env.defaultEnvId) ?? env.environments[0]
const dshHome = (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe'))

const session = await login(e.baseUrl, e.username, e.password)
const blueprint = {
  version: 2,
  env: 'standard-val',
  resources: [
    { kind: 'picklist', name: 'P1探测_变更类型', code: 'p1_probe_type__c', options: [{ name: '主要', code: 'major__c' }, { name: '次要', code: 'minor__c' }] },
    { kind: 'object', name: 'P1探测对象(可删)', code: 'p1_probe_demo__c', enableLifeCycle: false },
    { kind: 'field', object: 'p1_probe_demo__c', name: '编号', code: 'no__c', dataType: 1, isRequired: false },
    { kind: 'field', object: 'p1_probe_demo__c', name: '变更类型', code: 'type__c', dataType: 4, extras: { picklist: 'p1_probe_type__c' } },
    { kind: 'listLayout', object: 'p1_probe_demo__c', columns: [{ fieldCode: 'no__c' }, { fieldCode: 'type__c' }] },
  ],
}

const plan = await planBlueprint(session, blueprint, dshHome)
console.log('plan', plan.planId, JSON.stringify(plan.summary))
for (const s of plan.steps) console.log(`  [${s.action}] ${s.kind} ${s.title}`)

const applied = await applyPlan(session, plan.planId, dshHome)
console.log('\nstatus =', applied.status)
for (const r of applied.results) console.log(`  ${r.status.padEnd(10)} ${r.title} — ${r.message}`)

// 幂等：再跑一次计划，已存在的应当 skip
const plan2 = await planBlueprint(session, blueprint, dshHome)
console.log('\nsecond plan:', JSON.stringify(plan2.summary), plan2.steps.map((s) => `${s.action}:${s.kind}`).join(', '))
const applied2 = await applyPlan(session, plan2.planId, dshHome)
console.log('second status =', applied2.status)
for (const r of applied2.results) console.log(`  ${r.status.padEnd(10)} ${r.title} — ${r.message}`)
