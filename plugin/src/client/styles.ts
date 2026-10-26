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
.akw-mark{flex:none;display:grid;place-items:center;width:28px;height:28px;border-radius:9px;color:var(--dsw-alias-bg-base);background:var(--dsw-alias-state-business-primary)}
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

/* 下划线式 tab（akso-pass 的 .tabs / .tab.is-active） */
.akw-tabs{flex:none;display:flex;align-items:center;gap:2px;padding:0 24px;border-bottom:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-base)}
.akw-tab{position:relative;height:36px;padding:0 10px;border:0;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:13px;line-height:36px;cursor:pointer;white-space:nowrap}
.akw-tab:hover{color:var(--dsw-alias-label-primary)}
.akw-tab.is-active{color:var(--dsw-alias-label-primary);font-weight:600}
.akw-tab.is-active::after{content:"";position:absolute;left:8px;right:8px;bottom:-1px;height:2px;border-radius:2px;background:var(--dsw-alias-state-business-primary)}
.akw-tab i{margin-left:6px;font-style:normal;font-size:11px;color:var(--dsw-alias-label-tertiary);font-weight:400}
.akw-tab.is-active i{color:var(--dsw-alias-state-business-primary)}

.akw-body{flex:1;min-height:0;overflow:auto;padding:18px 24px 40px;display:flex;flex-direction:column;gap:14px}

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
.akw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin:0;padding:0;list-style:none}
.akw-card{box-sizing:border-box;display:flex;flex-direction:column;gap:12px;padding:14px;border:1px solid var(--dsw-alias-border-l3);border-radius:16px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-panel);transition:border-color .14s ease,box-shadow .14s ease,transform .14s ease}
.akw-card:hover{border-color:var(--dsw-alias-border-l3);transform:translateY(-1px)}
.akw-card.is-connected{border-color:var(--dsw-alias-state-business-primary);box-shadow:0 0 0 1px var(--dsw-alias-state-business-primary),var(--dsw-elevation-panel)}
.akw-card__head{display:flex;align-items:center;gap:10px;min-width:0}
.akw-avatar{flex:none;display:grid;place-items:center;width:32px;height:32px;border-radius:10px;font-size:13px;font-weight:600;color:var(--dsw-alias-label-primary);background:var(--dsw-alias-interactive-bg-hover-solid)}
.akw-card__id{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
.akw-card__id b{font-size:15px;line-height:20px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__id span{font-size:12px;line-height:16px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-badge{flex:none;padding:2px 8px;border:1px solid var(--dsw-alias-state-business-primary);border-radius:999px;font-size:11px;line-height:16px;color:var(--dsw-alias-state-business-primary)}

.akw-facts{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;margin:0;padding:10px 12px;border-radius:12px;background:var(--dsw-alias-bg-base)}
.akw-facts dt{font-size:11px;line-height:14px;color:var(--dsw-alias-label-caption)}
.akw-facts dd{margin:2px 0 0;font-size:13px;line-height:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__actions{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.akw-card__note{margin:0;font-size:12px;line-height:16px;color:var(--dsw-alias-label-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.akw-card__hint{font-size:11px;color:var(--dsw-alias-state-business-primary)}

/* 新增表单（akso-pass 的 .field-pair） */
.akw-form{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px;border:1px solid var(--dsw-alias-border-l3);border-radius:16px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-panel)}
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
.akw-skeleton{height:132px;border-radius:16px;background:var(--dsw-alias-interactive-bg-hover-solid);animation:akw-pulse 1.4s ease-in-out infinite}
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
.akw-picker{position:absolute;right:0;bottom:32px;z-index:40;display:flex;flex-direction:column;gap:8px;width:288px;max-height:340px;padding:12px;border:1px solid var(--dsw-alias-border-l3);border-radius:14px;background:var(--dsw-alias-bg-layer-1);box-shadow:var(--dsw-elevation-prominent)}
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

@media (max-width: 980px){
  .akw-stats,.akw-brandtext p{display:none}
  .akw-search input{width:110px}
  .akw-grid{grid-template-columns:1fr}
  .akw-form{grid-template-columns:1fr}
}
`
