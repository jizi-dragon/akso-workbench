/**
 * 配置引擎：plan（读现状 → diff → 风险）→ apply（有序、幂等、逐步校验）→ audit。
 *
 * 设计要点：
 *   - **plan 永不写**；apply 只执行 plan 里的步骤，不接受临时参数；
 *   - 引用一律用 code，id 在 apply 时解析（对象可能刚刚才被创建）；
 *   - 每个 domain 自己负责 inspect / applyStep / verify；
 *   - 失败即停，checkpoint 留痕，不做"猜测式重试"。
 */
import { randomUUID } from 'node:crypto'
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { CAPABILITIES, findCapability, type Capability, type Risk } from './capabilities.ts'
import { call, EgmpError, request, type EgmpSession } from './session.ts'
import { dataDir } from '../store.ts'
import type { Blueprint, FieldResource, FormLayoutResource, ListLayoutResource, MenuResource, ObjectResource, PicklistResource, LifecycleResource, ResourceKind } from './blueprint.ts'
import { validateBlueprint } from './blueprint.ts'

export interface Step {
  id: string
  kind: ResourceKind
  action: 'create' | 'update' | 'skip'
  title: string
  naturalKey: string
  risk: Risk
  irreversible: boolean
  capabilityId: string
  params: Record<string, unknown>
  expected?: Record<string, unknown>
  dependsOn: string[]
  warnings: string[]
}

export interface Plan {
  planId: string
  createdAt: number
  expiresAt: number
  env: string
  summary: { create: number; update: number; skip: number; irreversible: number }
  steps: Step[]
  warnings: string[]
  status: 'planned' | 'applied' | 'failed' | 'partial'
  results: StepResult[]
}

export interface StepResult {
  stepId: string
  title: string
  kind: ResourceKind
  action: Step['action']
  status: 'ok' | 'skipped' | 'failed' | 'verified' | 'unverified'
  message: string
  traceId: string | null
  durationMs: number
  before?: unknown
  after?: unknown
}

interface QueryContext {
  session: EgmpSession
  objects: Map<string, Record<string, unknown> | null>
  fields: Map<string, Array<Record<string, unknown>>>
  picklists: Map<string, Record<string, unknown> | null>
  statuses: Map<string, Array<Record<string, unknown>>>
}

function newId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

/* ───────────────────────────── 查询缓存 ───────────────────────────── */

/**
 * 查询阶段宁可"没查到"也不要炸：返回 null，由调用方决定当作"不存在"还是硬失败。
 * 这里**只走能力目录**（capabilities.ts），不在 engine 里另写一份路径 ——
 * 两份路径放一起就会漂移，而漂移的表现是线上 404 / 悄悄读不到数据。
 */
