/** 模块页共享侧边栏。
 *
 * 账号中心 / 对象分析 / Skill / 沙箱是独立 HTML，导航栏由本脚本注入，
 * 这样点击任何导航项都不会丢失侧栏；折叠状态存 localStorage，跨页面保持。
 */
(() => {
  const KEY = "workbench.sidebar.hidden";
  const root = document.documentElement;
  root.classList.add("app-sidebar-page");

  let hidden = false;
  try { hidden = localStorage.getItem(KEY) === "1"; } catch { /* ignore */ }
  root.classList.toggle("sidebar-hidden", hidden);

  const icon = (paths) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const icons = {
    chat: icon('<path d="M21 12a8 8 0 0 1-8 8H4l2.2-3.2A8 8 0 1 1 21 12Z"/><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01"/>'),
    settings: icon('<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.14-1.4l2-1.55-2-3.46-2.35.94A7 7 0 0 0 14.1 5.1L13.7 2.6h-3.4l-.4 2.5a7 7 0 0 0-2.4 1.42l-2.36-.94-2 3.46 2 1.55A7 7 0 0 0 5 12c0 .48.05.94.14 1.4l-2 1.55 2 3.46 2.35-.94a7 7 0 0 0 2.4 1.42l.4 2.5h3.4l.4-2.5a7 7 0 0 0 2.4-1.42l2.36.94 2-3.46-2-1.55c.1-.46.15-.92.15-1.4Z"/>'),
    insight: icon('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/>'),
    accounts: icon('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 2.6 4 5.6 4 9s-1.2 6.4-4 9c-2.8-2.6-4-5.6-4-9s1.2-6.4 4-9Z"/><circle cx="9" cy="9" r="1"/><circle cx="15" cy="9" r="1"/>'),
    skills: icon('<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10l2 2h6.5A1.5 1.5 0 0 1 20 7.5v10A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-12Z"/><path d="M12 11v5M9.5 13.5h5"/>'),
    sandbox: icon('<path d="M12 3 4 6.5v5c0 4.6 3.2 8.4 8 9.5 4.8-1.1 8-4.9 8-9.5v-5L12 3Z"/><path d="M9.5 12.2l1.8 1.8 3.4-3.6"/>'),
    panelLeft: icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m15 9-3 3 3 3"/>'),
    panelRight: icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m13 9 3 3-3 3"/>'),
  };

  const path = location.pathname;
  const item = (label, href, key, iconKey, active = false) =>
    `<a class="nav-item${active ? " active" : ""}" id="nav-${key}" href="${href}"${active ? ' aria-current="page"' : ""}>${icons[iconKey]}${label}</a>`;
  const active = {
    insight: path.endsWith("/insight.html"),
    accounts: path.endsWith("/accounts.html"),
    skills: path.endsWith("/skills.html"),
    sandbox: path.endsWith("/sandbox.html"),
  };

  const aside = document.createElement("aside");
  aside.className = "app-sidebar";
  aside.setAttribute("aria-label", "主导航");
  aside.innerHTML = `
    <div class="sidebar-brand">
      <button class="sidebar-toggle" id="btn-sidebar-toggle" type="button" title="隐藏侧边栏" aria-label="隐藏侧边栏" aria-expanded="true">${icons.panelLeft}</button>
      <div class="brand-emblem" aria-hidden="true">A</div>
      <div class="brand-name"><b>AKSO</b><small>全家桶工作台</small></div>
    </div>
    <nav class="sidebar-nav">
      ${item("对话", "/", "chat", "chat")}
      ${item("设置", "/?view=settings", "settings", "settings")}
    </nav>
    <div class="sidebar-section">
      <div class="sidebar-section-title"><span>平台模块</span></div>
      <nav class="sidebar-nav">
        ${item("对象分析", "/static/pages/insight.html", "insight", "insight", active.insight)}
        ${item("账号中心", "/static/pages/accounts.html", "accounts", "accounts", active.accounts)}
      </nav>
    </div>
    <div class="sidebar-footer">
      <span class="engine-dot"></span><span>本地服务</span>
      <button id="vault-chip" class="sidebar-account" type="button" title="登录 / 打开云端账号库">登录云端账号</button>
    </div>`;
  document.body.prepend(aside);

  // 全局登录入口：未登录显示「登录云端账号」；已登录显示邮箱前缀。
  // 点它统一进账号中心（那里有设备流遮罩；登录与同步是同一页，不再多一个入口）。
  (async () => {
    const chip = document.getElementById("vault-chip");
    if (!chip) return;
    try {
      const res = await fetch("/api/vault/status", { cache: "no-store" });
      const status = await res.json();
      if (status.loggedIn) {
        chip.textContent = status.displayName || status.email || "已登录";
        chip.title = `云端账号：${status.email || status.displayName || ""}`;
        chip.classList.add("is-in");
      } else {
        chip.textContent = "登录云端账号";
      }
    } catch { /* 状态读不到就保留“登录”入口，不阻断页面 */ }
    chip.addEventListener("click", () => {
      location.href = "/static/pages/accounts.html";
    });
  })();

  const floating = document.createElement("button");
  floating.className = "app-sidebar-floating-toggle";
  floating.id = "btn-sidebar-open";
  floating.type = "button";
  floating.title = "显示侧边栏";
  floating.setAttribute("aria-label", "显示侧边栏");
  floating.innerHTML = icons.panelRight;
  document.body.append(floating);

  function setHidden(next) {
    root.classList.toggle("sidebar-hidden", next);
    const hide = document.getElementById("btn-sidebar-toggle");
    if (hide) {
      hide.title = next ? "显示侧边栏" : "隐藏侧边栏";
      hide.setAttribute("aria-label", hide.title);
      hide.setAttribute("aria-expanded", String(!next));
    }
    try { localStorage.setItem(KEY, next ? "1" : "0"); } catch { /* ignore */ }
  }

  document.getElementById("btn-sidebar-toggle")?.addEventListener("click", () => {
    setHidden(!root.classList.contains("sidebar-hidden"));
  });
  document.getElementById("btn-sidebar-open")?.addEventListener("click", () => setHidden(false));
})();
