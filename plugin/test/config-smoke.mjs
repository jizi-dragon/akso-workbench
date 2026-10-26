// 开发自测脚本（Node 24 直跑 .ts）：AKSO_ENV_JSON 缺省读 %TEMP%/akso-skill/env.json；DSH_HOME 缺省用临时目录。
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
import { planBlueprint, invokeCapability } from '../src/config/engine.ts'
import { login } from '../src/config/session.ts'

const env = JSON.parse(readFileSync((process.env.AKSO_ENV_JSON ?? join(tmpdir(), 'akso-skill', 'env.json')), 'utf8'))
const e = env.environments.find((x) => x.id === env.defaultEnvId) ?? env.environments[0]

const session = await login(e.baseUrl, e.username, e.password)
console.log('login ok:', session.baseUrl, session.account, 'tokenLen=', session.token.length)

// 只读：直接调能力
const got = await invokeCapability(session, 'query.object.get', { code: 'test_approval_matrix_xy_mgt__ak' })
console.log('query.object.get code=', got.code, 'name=', got.data?.name)

// 只读：生成计划（不 apply）
const blueprint = {
  version: 2,
  env: 'standard-val',
  resources: [
    { kind: 'object', name: 'P1 探测对象', code: 'p1_probe_demo__c' },
    { kind: 'field', object: 'p1_probe_demo__c', name: '编号', code: 'no__c', dataType: 1 },
    { kind: 'field', object: 'p1_probe_demo__c', name: '变更类型', code: 'type__c', dataType: 4, extras: { picklist: 'change_type__c' } },
    { kind: 'listLayout', object: 'p1_probe_demo__c', columns: [{ fieldCode: 'no__c' }, { fieldCode: 'type__c' }] },
  ],
}
const plan = await planBlueprint(session, blueprint, (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe')))
console.log('\nplan:', plan.planId, JSON.stringify(plan.summary))
for (const step of plan.steps) {
  console.log(` - [${step.action}] ${step.kind} ${step.title} risk=${step.risk} deps=${step.dependsOn.join(',') || '-'}`)
}
console.log('warnings:', plan.warnings.join(' | ') || '(none)')

// 复用已有对象时，plan 应产生 skip
const plan2 = await planBlueprint(session, {
  resources: [{ kind: 'object', name: '测试审批矩阵_xy管理', code: 'test_approval_matrix_xy_mgt__ak' }],
}, (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe')))
console.log('\nplan2 (existing object):', JSON.stringify(plan2.summary), plan2.steps.map((s) => s.action).join(','))
console.log('OK')