async function tryInvoke(session: EgmpSession, capabilityId: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown> | null> {
  try {
    return await invokeCapability(session, capabilityId, params)
  } catch {
    return null
  }
}

async function getObject(ctx: QueryContext, code: string): Promise<Record<string, unknown> | null> {
  if (ctx.objects.has(code)) return ctx.objects.get(code) ?? null
  const json = await tryInvoke(ctx.session, 'query.object.get', { code })
  const data = (json?.data ?? null) as Record<string, unknown> | null
  ctx.objects.set(code, data)
  return data
}

async function getFields(ctx: QueryContext, objectCode: string): Promise<Array<Record<string, unknown>>> {
  if (ctx.fields.has(objectCode)) return ctx.fields.get(objectCode)!
  const json = await tryInvoke(ctx.session, 'query.fields.list', { code: objectCode })
  const data = json?.data as Record<string, unknown> | undefined
  const list = Array.isArray(data?.fields) ? (data!.fields as Array<Record<string, unknown>>) : Array.isArray(data) ? (data as Array<Record<string, unknown>>) : []
  ctx.fields.set(objectCode, list)
  return list
}

async function getPicklist(ctx: QueryContext, code: string): Promise<Record<string, unknown> | null> {
  if (ctx.picklists.has(code)) return ctx.picklists.get(code) ?? null
  const json = await tryInvoke(ctx.session, 'query.picklist.get', { code })
  const data = (json?.data ?? null) as Record<string, unknown> | null
  ctx.picklists.set(code, data)
  return data
}

/**
 * 对象已绑定的状态。按对象编码查 —— 对象元数据里的 lifeCycleId 是"对象侧生命周期 id"，
 * v3.6.4 的 lifecycle_status 端点只认生命周期**编码**，传 id 会 500「未找到对象生命周期」；
 * BasicObject/lifecycleStatus/{objectCode} 直接返回 [{belongType,id,code,name,lifecycleId,lifecycleCode,lifecycleName}]。
 */
async function getStatuses(ctx: QueryContext, objectCode: string): Promise<Array<Record<string, unknown>>> {
  if (ctx.statuses.has(objectCode)) return ctx.statuses.get(objectCode)!
  const json = await tryInvoke(ctx.session, 'query.lifecycle.status.list', { objectCode })
  const data = json?.data
  const nested = (data as Record<string, unknown> | undefined)?.lifecycle_status ?? (data as Record<string, unknown> | undefined)?.statuses
  const raw = Array.isArray(data) ? data : nested
  const list = Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : []
  ctx.statuses.set(objectCode, list)
  return list
}

/* ───────────────────────────── 通用调用 ───────────────────────────── */

export async function invokeCapability(session: EgmpSession, capabilityId: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
  const cap = findCapability(capabilityId)
  if (cap === undefined) throw new EgmpError(`未知能力：${capabilityId}`)
  let path = cap.path
  const query: Record<string, unknown> = {}
  const body: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (path.includes(`{${key}}`)) {
      path = path.replace(`{${key}}`, encodeURIComponent(String(value)))
      continue
    }
    if (cap.method === 'GET') query[key] = value
    else body[key] = value
  }
  // menu.sort 的 body 是数组，不是对象 —— 走底层 request（call 会断言 code===0 并只接受对象体）
  if (capabilityId === 'menu.sort' && Array.isArray(params.tree)) {
    const { status, json } = await request(session, cap.method, path, params.tree)
    if (json.code !== 0) throw new EgmpError(String(json.message ?? `HTTP ${status}`), { status, path })
    return json
  }
  return await call(session, cap.method, path, cap.method === 'GET' ? undefined : body, cap.method === 'GET' ? query : undefined)
}

/* ───────────────────────────── 计划 ───────────────────────────── */

function picklistIdOf(data: Record<string, unknown> | null): string {
  if (data === null) return ''
  const meta = data.picklists as Record<string, unknown> | undefined
  if (meta !== undefined && meta !== null) return String(meta.picklist_id ?? '')
  return String(data.id ?? '')
}

function picklistOptionCodes(data: Record<string, unknown> | null): string[] {
  if (data === null) return []
  if (Array.isArray(data)) return (data as Array<Record<string, unknown>>).map((o) => String(o.code ?? ''))
  const options = data.picklist_options
  if (Array.isArray(options)) return (options as Array<Record<string, unknown>>).map((o) => String(o.picklist_option_code ?? ''))
  return []
}

function picklistOptions(spec: PicklistResource): Array<Record<string, unknown>> {
  return spec.options.map((option, index) => ({
    ...option,
    code: option.code ?? `opt_${index}__c`,
    status: option.status ?? 1,
    operations: 1,
    sort: option.sort ?? index,
  }))
}

function objectBody(spec: ObjectResource): Record<string, unknown> {
  return {
    name: spec.name,
    code: spec.code,
    source: 3,
    status: 1,
    objectClass: 1,
    enableLifeCycle: spec.enableLifeCycle === true,
    enableSignatures: spec.enableSignatures === true,
    summaryFields: [],
  }
}

function fieldBody(spec: FieldResource): Record<string, unknown> {
  const extras = { ...(spec.extras ?? {}) }
  const picklistCode = typeof extras.picklist === 'string' ? extras.picklist : null
  const referenceObject = typeof extras.referenceObject === 'string' ? extras.referenceObject : null
  delete extras.picklist
  delete extras.referenceObject
  const body: Record<string, unknown> = {
    name: spec.name,
    code: spec.code,
    dataType: spec.dataType,
    isRequired: spec.isRequired === true,
    invisible: false,
    disabled: false,
    statisticsArrange: { id: 1 },
    ...extras,
  }
  if (picklistCode !== null) body.picklistCode = picklistCode
  if (referenceObject !== null) body.referenceObjectCode = referenceObject
  return body
}

