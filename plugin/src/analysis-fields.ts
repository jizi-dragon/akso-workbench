/**
 * 对象字段：只读读取 `BasicObject/field/{objectCode}`，把 92 个键压成报告/界面要用的字段表。
 *
 * 字段的 `dataType` 建完不能改，所以这里只做展示，不做任何写入或猜测；
 * 取不到的引用对象 / 选项集名称保留 id，宁可显示 id 也不编。
 */
import { call, type EgmpSession } from './config/session.ts'

export interface AnalysisField {
  id: string
  name: string
  code: string
  dataType: number
  dataTypeLabel: string
  source: number
  sourceLabel: string
  status: number
  isEnabled: boolean
  isRequired: boolean
  isUnique: boolean
  isDeleted: boolean
  sort: number
  maxLength: number | null
  format: string
  helpContent: string
  picklistId: string | null
  picklistName: string | null
  referenceObjectId: string | null
  referenceObjectCode: string | null
  referenceObjectName: string | null
}

const DATA_TYPE_LABELS: Record<number, string> = {
  1: '文本',
  2: '数字',
  3: '布尔',
  4: '单选',
  5: '日期',
  7: '日期时间',
  8: '附件',
  10: 'ID',
  11: '链接',
  12: '邮箱',
  13: '查找',
  14: '公式',
  15: '对象',
  16: '父对象',
  17: '长文本',
  18: '富文本',
  19: '组件',
  20: '统计',
  21: '加密文本',
  22: '只读文本',
  23: '对象多选',
}

const SOURCE_LABELS: Record<number, string> = {
  1: '系统',
  2: '预置',
  3: '自定义',
}

export function fieldTypeLabel(dataType: number): string {
  return DATA_TYPE_LABELS[dataType] ?? `类型 ${dataType}`
}

export function fieldSourceLabel(source: number): string {
  return SOURCE_LABELS[source] ?? `来源 ${source}`
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function number(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function bool(value: unknown): boolean {
  return value === true
}

/** 平台可能返回 `data` 裸数组，也可能包一层 `datas`；统一成数组。 */
function rowsOf(response: Record<string, unknown>): Record<string, unknown>[] {
  const data = response.data
  if (Array.isArray(data)) return data.map(record)
  const box = record(data)
  for (const key of ['datas', 'data', 'rows', 'items']) {
    if (Array.isArray(box[key])) return (box[key] as unknown[]).map(record)
  }
  return []
}

export async function readFields(session: EgmpSession, objectCode: string): Promise<AnalysisField[]> {
  const response = await call(
    session,
    'GET',
    `/api/openapi/v1.0/BasicObject/field/${encodeURIComponent(objectCode)}`,
  )
  return rowsOf(response)
    .filter((row) => !bool(row.isDeleted))
    .map((row) => {
      const dataType = number(row.dataType)
      const source = number(row.source)
      const status = number(row.status, 1)
      const relation = record(row.objectRelation)
      const picklist = record(row.picklist)
      return {
        id: text(row.id),
        name: text(row.name) || text(row.code),
        code: text(row.code),
        dataType,
        dataTypeLabel: fieldTypeLabel(dataType),
        source,
        sourceLabel: fieldSourceLabel(source),
        status,
        isEnabled: status !== 2,
        isRequired: bool(row.isRequired),
        isUnique: bool(row.isUnique),
        isDeleted: bool(row.isDeleted),
        sort: number(row.sort),
        maxLength: row.maxLength === null || row.maxLength === undefined ? null : number(row.maxLength),
        format: text(row.format),
        helpContent: text(row.helpContent),
        picklistId: text(row.picklistId) || null,
        picklistName: text(picklist.name) || text(picklist.code) || null,
        referenceObjectId: text(row.referenceObjectId) || null,
        referenceObjectCode: text(relation.objectCode) || null,
        referenceObjectName: text(relation.objectName) || null,
      }
    })
    .sort((left, right) => left.sort - right.sort || left.name.localeCompare(right.name, 'zh-CN'))
}
