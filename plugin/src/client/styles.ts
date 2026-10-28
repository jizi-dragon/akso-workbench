/**
 * 样式表（一次性注入 `<style data-plugin="akso-workbench">`）。
 *
 * 美学取自 akso-pass 的自有页面：顶部品牌条 + 统计芯片 + 下划线式 tab、卡片网格、
 * 软阴影、8/12/16 圆角、11/12/13/15 字号刻度。
 * 颜色一律走 DSH 主题 token（规范 §5 规则 10）—— 深浅色都跟着宿主，不写死色值。
 */
export const CSS = `
/* ───────────────── 账号中心页（main 座位） ───────────────── */
.akw-page{box-sizing:border-box;display:flex;flex-direction:column;height:100%;min-height:0;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-size:13px}

/* 顶部品牌条：标题 + 统计 + 搜索 + 主操作（导航栏在顶部，用户明确要求） */
.akw-topbar{position:sticky;top:0;z-index:3;flex:none;display:flex;align-items:center;gap:14px;padding:14px 24px 12px;background:var(--dsw-alias-bg-base);border-bottom:1px solid var(--dsw-alias-border-l2)}
.akw-topbar__left{display:flex;align-items:center;gap:12px;min-width:0}
.akw-topbar__right{display:flex;align-items:center;gap:8px;margin-left:auto}
.akw-logo{flex:none;display:block;width:28px;height:28px;object-fit:contain}
.akw-brandtext{min-width:0}
.akw-brandtext h1{margin:0;font-size:15px;line-height:20px;font-weight:600}
.akw-brandtext p{margin:2px 0 0;font-size:12px;line-height:16px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.akw-stats{display:flex;align-items:center;gap:8px;margin-left:6px}
.akw-stat{display:flex;align-items:baseline;gap:5px;padding:5px 10px;border:1px solid var(--dsw-alias-border-l3);border-radius:12px;background:var(--dsw-alias-bg-layer-1)}
.akw-stat b{font-size:15px;line-height:18px;font-weight:600;font-variant-numeric:tabular-nums}
.akw-stat span{font-size:11px;line-height:14px;color:var(--dsw-alias-label-tertiary)}
.akw-stat--on b{color:var(--dsw-alias-state-business-primary)}

.akw-search{display:flex;align-items:center;gap:6px;height:30px;padding:0 10px;border:1px solid var(--dsw-alias-border-l3);border-radius:12px;background:var(--dsw-specific-input-major);color:var(--dsw-alias-label-tertiary)}
.akw-search:focus-within{border-color:var(--dsw-alias-state-business-primary)}
.akw-search input{width:170px;border:0;outline:0;background:transparent;color:var(--dsw-alias-label-primary);font:inherit;font-size:13px}
.akw-search input::placeholder{color:var(--dsw-alias-label-caption)}

/* 一级导航（账号中心 / 配置工具）：分段控件，和二级的下划线 tab 区分开 */
.akw-modnav{flex:none;align-self:flex-start;display:flex;flex-wrap:wrap;gap:2px;max-width:calc(100% - 48px);margin:12px 24px 8px;padding:3px;border:1px solid var(--dsw-alias-border-l2);border-radius:12px;background:var(--dsw-alias-bg-layer-1)}
.akw-modnav__btn{flex:none;display:inline-flex;align-items:center;gap:7px;height:30px;padding:0 13px;border:0;border-radius:9px;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:13px;cursor:pointer;white-space:nowrap;transition:background .14s ease,color .14s ease,box-shadow .14s ease}
.akw-modnav__btn:hover{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-interactive-bg-hover)}
.akw-modnav__btn:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:1px}
.akw-modnav__btn.is-active{background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-weight:600;box-shadow:0 0 0 1px var(--dsw-alias-border-l2),0 1px 2px rgb(0 0 0 / 8%)}
.akw-modnav__btn svg{flex:none;width:15px;height:15px;opacity:.85}
.akw-modnav__btn.is-active svg{color:var(--dsw-alias-state-business-primary);opacity:1}

/* 下划线式 tab（akso-pass 的 .tabs / .tab.is-active）——二级导航统一用它 */
.akw-tabs{flex:none;display:flex;align-items:center;gap:2px;padding:0 24px;border-bottom:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-base)}
.akw-tab{position:relative;height:40px;padding:0 11px;border:0;border-radius:9px 9px 0 0;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:13px;line-height:40px;cursor:pointer;white-space:nowrap;transition:background .14s ease,color .14s ease}
.akw-tab:hover{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-interactive-bg-hover)}
.akw-tab:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:-2px}
.akw-tab.is-active{color:var(--dsw-alias-label-primary);font-weight:600}
.akw-tab.is-active::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;border-radius:2px;background:var(--dsw-alias-state-business-primary)}
.akw-tab i{margin-left:7px;padding:0 6px;border-radius:999px;background:var(--dsw-alias-interactive-bg-hover-solid);font-style:normal;font-size:11px;font-weight:500;line-height:16px;color:var(--dsw-alias-label-tertiary);font-variant-numeric:tabular-nums}
.akw-tab.is-active i{background:var(--dsw-alias-state-business-primary);color:var(--dsw-alias-bg-base)}

.akw-body{flex:1;min-height:0;overflow:auto;padding:18px 24px 40px;display:flex;flex-direction:column;gap:14px}

/* 一级模块占位（运行报告 / 对象分析 内容未接入）——不画假数据，也不铺满整页 */
.akw-pending{align-self:center;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;width:100%;max-width:720px;padding:40px 24px;border:1px dashed var(--dsw-alias-border-l3);border-radius:16px;text-align:center}
.akw-pending b{font-size:14px;font-weight:600;color:var(--dsw-alias-label-secondary)}
.akw-pending span{max-width:52ch;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}

/* 按钮：默认描边，主操作用品牌色填充 */
.akw-btn{box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center;gap:6px;height:30px;padding:0 12px;font:inherit;font-size:13px;color:var(--dsw-alias-label-primary);background:transparent;border:1px solid var(--dsw-alias-border-l4);border-radius:12px;cursor:pointer;white-space:nowrap;transition:background .14s ease,border-color .14s ease,transform .14s ease}
.akw-btn:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover);border-color:var(--dsw-alias-label-tertiary)}
.akw-btn:active:not(:disabled){transform:translateY(1px)}
.akw-btn:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:1px}
.akw-btn:disabled{opacity:.5;cursor:default}
.akw-btn--primary{color:var(--dsw-alias-bg-base);background:var(--dsw-alias-state-business-primary);border-color:transparent;font-weight:600}
.akw-btn--primary:hover:not(:disabled){background:var(--dsw-alias-state-business-primary);filter:brightness(1.08)}
.akw-btn--sm{height:26px;padding:0 10px;font-size:12px;border-radius:10px}
.akw-btn--ghost{border-color:transparent}
.akw-btn--danger{color:var(--dsw-alias-state-error-primary)}

/* 提示条（akso-pass 的 .notice） */
.akw-notice{display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid var(--dsw-alias-border-l3);border-radius:12px;font-size:13px;color:var(--dsw-alias-label-secondary)}
.akw-notice--err{color:var(--dsw-alias-state-error-primary);border-color:var(--dsw-alias-state-error-primary)}
.akw-notice__grow{flex:1;min-width:0}

/* 卡片网格 */
.akw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(276px,1fr));gap:10px;margin:0;padding:0;list-style:none}
.akw-card{box-sizing:border-box;display:flex;flex-direction:column;gap:9px;padding:11px 12px;border:1px solid var(--dsw-alias-border-l3);border-radius:14px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-panel);transition:border-color .14s ease,box-shadow .14s ease,transform .14s ease}
.akw-card:hover{border-color:var(--dsw-alias-border-l3);transform:translateY(-1px)}
.akw-card.is-connected{border-color:var(--dsw-alias-state-business-primary);box-shadow:0 0 0 1px var(--dsw-alias-state-business-primary),var(--dsw-elevation-panel)}
.akw-card__head{display:flex;align-items:center;gap:9px;min-width:0}
.akw-avatar{flex:none;display:grid;place-items:center;width:27px;height:27px;border-radius:8px;font-size:12px;font-weight:600;color:var(--dsw-alias-label-primary);background:var(--dsw-alias-interactive-bg-hover-solid)}
.akw-card__id{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
.akw-card__id b{font-size:14px;line-height:18px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__id span{font-size:11px;line-height:15px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-badge{flex:none;padding:1px 7px;border:1px solid var(--dsw-alias-state-business-primary);border-radius:999px;font-size:10px;line-height:15px;color:var(--dsw-alias-state-business-primary)}

.akw-facts{display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;margin:0;padding:7px 9px;border-radius:10px;background:var(--dsw-alias-bg-base)}
.akw-facts dt{font-size:10px;line-height:13px;color:var(--dsw-alias-label-caption)}
.akw-facts dd{margin:1px 0 0;font-size:12px;line-height:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__actions{display:flex;align-items:center;gap:6px;justify-content:flex-end}
.akw-card__note{margin:0;font-size:11px;line-height:15px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__foot{display:flex;align-items:center;justify-content:space-between;gap:8px}
.akw-card__hint{font-size:10px;color:var(--dsw-alias-state-business-primary)}
/* 卡片里的按钮比页面别处小一号：卡片密度上来了，「连接agent」不该还占 26px */
.akw-card .akw-btn--sm{height:24px;padding:0 9px;font-size:11px;border-radius:8px}

/* 盒子筛选：一排 chip；选中态用品牌色实心，和「已连接」同一套语义色 */
.akw-filter{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.akw-chip{box-sizing:border-box;display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 9px;border:1px solid var(--dsw-alias-border-l3);border-radius:999px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;cursor:pointer;transition:border-color .14s ease,color .14s ease,background .14s ease}
.akw-chip:hover{border-color:var(--dsw-alias-label-tertiary);color:var(--dsw-alias-label-primary)}
.akw-chip:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:1px}
.akw-chip i{padding:0 5px;border-radius:999px;background:var(--dsw-alias-interactive-bg-hover-solid);font-style:normal;font-size:10px;line-height:14px;color:var(--dsw-alias-label-tertiary);font-variant-numeric:tabular-nums}
.akw-chip.is-on{color:var(--dsw-alias-bg-base);background:var(--dsw-alias-state-business-primary);border-color:transparent}
.akw-chip.is-on i{color:var(--dsw-alias-bg-base);background:transparent;opacity:.72}

/* 配置池标记：圆形色标（在池里才亮） */
.akw-pool-dot{flex:none;width:8px;height:8px;border-radius:50%;border:1px solid var(--dsw-alias-border-l4);background:transparent}
.akw-pool-dot.is-on{border-color:transparent;background:var(--dsw-alias-state-business-primary);box-shadow:0 0 0 3px var(--dsw-alias-interactive-bg-hover)}
/* 新增表单（akso-pass 的 .field-pair） */
.akw-form{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px;border:1px solid var(--dsw-alias-border-l3);border-radius:16px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-panel)}
/* 添加账号：单列（用户要求，字段一列往下排）；太宽的单列表单不好填，限一个舒服的宽度 */
.akw-form--single{grid-template-columns:1fr;max-width:620px}
.akw-form__head{grid-column:1/-1;display:flex;align-items:baseline;gap:8px}
.akw-form__head h2{margin:0;font-size:15px;line-height:20px;font-weight:600}
.akw-form__head span{font-size:12px;color:var(--dsw-alias-label-tertiary)}
.akw-field{display:flex;flex-direction:column;gap:5px;min-width:0}
.akw-field__label{font-size:11px;line-height:14px;color:var(--dsw-alias-label-caption)}
.akw-input{box-sizing:border-box;width:100%;height:32px;padding:0 10px;font:inherit;font-size:13px;color:var(--dsw-alias-label-primary);background:var(--dsw-specific-input-major);border:1px solid var(--dsw-alias-border-l3);border-radius:12px;outline:none;transition:border-color .14s ease}
.akw-input:focus{border-color:var(--dsw-alias-state-business-primary)}
.akw-input::placeholder{color:var(--dsw-alias-label-caption)}
.akw-form__actions{grid-column:1/-1;display:flex;align-items:center;gap:8px;justify-content:flex-end}

/* 空 / 错 / 载入 */
.akw-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:56px 20px;border:1px dashed var(--dsw-alias-border-l2);border-radius:16px;text-align:center}
.akw-empty__icon{display:grid;place-items:center;width:44px;height:44px;border-radius:16px;background:var(--dsw-alias-interactive-bg-hover-solid);color:var(--dsw-alias-label-tertiary)}
.akw-empty__title{font-size:15px;font-weight:600}
.akw-empty__desc{font-size:12px;color:var(--dsw-alias-label-tertiary);max-width:360px}
.akw-skeleton{height:116px;border-radius:14px;background:var(--dsw-alias-interactive-bg-hover-solid);animation:akw-pulse 1.4s ease-in-out infinite}
@keyframes akw-pulse{0%,100%{opacity:.5}50%{opacity:1}}

/* 设备流：验证码 + 重新打开浏览器 */
.akw-login__primary{height:36px;font-size:14px}
.akw-login__fallback{display:flex;flex-direction:column;gap:10px;padding-top:10px}
.akw-device{display:flex;flex-direction:column;gap:10px;padding:12px 14px;border:1px dashed var(--dsw-alias-border-l2);border-radius:12px}
.akw-device__code{display:flex;align-items:baseline;gap:10px}
.akw-device__code span{font-size:11px;color:var(--dsw-alias-label-caption)}
.akw-device__code b{font-size:20px;line-height:24px;letter-spacing:2px;font-variant-numeric:tabular-nums;color:var(--dsw-alias-state-business-primary)}
.akw-device__actions{display:flex;gap:8px}

/* ───────────────── 账号条（conversation.composer.dock） ─────────────────
 * 宿主把这一行渲染成 display:flex / justify-content:center / align-items:center / gap:12px，
 * 顺序是「dock 条目 → ContextMeter」。slot 列表的包裹层是 display:contents，
 * 所以 order 与 margin-left:auto 作用在**行**上：把账号条排到 ContextMeter 右侧、贴最右。
 * 固定高度 24px，不改变 composer 的垂直节奏（规范 §4.4）。
 * 2026-10-26：只展示"已连接给 agent"的那一个账号；点它弹小面板换/清。 */
.akw-strip{position:relative;order:99;margin-left:auto;display:flex;align-items:center;gap:6px;height:24px;min-width:0;max-width:min(420px,40vw);font-size:12px}

/* 已连接的账号：页签名 · 站点名，一个胶囊，不再有 + */
.akw-connected{box-sizing:border-box;max-width:100%;height:24px;padding:0 12px;border-radius:999px;border:1px solid var(--dsw-alias-state-business-primary);background:var(--dsw-alias-interactive-bg-hover);color:var(--dsw-alias-label-primary);font:inherit;font-size:12px;line-height:22px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:border-color .14s ease,background .14s ease}
.akw-connected:hover{border-color:var(--dsw-alias-border-l4);background:var(--dsw-alias-interactive-bg-hover-solid)}

.akw-strip__add{flex:none;height:24px;padding:0 10px;border-radius:999px;border:1px dashed var(--dsw-alias-border-l3);background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;line-height:22px;cursor:pointer;white-space:nowrap;transition:color .14s ease,border-color .14s ease}
.akw-strip__add:hover{color:var(--dsw-alias-label-primary);border-color:var(--dsw-alias-state-business-primary)}
.akw-strip__retry{height:24px;padding:0 10px;border-radius:999px;border:1px solid var(--dsw-alias-state-error-primary);background:transparent;color:var(--dsw-alias-state-error-primary);font:inherit;font-size:12px;cursor:pointer}

/* 换/清连接的小面板（点已连接的胶囊弹出；点外面或 Esc 关闭） */
.akw-picker{position:absolute;right:0;bottom:32px;z-index:40;display:flex;flex-direction:column;gap:8px;width:288px;max-height:440px;padding:12px;border:1px solid var(--dsw-alias-border-l3);border-radius:14px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-prominent)}
.akw-picker__head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.akw-picker__head b{font-size:13px;line-height:18px;font-weight:600}
.akw-picker__x{width:22px;height:22px;border:1px solid var(--dsw-alias-border-l3);border-radius:8px;background:transparent;color:var(--dsw-alias-label-tertiary);font-size:14px;line-height:18px;cursor:pointer}
.akw-picker__x:hover{color:var(--dsw-alias-label-primary);border-color:var(--dsw-alias-border-l4)}
.akw-picker__list{display:flex;flex-direction:column;gap:4px;margin:0;padding:0;list-style:none;overflow-y:auto}
.akw-picker__item{display:flex;align-items:center;gap:8px;width:100%;padding:7px 9px;border:1px solid transparent;border-radius:10px;background:transparent;color:var(--dsw-alias-label-primary);font:inherit;text-align:left;cursor:pointer}
.akw-picker__item:hover{background:var(--dsw-alias-interactive-bg-hover)}
.akw-picker__item.is-on{border-color:var(--dsw-alias-state-business-primary);background:var(--dsw-alias-interactive-bg-hover)}
.akw-picker__name{flex:1;min-width:0;font-size:13px;line-height:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-picker__site{flex:none;max-width:45%;font-size:11px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-picker__tick{flex:none;font-size:12px;color:var(--dsw-alias-state-business-primary)}
.akw-picker__empty{display:flex;flex-direction:column;gap:8px;align-items:flex-start}
.akw-picker__empty p{margin:0;font-size:12px;color:var(--dsw-alias-label-tertiary)}
.akw-picker__clear{align-self:flex-start;height:26px;padding:0 10px;border:1px solid var(--dsw-alias-border-l3);border-radius:10px;background:transparent;color:var(--dsw-alias-state-error-primary);font:inherit;font-size:12px;cursor:pointer}
.akw-picker__clear:hover{background:var(--dsw-alias-interactive-bg-hover);border-color:var(--dsw-alias-border-l4)}

/* 令牌一角：一行摘要 + 展开后的原文（默认不摊开，免得一开面板就是一大串 base64） */
.akw-token{display:flex;flex-direction:column;gap:6px;padding-top:8px;border-top:1px solid var(--dsw-alias-border-l2)}
.akw-token__row{display:flex;align-items:center;gap:6px;min-width:0}
.akw-token__dot{flex:none;width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-label-tertiary)}
.akw-token__row.is-on .akw-token__dot{background:var(--dsw-alias-state-success-primary)}
.akw-token__row.is-warn .akw-token__dot{background:var(--dsw-alias-state-warn-primary)}
.akw-token__row.is-off .akw-token__dot{background:var(--dsw-alias-state-error-primary)}
.akw-token__name{flex:none;font-size:12px;line-height:16px;color:var(--dsw-alias-label-secondary)}
.akw-token__meta{flex:1;min-width:0;font-size:11px;line-height:16px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right}
.akw-token__toggle{flex:none;height:22px;padding:0 8px;border:1px solid var(--dsw-alias-border-l3);border-radius:8px;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:11px;cursor:pointer}
.akw-token__toggle:hover{background:var(--dsw-alias-interactive-bg-hover);color:var(--dsw-alias-label-primary)}
.akw-token__body{display:flex;flex-direction:column;gap:6px}
.akw-token__times{display:flex;gap:10px;font-size:11px;line-height:16px;color:var(--dsw-alias-label-tertiary)}
.akw-token__raw{max-height:76px;margin:0;padding:8px;border:1px solid var(--dsw-alias-border-l2);border-radius:10px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;line-height:16px;overflow:auto;word-break:break-all;white-space:pre-wrap;user-select:all}
.akw-token__acts{display:flex;justify-content:flex-end}

/* 登录门（akso-pass 的 login-mask 形态） */
.akw-body--center{align-items:center;justify-content:center}
.akw-login{display:flex;flex-direction:column;gap:14px;width:min(420px,100%);margin:6vh auto 0;padding:22px;border:1px solid var(--dsw-alias-border-l3);border-radius:16px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-prominent)}
.akw-login__title{margin:0;font-size:18px;line-height:24px;font-weight:600}
.akw-login__desc{margin:-6px 0 0;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-login__adv{font-size:12px;color:var(--dsw-alias-label-tertiary)}
.akw-login__adv summary{cursor:pointer;padding:2px 0 6px}
.akw-login__hint{margin:0;font-size:11px;line-height:16px;color:var(--dsw-alias-label-caption)}
.akw-login__err{margin:0;font-size:12px;color:var(--dsw-alias-state-error-primary)}

/* 同步状态（顶栏小字：让"四处同一份数据"可见） */
.akw-sync{font-size:11px;color:var(--dsw-alias-label-caption);white-space:nowrap;font-variant-numeric:tabular-nums}

/* 配置池页：说明 + 计数 + 行列表（行沿用 .akw-row） */
.akw-pool{display:flex;flex-direction:column;gap:12px}
.akw-pool__head{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.akw-pool__desc{margin:0;max-width:62ch;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-pool__tools{display:flex;align-items:center;gap:8px}
.akw-pool__count{font-size:11px;color:var(--dsw-alias-label-caption);font-variant-numeric:tabular-nums}
.akw-pool__pick{display:flex;flex-direction:column;gap:10px;padding:12px 14px;border:1px dashed var(--dsw-alias-border-l3);border-radius:14px;background:var(--dsw-alias-bg-layer-1)}
.akw-pool__pickhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.akw-pool__pickhead b{font-size:13px;font-weight:600}
.akw-pool__pickhead span{font-size:11.5px;color:var(--dsw-alias-label-tertiary)}
.akw-pool__pickempty{margin:0;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
/* 行式列表：站点 / 盒子 */
.akw-rows{display:flex;flex-direction:column;gap:8px;margin:0;padding:0;list-style:none}
.akw-row{display:flex;align-items:center;gap:12px;padding:10px 14px;border:1px solid var(--dsw-alias-border-l3);border-radius:12px;background:var(--dsw-alias-bg-layer-1)}
.akw-row:hover{border-color:var(--dsw-alias-border-l3)}
.akw-row__main{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.akw-row__main b{display:flex;align-items:center;gap:6px;font-size:13px;line-height:18px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-row__main span{font-size:12px;line-height:16px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-row__meta{flex:none;font-size:12px;color:var(--dsw-alias-label-tertiary);white-space:nowrap}
.akw-row__inline{flex:none;display:flex;align-items:center;gap:6px}
.akw-chiplet{padding:1px 7px;border:1px solid var(--dsw-alias-border-l3);border-radius:999px;font-size:11px;line-height:16px;font-weight:400;color:var(--dsw-alias-label-tertiary)}
.akw-chiplet--off{border-color:var(--dsw-alias-state-warn-primary);color:var(--dsw-alias-state-warn-primary)}
.akw-input--inline{width:150px;height:28px}
.akw-field--wide{grid-column:1/-1}

/* ───────────────── 配置工具（横向二级导航 + 接口台账） ─────────────────
 * 顶部是分区 tab（与账号中心的 tab 同一套下划线语言），下面是这一区的说明与卡片；
 * 对象处理再按对象/选项集/字段/布局分栏。颜色只走主题 token（规范 §5 规则 10）。
 */
.akw-cfg{display:flex;flex-direction:column;gap:16px;width:auto;margin:-18px -24px 0}
.akw-cfg__tabs{display:flex;align-items:center;gap:2px;padding:0 24px;border-bottom:1px solid var(--dsw-alias-border-l2);overflow-x:auto;scrollbar-width:none}
.akw-cfg__tabs::-webkit-scrollbar{display:none}
.akw-cfg__tab{position:relative;flex:none;height:38px;padding:0 12px;border:0;border-radius:9px 9px 0 0;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:13px;line-height:38px;cursor:pointer;white-space:nowrap;transition:background .14s ease,color .14s ease}
.akw-cfg__tab:hover{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-interactive-bg-hover)}
.akw-cfg__tab:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:-2px}
.akw-cfg__tab.is-active{color:var(--dsw-alias-label-primary);font-weight:600}
.akw-cfg__tab.is-active::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;border-radius:2px;background:var(--dsw-alias-state-business-primary)}
.akw-cfg__tab i{margin-left:7px;padding:0 6px;border-radius:999px;background:var(--dsw-alias-interactive-bg-hover-solid);font-style:normal;font-size:11px;font-weight:500;line-height:16px;color:var(--dsw-alias-label-tertiary);font-variant-numeric:tabular-nums}
.akw-cfg__tab.is-active i{background:var(--dsw-alias-state-business-primary);color:var(--dsw-alias-bg-base)}

.akw-cfg__main{display:flex;flex-direction:column;gap:14px;min-width:0;padding:0 24px}
.akw-cfg__lede{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.akw-cfg__lede p{margin:2px 0 0;max-width:62ch;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__stats{flex:none;display:flex;gap:6px}
.akw-cfg__stat{display:flex;align-items:baseline;gap:5px;padding:4px 9px;border:1px solid var(--dsw-alias-border-l3);border-radius:10px;font-variant-numeric:tabular-nums}
.akw-cfg__stat b{font-size:13px;font-weight:600}
.akw-cfg__stat em{font-style:normal;font-size:11px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__stat.is-danger b{color:var(--dsw-alias-state-error-primary)}
.akw-cfg__stat.is-warn b{color:var(--dsw-alias-state-warn-primary)}

/* 写入不可逆：一句提示，不用整块告警条 */
.akw-cfg__legend{margin:-4px 0 0;padding-left:10px;border-left:2px solid var(--dsw-alias-state-warn-primary);font-size:11.5px;line-height:17px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__session{display:flex;align-items:center;gap:8px;padding:8px 12px;border:1px dashed var(--dsw-alias-border-l3);border-radius:12px;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__session b{font-weight:600;color:var(--dsw-alias-label-primary)}
.akw-cfg__dot{flex:none;width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-label-caption)}
.akw-cfg__session.is-on .akw-cfg__dot{background:var(--dsw-alias-state-business-primary)}
/* eGMP 会话：条 + 登录表单，登录成功改成实线并把点染成主色 */
.akw-cfg__auth{display:grid;gap:10px}
.akw-cfg__auth.is-on .akw-cfg__session{border-style:solid;border-color:var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-1)}
.akw-cfg__auth.is-on .akw-cfg__dot{background:var(--dsw-alias-state-business-primary)}
.akw-cfg__sessiontext{flex:1;min-width:0}
.akw-cfg__sessiontext em{margin-left:6px;font-style:normal;color:var(--dsw-alias-label-caption)}
.akw-cfg__sessionacts{display:flex;flex:none;gap:6px}

.akw-cfg__subtabs{align-self:flex-start;display:flex;flex-wrap:wrap;gap:3px;padding:3px;border:1px solid var(--dsw-alias-border-l2);border-radius:12px;background:var(--dsw-alias-bg-layer-1)}
.akw-cfg__subtab{display:flex;align-items:center;gap:6px;height:28px;padding:0 11px;border:0;border-radius:9px;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12.5px;cursor:pointer;transition:background .14s ease,color .14s ease}
.akw-cfg__subtab:hover{color:var(--dsw-alias-label-primary)}
.akw-cfg__subtab:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:1px}
.akw-cfg__subtab.is-active{background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font-weight:600;box-shadow:0 1px 2px rgb(0 0 0 / 8%)}
.akw-cfg__subtab span{font-size:11px;font-weight:400;color:var(--dsw-alias-label-caption);font-variant-numeric:tabular-nums}

.akw-cfg__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(318px,1fr));align-items:start;gap:12px}
.akw-cfg__card{display:flex;flex-direction:column;gap:7px;padding:13px 14px 12px;border:1px solid var(--dsw-alias-border-l3);border-radius:14px;background:var(--dsw-alias-bg-layer-1);cursor:pointer;transition:border-color .14s ease,transform .14s ease,box-shadow .14s ease}
.akw-cfg__card:hover{border-color:var(--dsw-alias-border-l4);transform:translateY(-1px)}
.akw-cfg__card:focus-visible{outline:2px solid var(--dsw-focus-ring-color);outline-offset:1px}
.akw-cfg__card.is-blocked{border-color:var(--dsw-alias-state-error-primary)}
.akw-cfg__card.is-open{transform:none;box-shadow:var(--dsw-elevation-panel)}
.akw-cfg__cardtop{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.akw-cfg__cardtop strong{font-size:13px;line-height:18px;font-weight:600}
.akw-cfg__flags{flex:none;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:4px}
.akw-cfg__id,.akw-cfg__path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10.5px;line-height:15px}
.akw-cfg__id{color:var(--dsw-alias-label-caption)}
.akw-cfg__path{display:flex;gap:6px;color:var(--dsw-alias-label-tertiary);word-break:break-all}
.akw-cfg__path b{flex:none;font-weight:600;color:var(--dsw-alias-label-secondary)}
.akw-cfg__desc{margin:0;font-size:11.5px;line-height:1.5;color:var(--dsw-alias-label-tertiary);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.akw-cfg__meta{display:flex;align-items:center;gap:8px;margin-top:auto;padding-top:2px}
.akw-cfg__count{font-size:10.5px;color:var(--dsw-alias-label-caption);font-variant-numeric:tabular-nums}
.akw-cfg__more{margin-left:auto;font-size:11px;color:var(--dsw-alias-state-business-primary)}

.akw-cfg__tag{padding:1px 7px;border:1px solid;border-radius:999px;font-size:10.5px;line-height:15px;white-space:nowrap}
.akw-cfg__tag.is-ok{color:var(--dsw-alias-state-success-primary,#2e7d32)}
.akw-cfg__tag.is-warn{color:var(--dsw-alias-state-warn-primary,#b26a00)}
.akw-cfg__tag.is-danger{color:var(--dsw-alias-state-error-primary,#b00020)}
.akw-cfg__tag.is-kind-write{color:var(--dsw-alias-label-secondary);border-color:var(--dsw-alias-border-l4)}
.akw-cfg__tag.is-kind-query,.akw-cfg__tag.is-kind-auth{color:var(--dsw-alias-label-caption);border-color:var(--dsw-alias-border-l3)}

.akw-cfg__detail{display:flex;flex-direction:column;gap:8px;margin-top:2px;padding-top:10px;border-top:1px solid var(--dsw-alias-border-l2);cursor:default}
.akw-cfg__note{margin:0;font-size:11px;line-height:16px;color:var(--dsw-alias-state-warn-primary)}
.akw-cfg__noparam{font-size:11px;color:var(--dsw-alias-label-caption)}
.akw-cfg__params{width:100%;border-collapse:collapse;font-size:11px}
.akw-cfg__params th{padding:2px 8px 4px 0;font-weight:400;text-align:left;color:var(--dsw-alias-label-caption)}
.akw-cfg__params td{padding:3px 8px 3px 0;color:var(--dsw-alias-label-secondary);vertical-align:top}
.akw-cfg__params td.is-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dsw-alias-label-primary)}

.akw-cfg__empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;width:100%;max-width:720px;padding:46px 24px;border:1px dashed var(--dsw-alias-border-l3);border-radius:16px;text-align:center}
.akw-cfg__empty svg{width:48px;height:48px;margin-bottom:2px;color:var(--dsw-alias-label-caption)}
.akw-cfg__empty b{font-size:14px;font-weight:600;color:var(--dsw-alias-label-secondary)}
.akw-cfg__empty span{max-width:52ch;font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__empty em{max-width:52ch;font-style:normal;font-size:11px;line-height:16px;color:var(--dsw-alias-label-caption)}

.akw-cfg__hint{font-size:12px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.akw-cfg__hint.is-error{color:var(--dsw-alias-state-error-primary)}

@media (max-width: 980px){
  .akw-stats,.akw-brandtext p{display:none}
  .akw-search input{width:110px}
  .akw-grid{grid-template-columns:1fr}
  .akw-form{grid-template-columns:1fr}
  .akw-cfg__grid{grid-template-columns:1fr}
  .akw-cfg__lede{flex-direction:column;align-items:flex-start;gap:8px}
  .akw-cfg__tab{padding:0 9px}
}
`