export async function planBlueprint(session: EgmpSession, input: Blueprint, dshHome: string): Promise<Plan> {
  const validation = validateBlueprint(input)
  if (!validation.valid) {
    throw new EgmpError(`蓝图校验失败：${validation.errors.join('；')}`)
  }
  const ctx: QueryContext = { session, objects: new Map(), fields: new Map(), picklists: new Map(), statuses: new Map() }
  const warnings: string[] = [...validation.warnings]
  const steps: Step[] = []
  let seq = 0
  const add = (step: Omit<Step, 'id'>): string => {
    const id = `s${++seq}`
    steps.push({ ...step, id })
    return id
  }

  const objectStepIds = new Map<string, string>()
  const fieldStepIds = new Map<string, string>()

  for (const resource of input.resources) {
    switch (resource.kind) {
      case 'picklist': {
        const spec = resource as PicklistResource
        const existing = await getPicklist(ctx, spec.code)
        const desired = picklistOptions(spec).map((o) => String(o.code))
        const existingCodes = picklistOptionCodes(existing)
        const same = existing !== null && existingCodes.length === desired.length && desired.every((c) => existingCodes.includes(c))
        add({
          kind: 'picklist',
          action: existing === null ? 'create' : same ? 'skip' : 'update',
          title: `选项集 ${spec.name}（${spec.code}）`,
          naturalKey: spec.code,
          risk: 'low',
          irreversible: false,
          capabilityId: 'picklist.save',
          params: { name: spec.name, code: spec.code, options: picklistOptions(spec) },
          expected: { optionCount: desired.length },
          dependsOn: [],
          warnings: [],
        })
        break
      }
      case 'object': {
        const spec = resource as ObjectResource
        const existing = await getObject(ctx, spec.code)
        const id = add({
          kind: 'object',
          action: existing === null ? 'create' : 'skip',
          title: `对象 ${spec.name}（${spec.code}）`,
          naturalKey: spec.code,
          risk: spec.enableLifeCycle === true ? 'high' : 'medium',
          irreversible: spec.enableLifeCycle === true,
          capabilityId: 'object.save',
          params: objectBody(spec),
          expected: { code: spec.code },
          dependsOn: [],
          warnings: spec.enableLifeCycle === true ? ['enableLifeCycle 一旦开启不可回退'] : [],
        })
        objectStepIds.set(spec.code, id)
        break
      }
      case 'lifecycle': {
        const spec = resource as LifecycleResource
        const object = await getObject(ctx, spec.object)
        const existing = object === null ? [] : await getStatuses(ctx, spec.object)
        const existingCodes = existing.map((s) => String(s.code ?? s.lifecycle_status_code ?? ''))
        // 状态列表自带 lifecycleId（和对象元数据的 lifeCycleId 是同一个值），比对象元数据更权威
        const lifecycleId = existing.length > 0
          ? String(existing[0]?.lifecycleId ?? '')
          : object === null ? '' : String(object.lifeCycleId ?? object.lifecycle_id ?? '')
        for (const status of spec.statuses) {
          if (existingCodes.includes(status.code)) continue
          add({
            kind: 'lifecycle',
            action: 'create',
            title: `状态 ${status.name}（${status.code}）`,
            naturalKey: `${spec.object}.${status.code}`,
            risk: 'medium',
            irreversible: false,
            capabilityId: 'lifecycle.status.create',
            params: {
              name: status.name,
              code: status.code,
              lifecycleId: lifecycleId === '' ? null : lifecycleId,
              isEnabled: true,
              isRecordDeactivate: false,
              description: '',
              source: 3,
              workflowCancelStatus: '11515107-dce6-4ef1-8d51-b1ffed22040f',
            },
            expected: { code: status.code },
            dependsOn: objectStepIds.has(spec.object) && object === null ? [objectStepIds.get(spec.object)!] : [],
            warnings: lifecycleId === '' ? ['lifecycleId 将在执行时按对象解析'] : [],
          })
        }
        break
      }
      case 'field': {
        const spec = resource as FieldResource
        const object = await getObject(ctx, spec.object)
        const existingFields = object === null ? [] : await getFields(ctx, spec.object)
        const exists = existingFields.some((f) => String(f.field_code ?? f.code ?? '') === spec.code)
        if (exists) break
        const depends: string[] = []
        if (object === null && objectStepIds.has(spec.object)) depends.push(objectStepIds.get(spec.object)!)
        const id = add({
          kind: 'field',
          action: 'create',
          title: `字段 ${spec.name}（${spec.object}.${spec.code}）`,
          naturalKey: `${spec.object}.${spec.code}`,
          risk: 'medium',
          irreversible: false,
          capabilityId: 'field.save',
          params: fieldBody(spec),
          expected: { object: spec.object, code: spec.code, dataType: spec.dataType },
          dependsOn: depends,
          warnings: [],
        })
        fieldStepIds.set(`${spec.object}.${spec.code}`, id)
        break
      }
      case 'formLayout': {
        throw new EgmpError('表单布局写入暂不可用：平台要求 section.belongId 由其 FullPath 生成，具体规则待录制补齐。请先用 listLayout / menu，或等逆向完成。')
        const spec = resource as FormLayoutResource
        const object = await getObject(ctx, spec.object)
        const depends: string[] = []
        if (object === null && objectStepIds.has(spec.object)) depends.push(objectStepIds.get(spec.object)!)
        for (const section of spec.sections) for (const field of section.fields) {
          const key = `${spec.object}.${field}`
          if (fieldStepIds.has(key)) depends.push(fieldStepIds.get(key)!)
        }
        add({
          kind: 'formLayout',
          action: 'update',
          title: `表单布局 ${spec.object}`,
          naturalKey: `${spec.object}.formLayout`,
          risk: 'medium',
          irreversible: false,
          capabilityId: 'layout.save',
          params: { objectRef: spec.object, sections: spec.sections },
          expected: { fields: spec.sections.flatMap((s) => s.fields) },
          dependsOn: depends,
          warnings: ['SaveLayoutDetail 是全量提交：执行时会先读原布局再合并'],
        })
        break
      }
      case 'listLayout': {
        const spec = resource as ListLayoutResource
        const object = await getObject(ctx, spec.object)
        const depends: string[] = []
        if (object === null && objectStepIds.has(spec.object)) depends.push(objectStepIds.get(spec.object)!)
        for (const column of spec.columns) {
          const key = `${spec.object}.${column.fieldCode}`
          if (fieldStepIds.has(key)) depends.push(fieldStepIds.get(key)!)
        }
        add({
          kind: 'listLayout',
          action: 'update',
          title: `列表布局 ${spec.object}`,
          naturalKey: `${spec.object}.listLayout`,
          risk: 'medium',
          irreversible: false,
          capabilityId: 'listlayout.columns.set',
          params: { objectRef: spec.object, columns: spec.columns },
          expected: { columns: spec.columns.map((c) => c.fieldCode) },
          dependsOn: depends,
          warnings: ['AddColumns 是全量替换：执行时会先读原列再合并'],
        })
        break
      }
      case 'menu': {
        const spec = resource as MenuResource
        add({
          kind: 'menu',
          action: 'create',
          title: `菜单 ${spec.name}（${spec.code}）`,
          naturalKey: spec.code,
          risk: 'medium',
          irreversible: false,
          capabilityId: 'menu.submit',
          params: {
            name: spec.name,
            code: spec.code,
            menuActionType: spec.menuActionType,
            ...(spec.parentMenuId !== undefined ? { parentMenuId: spec.parentMenuId } : {}),
            ...(spec.object !== undefined ? { objectCode: spec.object } : {}),
            source: 2,
            menuType: 1,
            status: 1,
          },
          expected: { code: spec.code },
          dependsOn: [],
          warnings: ['平台没有按 code 预检菜单的接口：本步骤不做重复判断'],
        })
        break
      }
    }
  }

  const summary = { create: 0, update: 0, skip: 0, irreversible: 0 }
  for (const step of steps) {
    if (step.action === 'create') summary.create += 1
    else if (step.action === 'update') summary.update += 1
    else summary.skip += 1
    if (step.irreversible) summary.irreversible += 1
  }
  const plan: Plan = {
    planId: newId('pln'),
    createdAt: Date.now(),
    expiresAt: Date.now() + 30 * 60 * 1000,
    env: input.env ?? '',
    summary,
    steps,
    warnings,
    status: 'planned',
    results: [],
  }
  savePlan(dshHome, plan)
  return plan
}

