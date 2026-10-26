/**
 * akso-workbench · DSH 插件（浏览器半边）
 *
 * 两个座位（规范 §3 / §4）：
 *   1. `sidebar.panellist` + `main` —— 左栏菜单项与它在中栏打开的账号中心页，
 *      两处 id / key **必须同一个值**（`akso-workbench`）；
 *   2. `conversation.composer.dock` —— 输入卡片下方那一行，与 ContextMeter 并排。
 *
 * ★ 所有注册都包在 `ctx.slots.inject(seat, () => ctx.slots.register(...))` 里：
 *   宿主没声明该座位时静默缺席，**不抛错**（规范 §5 规则 6）。
 * ★ 整个 apply 外面 try/catch：外部插件抛错会把整个 shell 带崩（规范 §5 规则 5）。
 */
import { AccountStrip } from './strip.tsx'
import { AccountsPage } from './page.tsx'
import { CSS } from './styles.ts'
import { dictionaries, NS, setTranslator } from './i18n.ts'

export const inject = ['slots', 'locale', 'layout']

const PANEL_ID = 'akso-workbench'

/** 单色线性图标：尺寸与激活态由侧栏传入，只用 currentColor（规范 §3.3）。 */
function WorkbenchIcon({ size }: { size?: number; active?: boolean }) {
  const edge = size ?? 16
  return (
    <svg
      width={edge}
      height={edge}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="8" cy="5.5" r="2.5" />
      <path d="M3.2 13.2c0-2.6 2.1-4.2 4.8-4.2s4.8 1.6 4.8 4.2" />
    </svg>
  )
}

function injectStyles(): void {
  const marker = 'style[data-plugin="akso-workbench"]'
  if (document.querySelector(marker) !== null) return
  const tag = document.createElement('style')
  tag.dataset.plugin = 'akso-workbench'
  tag.textContent = CSS
  document.head.appendChild(tag)
}

type Translate = ((key: string, vars?: Record<string, string | number>) => string) | undefined

export function apply(ctx: any): void {
  try {
    ctx.effect(() => ctx.locale.register(NS, dictionaries), 'akso-workbench: dictionaries')
    const bound = ctx.locale.bind(NS) as (key: string, vars?: Record<string, string | number>) => string
    setTranslator((key, vars) => bound(key, vars))
    injectStyles()

    ctx.slots.inject('sidebar.panellist', () =>
      ctx.slots.register(
        {
          name: 'sidebar.panellist',
          id: PANEL_ID,
          order: 40,
          locale: NS,
          label: () => bound('menu.label'),
        },
        WorkbenchIcon,
      ),
    )

    ctx.slots.inject('main', () =>
      ctx.slots.register(
        {
          name: 'main',
          key: PANEL_ID,
          locale: NS,
        },
        AccountsPage,
      ),
    )

    /* 配置工具在 akso-workbench 页面内以导航 tab 呈现（见 page.tsx），不再单占左栏 */

    ctx.slots.inject('conversation.composer.dock', () =>
      ctx.slots.register(
        {
          name: 'conversation.composer.dock',
          id: 'akso-workbench-account-strip',
          order: 20,
          locale: NS,
          inject: () => ({
            openPanel: () => ctx.layout.selectPanel(PANEL_ID),
          }),
        },
        AccountStrip as unknown as (props: never) => never,
      ),
    )
  } catch (error) {
    // 外部插件绝不能把 shell 带崩
    console.error('[akso-workbench] register failed', error)
  }
}
