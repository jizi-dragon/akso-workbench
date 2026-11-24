/**
 * 运行报告入口（一级模块 `runs`）。
 *
 * 这里不是报告渲染器，而是「把一份 Word 表单交给 Agent 做成运行报告」的起点：
 * - 输入目标对象和原始表单路径，复制一段约束完整的生成指令；
 * - 资料索引 / 硬规则清单，避免每次从零解释；
 * - 真正的中间态、字段映射和最终模板仍由 Agent 在工作区里产出。
 */
import { useMemo, useState } from 'react'

const GUIDE = 'docs/knowledge/运行报告生成.md'
const SYNTAX = 'docs/knowledge/模板参数配置.md'
const CHANGE_SAMPLE = 'docs/knowledge/运行报告配置.md'
const CAPA_MAPPING = 'akso-workbench-data/reports/CAPA-中间态-字段映射.md'

const STEPS = [
  { title: '收材料', note: '原始表单 + 默认布局导出 + 配置报告；没有导出就先做对象分析。' },
  { title: '做中间态', note: '保留版式，字段用 <<字段名称>>，相关对象表严格两行。' },
  { title: '审核', note: '用户确认章节、字段、签字、留空项；不要在未确认时换 code。' },
  { title: '换最终模板', note: '替换 <<[code]>>，补 if/foreach/签名，真实数据渲染校验。' },
]

const RULES = [
  '字段 code 只从默认布局导出 / 配置报告里抄，不按中文标签猜。',
  '相关对象表只留“表头 + 一行数据”，第二行每格一个字段。',
  '签字用电子签名关系，不能写普通 <<签字/日期>> 占位。',
  '没有可靠字段的格子留空，并写进待确认，不编造占位。',
  '中间态先交用户审核，确认后再替换 code、加 if/foreach。',
]

const MATERIALS = [
  { title: '运行报告生成指南', path: GUIDE, note: '流程、CAPA 实例、校验清单' },
  { title: '模板参数配置', path: SYNTAX, note: 'Aspose 占位、if/foreach、日期、附件' },
  { title: '变更对象样例', path: CHANGE_SAMPLE, note: '从纸质表单到最终模板的映射' },
  { title: 'CAPA 字段映射', path: CAPA_MAPPING, note: '父对象 / 行动措施 / 签名关系' },
]

function buildPrompt(objectCode: string, templatePath: string): string {
  const object = objectCode.trim() || '待填写（对象 code）'
  const template = templatePath.trim() || '待填写（原始表单 .docx 的绝对路径）'
  return [
    '请按 akso-workbench 运行报告流程，为下面的对象制作运行报告模板。',
    `目标对象：${object}`,
    `原始表单 / 模板：${template}`,
    '',
    '执行要求：',
    `1. 先读 ${GUIDE} 和 ${SYNTAX}，不要凭记忆猜语法。`,
    '2. 分析原始表单结构：章节、表格、签字/日期位置、复选框、黄色填写提示，并列出“取 / 不取”的章节。',
    '3. 先做中间态：保留原始版式，删除填写提示/复选框/下划线空白；字段用 <<字段名称>>；相关对象表严格两行（表头 + 一行 foreach，第二行每格只放一个字段）；签字用电子签名表；没有可靠字段的格子留空。',
    '4. 同时输出字段映射说明：每个占位对应的对象字段 code、关系码、类型、置信度；无法映射的列进“待确认”。',
    '5. 把中间态 docx 和映射说明交给我审核，不要直接提交最终模板。',
    '6. 我确认后再替换成 <<[code]>>，补 if/foreach/签名，并用真实数据渲染、跑校验。',
    '',
    '硬规则：字段 code 和 __cr / __sysr 只能从默认布局导出或配置报告核对，不能猜；无法确认就留空，不要用相近字段凑。',
  ].join('\n')
}