/* ───────────────────────────── 执行 ───────────────────────────── */

interface Resolution {
  objects: Map<string, string>
  fields: Map<string, string>
  picklists: Map<string, string>
  lifecycles: Map<string, string>
}

async function resolveObjectId(ctx: QueryContext, res: Resolution, code: string): Promise<string> {
  const cached = res.objects.get(code)
  if (cached !== undefined) return cached
  const object = await getObject(ctx, code)
  if (object === null) throw new EgmpError(`对象 ${code} 不存在，无法继续`)
  const id = String(object.id ?? '')
  res.objects.set(code, id)
  if (object.lifeCycleId) res.lifecycles.set(code, String(object.lifeCycleId))
  ctx.objects.set(code, object)
  return id
}

async function resolveFieldId(ctx: QueryContext, res: Resolution, objectCode: string, fieldCode: string): Promise<string> {
  const key = `${objectCode}.${fieldCode}`
  const cached = res.fields.get(key)
  if (cached !== undefined) return cached
  const fields = await getFields(ctx, objectCode)
  const found = fields.find((f) => String(f.field_code ?? f.code ?? '') === fieldCode)
  if (found === undefined) throw new EgmpError(`字段 ${key} 不存在`)
  const id = String(found.field_id ?? found.id ?? '')
  res.fields.set(key, id)
  return id
}

