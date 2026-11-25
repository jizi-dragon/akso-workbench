import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { saveApproval, type Plan } from '../src/config/engine.ts'
import {
  applyTask,
  createTask,
  loadTask,
  mergeTaskPreview,
  parseTaskInput,
  saveTask,
  taskHash,
  type Task,
} from '../src/config/task.ts'
import type { Blueprint } from '../src/config/blueprint.ts'
import { parseBindingRef, resolveBindings, validatePlanBindings, validateTaskBindings } from '../src/config/bindings.ts'
import type { EgmpSession } from '../src/config/session.ts'

function withHome(run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

function blueprint(code: string, kind = 'picklist'): Blueprint {
  return { version: '1', env: 'test', resources: [{ kind, code, name: 'X', options: [{ code: 'o1', name: '选项一' }] }] } as unknown as Blueprint
}

function session(accountId = 'a_1'): EgmpSession {
  return { baseUrl: 'https://example.test', account: 'lyy', accountId, token: 't', savedAt: Date.now() }
}

function planFixture(planId: string, steps: Plan['steps']): Plan {
  return {
    planId,
    createdAt: Date.now(),
    expiresAt: Date.now() + 30 * 60 * 1000,
    env: 'test',
    target: { baseUrl: 'https://example.test', accountId: 'a_1', account: 'lyy' },
    summary: { create: 0, update: 0, skip: 0, irreversible: 0 },
    steps,
    warnings: [],
    status: 'planned',
    results: [],
  }
}

function codeOf(error: unknown): string | null {
  return (error as { code?: string } | null)?.code ?? null
}

test('parseTaskInput：缺 stages / 超 12 段 / 蓝图非法都给出人话错误', () => {
  assert.equal(parseTaskInput('').valid, false)
  assert.equal(parseTaskInput('{}').valid, false)
  const tooMany = { stages: Array.from({ length: 13 }, () => ({ blueprint: blueprint('x__c') })) }
  const many = parseTaskInput(JSON.stringify(tooMany))
  assert.equal(many.valid, false)
  if (!many.valid) assert.match(many.error, /最多 12/)
  const bad = parseTaskInput(JSON.stringify({ stages: [{ blueprint: { resources: [{ kind: 'object' }] } }] }))
  assert.equal(bad.valid, false)
})

test('parseTaskInput：接受合法任务并给默认名', () => {
  const parsed = parseTaskInput(JSON.stringify({ stages: [{ blueprint: blueprint('a__c') }, { name: '建字段', blueprint: blueprint('b__c') }] }))
  assert.equal(parsed.valid, true)
  if (!parsed.valid) return
  assert.equal(parsed.name, '配置任务')
  assert.equal(parsed.stages[0]?.name, '第 1 阶段')
  assert.equal(parsed.stages[1]?.name, '建字段')
})

test('createTask / saveTask / loadTask：tsk_ 前缀、60 分钟有效期、拒绝非法 id', () => {
  withHome((home) => {
    const task = createTask(session(), {
      name: '建对象+字段',
      stages: [
        { name: '建对象', blueprint: blueprint('a__c', 'object') },
        { name: '建字段', blueprint: blueprint('b__c', 'field') },
      ],
    })
    assert.match(task.taskId, /^tsk_/)
    assert.equal(task.stages.length, 2)
    const ttl = task.expiresAt - task.createdAt
    assert.ok(ttl >= 59 * 60 * 1000 && ttl <= 61 * 60 * 1000, `任务有效期应约 60 分钟，实际 ${ttl}`)
    saveTask(home, task)
    const loaded = loadTask(home, task.taskId)
    assert.equal(loaded?.name, '建对象+字段')
    assert.equal(loadTask(home, '../etc/passwd'), null)
    assert.equal(loadTask(home, 'pln_nope'), null)
  })
})

test('mergeTaskPreview：步骤带阶段前缀、汇总各段变更', () => {
  const task = createTask(session(), {
    name: '任务',
    stages: [
      { name: '建对象', blueprint: blueprint('a__c') },
      { name: '建字段', blueprint: blueprint('b__c') },
    ],
  })
  const step = (id: string, action: 'create' | 'update' | 'skip'): Plan['steps'][number] => ({
    id,
    kind: 'picklist',
    action,
    title: `步骤 ${id}`,
    naturalKey: id,
    risk: 'medium',
    irreversible: action === 'create',
    capabilityId: 'picklist.save',
    params: {},
    dependsOn: [],
    warnings: [],
  })
  const plans = [
    planFixture('pln_a', [step('s1', 'create'), step('s2', 'skip')]),
    planFixture('pln_b', [step('s1', 'update')]),
  ]
  const merged = mergeTaskPreview(task, plans)
  assert.equal(merged.planId, task.taskId)
  assert.deepEqual(merged.steps.map((s) => s.id), ['1.s1', '1.s2', '2.s1'])
  assert.equal(merged.steps[0]?.title, '建对象 · 步骤 s1')
  assert.deepEqual(merged.summary, { create: 1, update: 1, skip: 1, irreversible: 1 })
})

test('taskHash：等价对象稳定，改阶段顺序或名字就变', () => {
  const a = createTask(session(), { name: 'x', stages: [{ name: 's1', blueprint: blueprint('a__c') }, { name: 's2', blueprint: blueprint('b__c') }] })
  const b = { ...a, createdAt: a.createdAt + 1, taskId: 'tsk_other', stages: a.stages.map((stage) => ({ ...stage })) }
  assert.equal(taskHash(a), taskHash(b))
  assert.notEqual(taskHash(a), taskHash({ ...a, stages: [...a.stages].reverse() }))
  assert.notEqual(taskHash(a), taskHash({ ...a, stages: [{ ...a.stages[0]!, name: '改过名' }, a.stages[1]!] }))
})

test('输出绑定：解析步骤/阶段引用与路径', () => {
  assert.deepEqual(parseBindingRef('steps.s1.after'), { kind: 'step', stage: null, stepId: 's1', path: '' })
  assert.deepEqual(parseBindingRef('stages.2.steps.s3.after.data[0].id'), { kind: 'stage', stage: 2, stepId: 's3', path: '.data[0].id' })
  assert.throws(() => parseBindingRef('steps.s1.data'), /格式不合法/)

  const context = { steps: { s1: { data: { id: 'wf_1', rows: [{ id: 'r1' }] } } } }
  assert.deepEqual(
    resolveBindings({ a: { $ref: 'steps.s1.after.data.id' }, b: [{ $ref: 'steps.s1.after.data.rows[0].id' }] }, context),
    { a: 'wf_1', b: ['r1'] },
  )
  assert.throws(() => resolveBindings({ a: { $ref: 'steps.s2.after' } }, context), /尚未产生输出/)
})

test('输出绑定：计划内只能引用更早步骤，任务内只能引用更早阶段', () => {
  const plans = [
    [{ id: 's1', params: {} }, { id: 's2', params: { id: { $ref: 'steps.s1.after' } } }],
    [{ id: 's1', params: {} }, { id: 's2', params: { id: { $ref: 'stages.1.steps.s1.after' } } }],
  ]
  assert.deepEqual(validatePlanBindings(plans[0]!), [])
  assert.deepEqual(validatePlanBindings(plans[1]!), [])
  assert.match(validatePlanBindings([{ id: 's1', params: { id: { $ref: 'steps.s2.after' } } }, { id: 's2', params: {} }])[0] ?? '', /只能引用更早步骤/)
  assert.deepEqual(validateTaskBindings([[{ id: 's1' }], [{ id: 's1', params: { id: { $ref: 'stages.1.steps.s1.after' } } }]]), [])
  assert.match(validateTaskBindings([[{ id: 's1' }], [{ id: 's1', params: { id: { $ref: 'stages.2.steps.s1.after' } } }]])[0] ?? '', /只能引用更早阶段/)
  assert.match(validateTaskBindings([[{ id: 's1' }], [{ id: 's1', params: { id: { $ref: 'stages.1.steps.s9.after' } } }]])[0] ?? '', /不存在的步骤/)
})

test('taskHash：绑定模板参与指纹，改引用就变', () => {
  const withRef = (ref: string): Blueprint => ({
    version: 1,
    resources: [{ kind: 'call', capability: 'workflow.create', params: { parentId: { $ref: ref } } }],
  } as unknown as Blueprint)
  const a = createTask(session(), {
    name: 'x',
    stages: [{ name: 's1', blueprint: withRef('steps.s1.after') }, { name: 's2', blueprint: withRef('stages.1.steps.s1.after') }],
  })
  const b = createTask(session(), {
    name: 'x',
    stages: [{ name: 's1', blueprint: withRef('steps.s1.after') }, { name: 's2', blueprint: withRef('stages.1.steps.s2.after') }],
  })
  assert.notEqual(taskHash(a), taskHash(b))
})

test('applyTask：跨阶段 $ref 在执行时填入前序返回', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  const originalFetch = globalThis.fetch
  const urls: string[] = []
  globalThis.fetch = (async (input: string | URL | Request) => {
    urls.push(String(input))
    return new Response(JSON.stringify({ code: 0, data: { id: 'wf_from_stage_1', name: 'x' } }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }) as typeof fetch
  try {
    const task = createTask(session(), {
      name: '绑定执行',
      stages: [
        {
          name: '一',
          blueprint: {
            version: '1',
            env: 'test',
            resources: [{ kind: 'call', capability: 'workflow.steps.list', params: { workflowConfigId: 'wf_a' } }],
          } as unknown as Blueprint,
        },
        {
          name: '二',
          blueprint: {
            version: '1',
            env: 'test',
            resources: [{ kind: 'call', capability: 'workflow.step.get', params: { id: { $ref: 'stages.1.steps.s1.after.id' } } }],
          } as unknown as Blueprint,
        },
      ],
    })
    saveTask(home, task)
    const applied = await applyTask(session(), task.taskId, home, { requireApproval: false })
    assert.equal(applied.status, 'applied', JSON.stringify(applied.results, null, 2))
    assert.match(urls[1] ?? '', /wf_from_stage_1/)
  } finally {
    globalThis.fetch = originalFetch
    rmSync(home, { recursive: true, force: true })
  }
})

test('applyTask：任务不存在直接拒绝', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  try {
    await assert.rejects(() => applyTask(session(), 'tsk_missing', home, { requireApproval: false }), (error: unknown) => codeOf(error) === 'TASK_MISSING')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('applyTask：过期任务直接拒绝，不碰平台', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  try {
    const task = createTask(session(), { name: 'x', stages: [{ name: 's', blueprint: blueprint('a__c') }] })
    task.expiresAt = Date.now() - 1
    saveTask(home, task)
    await assert.rejects(() => applyTask(session(), task.taskId, home), (error: unknown) => codeOf(error) === 'TASK_EXPIRED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('applyTask：没有审批记录不能执行', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  try {
    const task = createTask(session(), { name: 'x', stages: [{ name: 's', blueprint: blueprint('a__c') }] })
    saveTask(home, task)
    await assert.rejects(() => applyTask(session(), task.taskId, home), (error: unknown) => codeOf(error) === 'TASK_APPROVAL_REQUIRED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('applyTask：审批指纹不一致 / 已用过 / 账号不符都拒绝', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-task-'))
  try {
    const task = createTask(session(), { name: 'x', stages: [{ name: 's', blueprint: blueprint('a__c') }] })
    saveTask(home, task)

    saveApproval(home, { planId: task.taskId, hash: 'not-the-hash', at: Date.now() })
    await assert.rejects(() => applyTask(session(), task.taskId, home), (error: unknown) => codeOf(error) === 'TASK_APPROVAL_MISMATCH')

    const hash = taskHash(task)
    saveApproval(home, { planId: task.taskId, hash, at: Date.now() })
    // 已用过的审批
    saveApproval(home, { planId: task.taskId, hash, at: Date.now(), usedAt: Date.now() })
    await assert.rejects(() => applyTask(session(), task.taskId, home), (error: unknown) => codeOf(error) === 'TASK_APPROVAL_USED')

    // 账号不符：任务绑定 a_1，当前账号 a_2
    const other = createTask(session('a_1'), { name: 'x', stages: [{ name: 's', blueprint: blueprint('a__c') }] })
    saveTask(home, other)
    saveApproval(home, { planId: other.taskId, hash: taskHash(other), at: Date.now() })
    await assert.rejects(() => applyTask(session('a_2'), other.taskId, home), (error: unknown) => codeOf(error) === 'TASK_TARGET_CHANGED')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('Task 结构可落盘并回读（字段齐全）', () => {
  withHome((home) => {
    const task: Task = createTask(session(), { name: '回读', stages: [{ name: 's', blueprint: blueprint('a__c') }] })
    saveTask(home, task)
    const loaded = loadTask(home, task.taskId)
    assert.deepEqual(loaded, task)
  })
})
