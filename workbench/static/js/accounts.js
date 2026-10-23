/** 账号中心（云端版）。
 *
 * 数据源只有云端 `/api/vault/library`；本地账号库只作为「本地推到云端」的迁移输入，
 * 页面不再提供本地增删改、盒子/分配池/备份管理。
 * 未登录时用遮罩挡住内容，中间只留设备流登录。
 */

import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
const COLORS = ["#1E6FFF", "#0FA3B1", "#7C5CFF", "#FF7A1A", "#22C55E"];

const state = {
  library: null,
  status: null,
  box: "",
  search: "",
  loginBusy: false,
  cancelled: false,
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function colorOf(index) {
  return COLORS[((index % COLORS.length) + COLORS.length) % COLORS.length];
}

function showNotice(text = "", kind = "") {
  const node = $("cloud-notice");
  if (!node) return;
  node.className = `cloud-notice${kind ? ` ${kind}` : ""}${text ? "" : " hidden"}`;
  node.textContent = text;
}

function setOverlay(visible, message = "") {
  const overlay = $("login-overlay");
  if (!overlay) return;
  overlay.classList.toggle("hidden", !visible);
  $("cloud-content")?.setAttribute("aria-hidden", String(visible));
  if (message) $("login-message").textContent = message;
}

function paintIdentity(status) {
  const el = $("cloud-user");
  if (!el) return;
  const name = status?.displayName || status?.email || "";
  el.textContent = status?.loggedIn ? (name || "已登录云端") : "未登录";
  el.title = status?.loggedIn ? `云端账号库 · ${name || "已登录"}` : "云端账号库未登录";
  $("btn-logout")?.classList.toggle("hidden", !status?.loggedIn);
}

function boxName(account) {
  return (account?.box || "").trim() || state.library?.defaultBox || "默认盒子";
}

function renderStats() {
  const lib = state.library;
  const counts = lib?.counts || {};
  $("stat-accounts").textContent = String(counts.accounts ?? lib?.accounts?.length ?? "—");
  $("stat-sites").textContent = String(counts.sites ?? lib?.sites?.length ?? "—");
  $("stat-boxes").textContent = String(counts.boxes ?? lib?.boxes?.length ?? "—");
  $("cloud-counts").textContent = lib
    ? `${counts.sites ?? 0} 站点 · ${counts.accounts ?? 0} 账号 · ${counts.boxes ?? 0} 盒子`
    : "";
}

function renderBoxChips() {
  const wrap = $("box-chips");
  if (!wrap) return;
  const lib = state.library;
  const boxes = lib?.boxes || [];
  wrap.innerHTML = "";
  const add = (label, value, count) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chip${state.box === value ? " active" : ""}`;
    button.dataset.box = value;
    button.textContent = `${label}${count == null ? "" : ` ${count}`}`;
    wrap.appendChild(button);
  };
  add("全部", "", lib?.counts?.accounts ?? (lib?.accounts?.length || 0));
  for (const box of boxes) {
    add(box.name, box.name, box.count);
  }
  wrap.querySelectorAll("button[data-box]").forEach((button) => {
    button.addEventListener("click", () => {
      state.box = button.dataset.box || "";
      renderBoxChips();
      renderAccounts();
    });
  });
}

function visibleAccounts() {
  const all = state.library?.accounts || [];
  const needle = state.search.trim().toLowerCase();
  return all.filter((account) => {
    if (state.box && boxName(account) !== state.box) return false;
    if (!needle) return true;
    return [
      account.username,
      account.tabName,
      account.siteName,
      account.baseUrl,
      account.box,
      ...(account.tags || []),
    ]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
}

function renderAccounts() {
  const wall = $("acc-wall");
  if (!wall) return;
  const rows = visibleAccounts();
  wall.innerHTML = "";
  if (!rows.length) {
    wall.innerHTML = '<li class="empty" style="grid-column:1/-1"><span class="empty-ico">☁️</span>这个筛选下没有云端账号</li>';
    return;
  }
  rows.forEach((account, index) => {
    const color = colorOf(index);
    const tags = (account.tags || []).slice(0, 3);
    const alias = (account.tabName || "").trim() || account.username || "（未命名）";
    const li = document.createElement("li");
    li.className = "account-card cloud-account-card";
    li.style.setProperty("--accent", color);
    li.innerHTML = `
      <div class="ac-head">
        <div class="ac-avatar" style="--ring:${color}">${esc(alias.slice(0, 1).toUpperCase())}</div>
        <div class="meta">
          <div class="alias">${esc(alias)}</div>
          <div class="sub">
            <span class="box-tag">${esc(boxName(account))}</span>
            ${esc(account.siteName || "")}${account.baseUrl ? ` · ${esc(account.baseUrl)}` : ""}
          </div>
        </div>
      </div>
      ${alias !== account.username ? `<div class="cloud-account-name">${esc(account.username)}</div>` : ""}
      ${tags.length ? `<div class="chips">${tags.map((tag) => `<span class="chip">${esc(tag)}</span>`).join("")}</div>` : ""}
      <div class="cloud-account-foot">
        <span class="cloud-readonly">只读</span>
        <span class="cloud-password">${account.hasPassword ? "已存密码" : "未存密码"}</span>
      </div>`;
    wall.appendChild(li);
  });
}

function renderSites() {
  const list = $("site-list");
  if (!list) return;
  const sites = state.library?.sites || [];
  list.innerHTML = sites.length
    ? sites.map((site) => `
      <li>
        <span class="cloud-list-main">${esc(site.name || site.baseUrl || site.id)}</span>
        <span class="cloud-list-sub">${esc(site.baseUrl || "")}</span>
        <span class="cloud-list-count">${site.accountCount ?? 0} 账号</span>
      </li>`).join("")
    : '<li class="empty">云端没有站点</li>';
}

function renderBoxes() {
  const list = $("box-list");
  if (!list) return;
  const boxes = state.library?.boxes || [];
  list.innerHTML = boxes.length
    ? boxes.map((box) => `
      <li>
        <span class="cloud-list-main">${esc(box.name)}</span>
        <span class="cloud-list-sub">${box.siteName ? esc(box.siteName) : "未绑定站点"}${box.disabled ? " · 已停用" : ""}</span>
        <span class="cloud-list-count">${box.count ?? 0} 账号</span>
      </li>`).join("")
    : '<li class="empty">云端没有盒子</li>';
}

function renderLibrary() {
  renderStats();
  renderBoxChips();
  renderAccounts();
  renderSites();
  renderBoxes();
}

async function loadLibrary(refresh = false) {
  try {
    const lib = await api.getVaultLibrary(refresh);
    state.library = lib;
    showNotice(
      lib.stale ? `云端暂时不可用，显示上次同步的数据${lib.error ? `：${lib.error}` : ""}` : "",
      lib.stale ? "warn" : "",
    );
    renderLibrary();
  } catch (error) {
    if (error.status === 401) {
      setOverlay(true, "云端登录已过期，请重新登录。");
      paintIdentity({ loggedIn: false });
      return;
    }
    showNotice(`读取云端账号库失败：${error.message}`, "error");
  }
}

async function afterLogin() {
  state.status = await api.getVaultStatus();
  paintIdentity(state.status);
  setOverlay(false);
  await loadLibrary(true);
}

function setLoginProgress(text, isError = false) {
  const el = $("login-progress");
  if (!el) return;
  el.classList.toggle("hidden", !text);
  el.classList.toggle("error", isError);
  el.textContent = text || "";
}

async function startLogin() {
  if (state.loginBusy) return;
  state.loginBusy = true;
  state.cancelled = false;
  const button = $("cloud-login");
  if (button) button.disabled = true;
  setLoginProgress("正在申请设备授权…");
  try {
    const info = await api.vaultLoginStart("桌面工作台");
    if (!info?.verificationUrl || !info?.deviceCode) {
      throw new Error("云端没有返回授权地址");
    }
    window.open(info.verificationUrl, "_blank", "noopener");
    setLoginProgress("等待浏览器授权…");
    const interval = Math.max(1, Number(info.interval) || 3) * 1000;
    const expires = Math.max(30, Number(info.expiresIn) || 600) * 1000;
    const deadline = Date.now() + expires;
    while (!state.cancelled && Date.now() < deadline) {
      await sleep(interval);
      const result = await api.vaultLoginPoll(info.deviceCode);
      if (result?.status === "approved") {
        setLoginProgress("登录成功，正在读取云端账号库…");
        await afterLogin();
        return;
      }
      if (result?.status === "denied") {
        throw new Error("授权被拒绝");
      }
    }
    throw new Error("授权超时，请重新登录");
  } catch (error) {
    setLoginProgress(`登录失败：${error.message}`, true);
  } finally {
    state.loginBusy = false;
    if (button) button.disabled = false;
  }
}

async function boot() {
  try {
    state.status = await api.getVaultStatus();
    paintIdentity(state.status);
    if (!state.status?.loggedIn) {
      setOverlay(true, "账号中心的数据全部来自云端，请先登录。");
      return;
    }
    setOverlay(false);
    await loadLibrary(false);
  } catch (error) {
    setOverlay(true, `读取登录状态失败：${error.message}`);
  }
}

$("cloud-login")?.addEventListener("click", startLogin);
$("btn-refresh")?.addEventListener("click", () => loadLibrary(true));
$("btn-logout")?.addEventListener("click", async () => {
  try {
    await api.vaultLogout();
  } catch { /* 本地会话即使云端失败也已清掉 */ }
  state.library = null;
  state.status = { loggedIn: false };
  paintIdentity(state.status);
  setOverlay(true, "已退出云端账号库，请重新登录。");
});
$("btn-push")?.addEventListener("click", async () => {
  if (!state.status?.loggedIn) return;
  if (!confirm("把本机旧账号库并上云端？云端会按 updatedAt 合并，不会清空云端。")) return;
  const button = $("btn-push");
  if (button) button.disabled = true;
  try {
    const result = await api.vaultPush();
    const r = result?.result || {};
    showNotice(
      `已推送 ${result.sent} 个账号：新增 ${r.created ?? 0}，更新 ${r.updated ?? 0}，跳过 ${r.skipped ?? 0}`,
    );
    await loadLibrary(true);
  } catch (error) {
    showNotice(error.status === 409
      ? "云端在你上次拉取后已变更；本次没有写入，请先点「同步云端」再推送。"
      : `推送失败：${error.message}`, "error");
  } finally {
    if (button) button.disabled = false;
  }
});
$("acc-search")?.addEventListener("input", (event) => {
  state.search = event.target.value || "";
  renderAccounts();
});

/* 自动同步：页面可见时每 60 秒拉一次；窗口重新聚焦也拉一次。
   ★ 只在已登录且页面可见时打网络；未登录/正在登录不打扰设备流。 */
async function autoSync() {
  if (document.visibilityState !== "visible") return;
  if (state.loginBusy || !state.status?.loggedIn) return;
  try {
    await loadLibrary(true);
  } catch { /* 401 已由 loadLibrary 切回登录遮罩 */ }
}
setInterval(autoSync, 60_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void autoSync();
});
window.addEventListener("focus", () => void autoSync());

boot().catch((error) => setLoginProgress(`初始化失败：${error.message}`, true));