async function resolvePicklistId(ctx: QueryContext, res: Resolution, picklistCode: string): Promise<string> {
  const cached = res.picklists.get(picklistCode)
  if (cached !== undefined) return cached
  const data = await getPicklist(ctx, picklistCode)
  const id = picklistIdOf(data)
  if (id === '') throw new EgmpError(`选项集 ${picklistCode} 查不到 id`)
  res.picklists.set(picklistCode, id)
  return id
}

function controlType(dataType: number): string {
  switch (dataType) {
    case 3: return 'AKSelectorYesNo'
    case 4: return 'AKSelectorSingle'
    case 5:
    case 7: return 'AKDateTime'
    case 8: return 'AKFile'
    case 15:
    case 16:
    case 23: return 'AKSelectorObject'
    case 18: return 'AKRichTextEditor'
    default: return 'AKInput'
  }
}

async function applyFormLayout(ctx: QueryContext, step: Step, res: Resolution): Promise<{ message: string; after: unknown }> {
  const objectCode = String((step.params as Record<string, unknown>).objectRef)
  const sections = (step.params as Record<string, unknown>).sections as FormLayoutResource['sections']
  const objectId = await resolveObjectId(ctx, res, objectCode)
  const list = await invokeCapability(ctx.session, 'layout.list', { basicObjectId: objectId, pageIndex: 1, pageSize: 20 })
  const layouts = (list.data as Record<string, unknown> | undefined)?.datas as Array<Record<string, unknown>> | undefined
  const layout = (layouts ?? []).find((l) => Number(l.source) === 2) ?? (layouts ?? [])[0]
  if (layout === undefined) throw new EgmpError(`对象 ${objectCode} 没有可用的表单布局`)
  const layoutId = String(layout.id ?? '')
  const detail = await invokeCapability(ctx.session, 'layout.detail', { layoutId })
  const data = (detail.data ?? {}) as Record<string, unknown>
  const existingSections = Array.isArray(data.sections) ? (data.sections as Array<Record<string, unknown>>) : []
  const existingControls = Array.isArray(data.controls) ? (data.controls as Array<Record<string, unknown>>) : []
  const addedSections: Array<Record<string, unknown>> = []
  const addedControls: Array<Record<string, unknown>> = []
  for (const [index, section] of sections.entries()) {
    const code = section.code ?? `cs${index + 1}__cs`
    let target = existingSections.find((s) => String(s.code ?? '') === code)
    if (target === undefined) {
      target = { id: randomUUID(), layoutId, pageId: null, code, name: section.name, type: 1, columnsNum: section.columnsNum ?? 2, required: null, readOnly: null, relationId: '00000000-0000-0000-0000-000000000000' }
      existingSections.push(target)
      addedSections.push(target)
    }
    for (const fieldCode of section.fields) {
      const control = existingControls.find((c) => String(c.code ?? '') === fieldCode)
      if (control !== undefined) continue
      const fieldId = await resolveFieldId(ctx, res, objectCode, fieldCode)
      const fields = await getFields(ctx, objectCode)
      const meta = fields.find((f) => String(f.field_code ?? f.code ?? '') === fieldCode) ?? {}
      const dataType = Number(meta.field_type ?? meta.dataType ?? 1)
      const control2 = {
        id: randomUUID(),
        layoutId,
        sectionId: String(target.id),
        code: fieldCode,
        name: String(meta.field_name ?? meta.name ?? fieldCode),
        fieldId,
        dataType,
        type: controlType(dataType),
        gridSpan: 1,
        sort: existingControls.length + 1,
        attrs: {},
      }
      existingControls.push(control2)
      addedControls.push(control2)
    }
  }
  const body: Record<string, unknown> = {
    ...data,
    sections: existingSections,
    controls: existingControls,
    pages: Array.isArray(data.pages) ? data.pages : [],
    rules: Array.isArray(data.rules) ? data.rules : [],
  }
  await invokeCapability(ctx.session, 'layout.save', body)
  return { message: `已追加 ${addedSections.length} 个分区、${addedControls.length} 个控件`, after: { layoutId, controls: existingControls.length } }
}