function fallbackCopy(text: string): boolean {
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

export function RunReportTab() {
  const [objectCode, setObjectCode] = useState('')
  const [templatePath, setTemplatePath] = useState('')
  const [notice, setNotice] = useState('')
  const [copied, setCopied] = useState('')
  const prompt = useMemo(() => buildPrompt(objectCode, templatePath), [objectCode, templatePath])

  async function copy(text: string, label: string): Promise<void> {
    setNotice('')
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      ok = fallbackCopy(text)
    }
    if (!ok) {
      setNotice('复制失败；可以直接选中下方指令手动复制。')
      return
    }
    setCopied(label)
    setNotice(`已复制${label}。`)
    window.setTimeout(() => setCopied(''), 1800)
  }

  return (
    <div className="akw-runs">
      <section className="akw-runs__hero">
        <div className="akw-runs__hero-main">
          <span className="akw-runs__eyebrow">页面模板 / Word 运行报告</span>
          <h2>运行报告</h2>
          <p>
            运行报告 = 用户表单的版式 + 系统字段变量。先用 <code>{'<<字段名称>>'}</code> 做中间态给用户审核，
            确认后再替换成 <code>{'<<[code]>>'}</code>，补 <code>if</code> / <code>foreach</code> 和电子签名。
          </p>
        </div>
        <div className="akw-runs__chips" aria-label="运行报告要点">
          <span><b>4</b> 份输入</span>
          <span><b>2</b> 行子表</span>
          <span><b>1</b> 张签名表</span>
        </div>
      </section>

      <div className="akw-runs__flow" aria-label="生成流程">
        {STEPS.map((step, index) => (
          <article className="akw-runs__step" key={step.title}>
            <i>{index + 1}</i>
            <div>
              <b>{step.title}</b>
              <span>{step.note}</span>
            </div>
          </article>
        ))}
      </div>

      {notice !== '' ? (
        <div className="akw-notice" role="status" aria-live="polite">
          <span className="akw-notice__grow">{notice}</span>
        </div>
      ) : null}

      <div className="akw-runs__grid">
        <section className="akw-runs__card">
          <header className="akw-runs__card-head">
            <div>
              <h3>开始一份运行报告</h3>
              <p>填写对象和原始表单路径，复制指令到对话里让 Agent 接手。</p>
            </div>
            <button
              type="button"
              className="akw-btn akw-btn--sm akw-btn--ghost"
              onClick={() => {
                setObjectCode('capa_plan__c')
                setTemplatePath('akso-workbench-data/reports/CAPA-运行报告-原始表单.docx')
                setNotice('已填入 CAPA 示例；如果你的工作区没有这份文件，请换成自己的模板路径。')
              }}
            >
              填入 CAPA 示例
            </button>
          </header>

          <label className="akw-runs__field">
            <span>目标对象 code</span>
            <input
              value={objectCode}
              onChange={(event) => setObjectCode(event.target.value)}
              placeholder="例如 capa_plan__c"
              spellCheck={false}
            />
          </label>
          <label className="akw-runs__field">
            <span>原始表单 / 模板路径</span>
            <input
              value={templatePath}
              onChange={(event) => setTemplatePath(event.target.value)}
              placeholder="例如 D:\\...\\CAPA-运行报告-原始表单.docx"
              spellCheck={false}
            />
          </label>

          <div className="akw-runs__actions">
            <button
              type="button"
              className="akw-btn akw-btn--primary"
              onClick={() => void copy(prompt, '生成指令')}
            >
              {copied === '生成指令' ? '已复制' : '复制生成指令'}
            </button>
            <button
              type="button"
              className="akw-btn akw-btn--sm"
              onClick={() => void copy(GUIDE, '指南路径')}
            >
              复制指南路径
            </button>
            <span className="akw-runs__hint">复制后到对话里粘贴；中间态产物落在工作区 reports/。</span>
          </div>

          <details className="akw-runs__details">
            <summary>预览生成指令</summary>
            <pre>{prompt}</pre>
          </details>
        </section>

        <section className="akw-runs__card">
          <header className="akw-runs__card-head">
            <div>
              <h3>生成规则</h3>
              <p>先中间态、后 code；任何一项不确定都先留空。</p>
            </div>
          </header>
          <ul className="akw-runs__rules">
            {RULES.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
          <div className="akw-runs__boundary">
            <b>边界</b>
            <span>
              这个入口只负责发起任务和资料索引，不在页面里伪造成品。对象分析、字段核对、docx 改动仍由 Agent
              在工作区完成并留痕。
            </span>
          </div>
        </section>
      </div>

      <section className="akw-runs__materials">
        <header className="akw-runs__section-head">
          <h3>资料索引</h3>
          <span>复制路径给 Agent，或直接打开工作区文件。</span>
        </header>
        <div className="akw-runs__material-grid">
          {MATERIALS.map((item) => (
            <article className="akw-runs__material" key={item.path}>
              <b>{item.title}</b>
              <code>{item.path}</code>
              <span>{item.note}</span>
              <button
                type="button"
                className="akw-btn akw-btn--sm akw-btn--ghost"
                onClick={() => void copy(item.path, `${item.title}路径`)}
              >
                {copied === `${item.title}路径` ? '已复制' : '复制路径'}
              </button>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
