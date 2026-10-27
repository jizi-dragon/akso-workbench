/**
 * 蓝图：声明式期望状态（v2 草案）。
 *
 * 设计原则：
 *   - 只描述"要什么"，不描述"怎么调接口"；接口由 capability 目录负责；
 *   - 引用用 **code** 而非 id（id 是平台生成的，蓝图要可读、可版本化）；
 *   - 每个资源带 `kind`，引擎按 kind 找 domain 处理。
 */

export type ResourceKind = 'picklist' | 'object' | 'lifecycle' | 'field' | 'formLayout' | 'listLayout' | 'menu'

export interface PicklistOptionSpec {
  name: string
  code?: string
  sort?: number
  status?: number
}

export interface PicklistResource {
  kind: 'picklist'
  name: string
  code: string
  options: PicklistOptionSpec[]
}

export interface ObjectResource {
  kind: 'object'
  name: string
  code: string
  enableLifeCycle?: boolean
  enableSignatures?: boolean
}

export interface LifecycleResource {
  kind: 'lifecycle'
  object: string
  statuses: Array<{ name: string; code: string }>
}

export interface FieldResource {
  kind: 'field'
  object: string
  name: string
  code: string
  dataType: number
  isRequired?: boolean
  /** 各 dataType 的特有参数（maxLength / picklist / referenceObject / formulaScript ...） */
  extras?: Record<string, unknown>
}

export interface FormLayoutResource {
  kind: 'formLayout'
  object: string
  sections: Array<{ name: string; code?: string; columnsNum?: number; fields: string[] }>
}

export interface ListLayoutResource {
  kind: 'listLayout'
  object: string
  columns: Array<{ fieldCode: string; sort?: number }>
}

export interface MenuResource {
  kind: 'menu'
  name: string
  code: string
  menuActionType: number
  parentMenuId?: string
  object?: string
  layoutCode?: string
  listLayoutCode?: string
}

export type Resource =
  | PicklistResource
  | ObjectResource
  | LifecycleResource
  | FieldResource
  | FormLayoutResource
  | ListLayoutResource
  | MenuResource

export interface Blueprint {
  version?: number
  env?: string
  mode?: 'plan' | 'apply'
  resources: Resource[]
}

export interface ValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

const KIND_SET = new Set<ResourceKind>(['picklist', 'object', 'lifecycle', 'field', 'formLayout', 'listLayout', 'menu'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function validateBlueprint(input: unknown): ValidationResult {
  const errors: string[] = []
  const warnings: string[] = []

  if (!isRecord(input)) return { valid: false, errors: ['蓝图必须是 JSON 对象'], warnings }
  const resources = input.resources
  if (!Array.isArray(resources) || resources.length === 0) {
    return { valid: false, errors: ['蓝图至少需要一个 resource'], warnings }
  }

  const objectCodes = new Set<string>()
  const picklistCodes = new Set<string>()

  resources.forEach((resource, index) => {
    const at = `resources[${index}]`
    if (!isRecord(resource)) {
      errors.push(`${at} 必须是对象`)
      return
    }
    const kind = resource.kind
    if (typeof kind !== 'string' || !KIND_SET.has(kind as ResourceKind)) {
      errors.push(`${at}.kind 不合法（应为 ${[...KIND_SET].join(' / ')}）`)
      return
    }

    const requireString = (key: string): void => {
      if (typeof resource[key] !== 'string' || String(resource[key]).trim() === '') errors.push(`${at}.${key} 必填`)
    }

    switch (kind as ResourceKind) {
      case 'picklist': {
        requireString('name')
        requireString('code')
        if (!Array.isArray(resource.options) || resource.options.length === 0) errors.push(`${at}.options 至少一个选项`)
        picklistCodes.add(String(resource.code ?? ''))
        break
      }
      case 'object': {
        requireString('name')
        requireString('code')
        if (resource.enableLifeCycle === true) warnings.push(`${at} 开启生命周期是**不可逆**操作`)
        objectCodes.add(String(resource.code ?? ''))
        break
      }
      case 'lifecycle': {
        requireString('object')
        if (!Array.isArray(resource.statuses) || resource.statuses.length === 0) errors.push(`${at}.statuses 至少一个状态`)
        break
      }
      case 'field': {
        requireString('object')
        requireString('name')
        requireString('code')
        if (typeof resource.dataType !== 'number') errors.push(`${at}.dataType 必须是数字`)
        // dataType=4（选项集）必须指明引用哪个 picklist —— extras 是未知形状，先收窄再读
        const extras = resource.extras
        if (resource.dataType === 4 && !(isRecord(extras) && typeof extras.picklist === 'string')) {
          errors.push(`${at} dataType=4 需要 extras.picklist（选项集 code）`)
        }
        break
      }
      case 'formLayout': {
        requireString('object')
        if (!Array.isArray(resource.sections) || resource.sections.length === 0) errors.push(`${at}.sections 至少一个分区`)
        break
      }
      case 'listLayout': {
        requireString('object')
        if (!Array.isArray(resource.columns) || resource.columns.length === 0) errors.push(`${at}.columns 至少一列`)
        break
      }
      case 'menu': {
        requireString('name')
        requireString('code')
        if (typeof resource.menuActionType !== 'number') errors.push(`${at}.menuActionType 必须是数字（1=子菜单，3=母菜单）`)
        if (resource.menuActionType === 1 && typeof resource.parentMenuId !== 'string') {
          warnings.push(`${at} 子菜单没有 parentMenuId，平台可能拒绝`)
        }
        break
      }
    }
  })

  for (const code of objectCodes) if (code === '') errors.push('对象 code 不能为空')
  for (const code of picklistCodes) if (code === '') errors.push('选项集 code 不能为空')

  return { valid: errors.length === 0, errors, warnings }
}