async function applyListLayout(ctx: QueryContext, step: Step, res: Resolution): Promise<{ message: string; after: unknown }> {
  const objectCode = String((step.params as Record<string, unknown>).objectRef)
  const columns = (step.params as Record<string, unknown>).columns as ListLayoutResource['columns']
  const objectId = await resolveObjectId(ctx, res, objectCode)
  const list = await invokeCapability(ctx.session, 'listlayout.list', { objectId })
  const layouts = Array.isArray(list.data) ? (list.data as Array<Record<string, unknown>>) : []
  const layout = layouts.find((l) => Number(l.source) === 2) ?? layouts[0]
  if (layout === undefined) throw new EgmpError(`对象 ${objectCode} 没有可用的列表布局`)
  const listlayoutId = String(layout.id ?? '')
  const current = await invokeCapability(ctx.session, 'listlayout.columns.get', { listlayoutId })
  const existing = Array.isArray(current.data) ? (current.data as Array<Record<string, unknown>>) : []
  const byPath = new Map(existing.map((c) => [String(c.fieldPath ?? c.field_path ?? ''), c]))
  for (const [index, column] of columns.entries()) {
    if (byPath.has(column.fieldCode)) continue
    const fieldId = await resolveFieldId(ctx, res, objectCode, column.fieldCode)
    byPath.set(column.fieldCode, { fieldId, fieldPath: column.fieldCode, sort: column.sort ?? index + 1 })
  }
  const merged = [...byPath.values()].map((c, index) => ({ ...c, sort: Number(c.sort ?? index + 1) }))
  await invokeCapability(ctx.session, 'listlayout.columns.set', { listlayoutId, listlayoutColumns: merged })
  return { message: `列表列 ${merged.length} 个（本次目标 ${columns.length}）`, after: { listlayoutId, columns: merged.length } }
}

async function applyStep(ctx: QueryContext, step: Step, res: Resolution): Promise<{ message: string; after: unknown }> {
  if (step.kind === 'formLayout') return await applyFormLayout(ctx, step, res)
  if (step.kind === 'listLayout') return await applyListLayout(ctx, step, res)

  // 通用：按 capability 调用，执行前解析引用
  const params: Record<string, unknown> = { ...step.params }
  if (step.kind === 'object' || step.kind === 'picklist') {
    const json = await invokeCapability(ctx.session, step.capabilityId, params)
    return { message: 'ok', after: json.data ?? null }
  }
  if (step.kind === 'field') {
    const objectCode = String(step.expected?.object ?? '')
    const objectId = await resolveObjectId(ctx, res, objectCode)
    params.objectId = objectId
    if (typeof params.picklistCode === 'string') {
      params.picklistId = await resolvePicklistId(ctx, res, params.picklistCode)
      delete params.picklistCode
    }
    if (typeof params.referenceObjectCode === 'string') {
      params.referenceObjectId = await resolveObjectId(ctx, res, params.referenceObjectCode)
      params.objectRelation = {}
      delete params.referenceObjectCode
    }
    const json = await invokeCapability(ctx.session, step.capabilityId, params)
    return { message: 'ok', after: json.data ?? null }
  }
  if (step.kind === 'lifecycle') {
    if (params.lifecycleId === null) {
      const objectCode = String(step.naturalKey.split('.')[0] ?? '')
      params.lifecycleId = await resolveObjectId(ctx, res, objectCode).then(async (id) => {
        const object = await getObject(ctx, objectCode)
        return String(object?.lifeCycleId ?? object?.lifecycle_id ?? id)
      })
    }
    const json = await invokeCapability(ctx.session, step.capabilityId, params)
    return { message: 'ok', after: json.data ?? null }
  }
  if (step.kind === 'menu') {
    if (typeof params.objectCode === 'string') {
      params.objectId = await resolveObjectId(ctx, res, params.objectCode)
      delete params.objectCode
    }
    const json = await invokeCapability(ctx.session, step.capabilityId, params)
    return { message: 'ok', after: json.data ?? null }
  }
  throw new EgmpError(`不支持的步骤类型：${step.kind}`)
}

