// 开发自测脚本（Node 24 直跑 .ts）：AKSO_ENV_JSON 缺省读 %TEMP%/akso-skill/env.json；DSH_HOME 缺省用临时目录。
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import fs from 'node:fs'
const { planBlueprint } = await import('../src/config/engine.ts')
const { login } = await import('../src/config/session.ts')
const env = JSON.parse(fs.readFileSync((process.env.AKSO_ENV_JSON ?? join(tmpdir(), 'akso-skill', 'env.json')),'utf8'))
const e = env.environments.find(x=>x.id===env.defaultEnvId) ?? env.environments[0]
const session = await login(e.baseUrl, e.username, e.password)
try {
  await planBlueprint(session, { resources: [{ kind:'formLayout', object:'p1_probe_demo__c', sections:[{name:'基本信息',fields:['no__c']}] }] }, (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe')))
  console.log('unexpected success')
} catch (err) { console.log('formLayout plan rejected ->', err.message.slice(0, 60)) }
const p = await planBlueprint(session, { resources: [{ kind:'menu', name:'P1探测菜单', code:'p1_probe_menu__c', menuActionType:3 }] }, (process.env.DSH_HOME ?? join(tmpdir(), 'dsh-home-probe')))
console.log('menu plan:', JSON.stringify(p.summary), p.steps[0].warnings.join('|'))