async function verifyStep(ctx: QueryContext, step: Step, res: Resolution): Promise<{ verified: boolean; message: string }> {
  try {
    switch (step.kind) {
      case 'picklist': {
        const data = await getPicklist(ctx, step.naturalKey)
        return { verified: data !== null, message: data === null ? '回读未找到选项集' : '选项集存在' }
      }
      case 'object': {
        const object = await getObject(ctx, step.naturalKey)
        return { verified: object !== null, message: object === null ? '回读未找到对象' : '对象存在' }
      }
      case 'field': {
        const objectCode = String(step.expected?.object ?? '')
        const fields = await getFields(ctx, objectCode)
        const ok = fields.some((f) => String(f.field_code ?? f.code ?? '') === String(step.expected?.code ?? ''))
        return { verified: ok, message: ok ? '字段存在' : '回读未找到字段' }
      }
      case 'lifecycle': {
        const objectCode = String(step.naturalKey.split('.')[0] ?? '')
        const statuses = await getStatuses(ctx, objectCode)
        const ok = statuses.some((s) => String(s.code ?? s.lifecycle_status_code ?? '') === String(step.expected?.code ?? ''))
        return { verified: ok, message: ok ? '状态存在' : '回读未找到状态' }
      }
      case 'formLayout': {
        const objectCode = String((step.params as Record<string, unknown>).objectRef)
        const objectId = await resolveObjectId(ctx, res, objectCode)
        const list = await invokeCapability(ctx.session, 'layout.list', { basicObjectId: objectId, pageIndex: 1, pageSize: 20 })
        const layouts = ((list.data as Record<string, unknown> | undefined)?.datas ?? []) as Array<Record<string, unknown>>
        const layout = layouts.find((l) => Number(l.source) === 2) ?? layouts[0]
        if (layout === undefined) return { verified: false, message: '没有布局可校验' }
        const detail = await invokeCapability(ctx.session, 'layout.detail', { layoutId: layout.id })
        const controls = Array.isArray((detail.data as Record<string, unknown>)?.controls) ? ((detail.data as Record<string, unknown>).controls as Array<Record<string, unknown>>) : []
        const need = (step.expected?.fields as string[] | undefined) ?? []
        const have = new Set(controls.map((c) => String(c.code ?? '')))
        const missing = need.filter((f) => !have.has(f))
        return { verified: missing.length === 0, message: missing.length === 0 ? `控件齐全（${need.length}）` : `缺少控件：${missing.join(', ')}` }
      }
      case 'listLayout': {
        const objectCode = String((step.params as Record<string, unknown>).objectRef)
        const objectId = await resolveObjectId(ctx, res, objectCode)
        const list = await invokeCapability(ctx.session, 'listlayout.list', { objectId })
        const layouts = Array.isArray(list.data) ? (list.data as Array<Record<string, unknown>>) : []
        const layout = layouts.find((l) => Number(l.source) === 2) ?? layouts[0]
        if (layout === undefined) return { verified: false, message: '没有列表布局可校验' }
        const current = await invokeCapability(ctx.session, 'listlayout.columns.get', { listlayoutId: layout.id })
        const have = new Set((Array.isArray(current.data) ? current.data : []).map((c: Record<string, unknown>) => String(c.fieldPath ?? c.field_path ?? '')))
        const need = (step.expected?.columns as string[] | undefined) ?? []
        const missing = need.filter((c) => !have.has(c))
        return { verified: missing.length === 0, message: missing.length === 0 ? `列齐全（${need.length}）` : `缺少列：${missing.join(', ')}` }
      }
      case 'menu':
        return { verified: false, message: '平台无按 code 回查菜单的接口，需人工在控制台确认' }
      default:
        return { verified: false, message: '未知类型' }
    }
  } catch (error) {
    return { verified: false, message: error instanceof Error ? error.message : String(error) }
  }
}

export async function applyPlan(session: EgmpSession, planId: string, dshHome: string): Promise<Plan> {
  const plan = loadPlan(dshHome, planId)
  if (plan === null) throw new EgmpError(`计划不存在：${planId}`)
  if (Date.now() > plan.expiresAt) throw new EgmpError('计划已过期（30 分钟），请重新生成')
  if (plan.status === 'applied') return plan
  if (plan.summary.irreversible > 0 && plan.warnings.length === 0) {
    // 不可逆步骤必须在 warnings 里被显式列出；这里只是兜底，不阻断
  }
  const ctx: QueryContext = { session, objects: new Map(), fields: new Map(), picklists: new Map(), statuses: new Map() }
  const res: Resolution = { objects: new Map(), fields: new Map(), picklists: new Map(), lifecycles: new Map() }
  const started = Date.now()
  for (const step of plan.steps) {
    if (step.action === 'skip') {
      plan.results.push({ stepId: step.id, title: step.title, kind: step.kind, action: step.action, status: 'skipped', message: '已存在且一致', traceId: null, durationMs: 0 })
      continue
    }
    const t0 = Date.now()
    try {
      const { message, after } = await applyStep(ctx, step, res)
      const verify = await verifyStep(ctx, step, res)
      const result: StepResult = {
        stepId: step.id,
        title: step.title,
        kind: step.kind,
        action: step.action,
        status: verify.verified ? 'verified' : 'unverified',
        message: `${message}；${verify.message}`,
        traceId: null,
        durationMs: Date.now() - t0,
        after,
      }
      plan.results.push(result)
      audit(dshHome, plan, step, result)
    } catch (error) {
      // 平台有「写进去了却回未授权」的端点（2026-10-27 实测 `picklist.save`：HTTP 200 + code=401
      // Unauthorized，但数据已落库）。所以失败也要回读一次：确认已是期望态就不算失败，
      // 否则这条路会让 agent 重试同一步、把数据写两遍。
      const verify = await verifyStep(ctx, step, res).catch((verifyError: unknown) => ({
        verified: false,
        message: verifyError instanceof Error ? verifyError.message : String(verifyError),
      }))
      const failure = error instanceof Error ? error.message : String(error)
      const result: StepResult = {
        stepId: step.id,
        title: step.title,
        kind: step.kind,
        action: step.action,
        status: verify.verified ? 'verified' : 'failed',
        message: verify.verified
          ? `写入报「${failure}」，但回读确认已生效：${verify.message}`
          : `${failure}；回读未确认：${verify.message}`,
        traceId: error instanceof EgmpError ? error.traceId : null,
        durationMs: Date.now() - t0,
      }
      plan.results.push(result)
      audit(dshHome, plan, step, result)
      if (!verify.verified) {
        plan.status = 'failed'
        savePlan(dshHome, plan)
        return plan
      }
    }
  }
  plan.status = plan.results.some((r) => r.status === 'failed') ? 'failed' : 'applied'
  plan.results.push({ stepId: '-', title: '汇总', kind: 'object', action: 'update', status: 'ok', message: `用时 ${Date.now() - started}ms`, traceId: null, durationMs: Date.now() - started })
  savePlan(dshHome, plan)
  return plan
}

/* ───────────────────────────── 持久化 ───────────────────────────── */

function plansDir(dshHome: string): string {
  return join(dataDir(dshHome), 'config-plans')
}

function auditDir(dshHome: string): string {
  return join(dataDir(dshHome), 'config-audit')
}

export function savePlan(dshHome: string, plan: Plan): void {
  const dir = plansDir(dshHome)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, `${plan.planId}.json`), `${JSON.stringify(plan, null, 2)}\n`, 'utf8')
}

export function loadPlan(dshHome: string, planId: string): Plan | null {
  const file = join(plansDir(dshHome), `${planId}.json`)
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as Plan
  } catch {
    return null
  }
}

function audit(dshHome: string, plan: Plan, step: Step, result: StepResult): void {
  const dir = auditDir(dshHome)
  mkdirSync(dir, { recursive: true })
  const month = new Date().toISOString().slice(0, 7)
  const record = {
    at: new Date().toISOString(),
    planId: plan.planId,
    env: plan.env,
    stepId: step.id,
    kind: step.kind,
    action: step.action,
    title: step.title,
    capabilityId: step.capabilityId,
    risk: step.risk,
    irreversible: step.irreversible,
    status: result.status,
    message: result.message,
    traceId: result.traceId,
    durationMs: result.durationMs,
  }
  appendFileSync(join(dir, `${month}.jsonl`), `${JSON.stringify(record)}\n`, 'utf8')
}

export function readAudit(dshHome: string, limit = 50): Array<Record<string, unknown>> {
  const dir = auditDir(dshHome)
  if (!existsSync(dir)) return []
  const lines: string[] = []
  try {
    for (const name of readdirSync(dir).filter((n) => n.endsWith('.jsonl')).sort().reverse()) {
      const content = readFileSync(join(dir, name), 'utf8').trim()
      if (content !== '') lines.push(...content.split('\n'))
      if (lines.length >= limit) break
    }
  } catch {
    return []
  }
  return lines.slice(-limit).reverse().map((line) => {
    try { return JSON.parse(line) as Record<string, unknown> } catch { return { raw: line } }
  })
}

export function capabilityList(): Capability[] {
  return CAPABILITIES
}
