/** 知识库视图：文件管理区 + 检索测试台 + 效果度量 */

import { api } from "./api.js";
import { state, updateEngineStats } from "./app.js";

let knowledgeInitialized = false;
let knowledgeData = { documents: [], chunks: [], metrics: [] };
let selectedDocId = null;
let searchTimer = 0;

const SOURCE_LABEL = { chunk: "文档" };
const SOURCE_BADGE = { chunk: "badge-sky" };
const METHOD_LABEL = { exact: "精确", keyword: "关键词", vector: "向量" };

export function initKnowledge() {
  if (knowledgeInitialized) return;
  knowledgeInitialized = true;

  document.getElementById("btn-upload").addEventListener("click", () => document.getElementById("file-input").click());
  document.getElementById("btn-upload-files").addEventListener("click", () => document.getElementById("file-input").click());
  document.getElementById("file-input").addEventListener("change", (e) => {
    void onFiles(e.target.files);
    e.target.value = "";
  });

  document.getElementById("files-filter").addEventListener("input", () => renderFiles());

  document.getElementById("btn-reindex").addEventListener("click", async () => {
    const btn = document.getElementById("btn-reindex");
    btn.disabled = true;
    btn.textContent = "索引中…";
    try {
      const result = await api.reindex();
      showNotice(`索引重建完成：向量化 ${result.indexed} 个 · 失败 ${result.failed} 个`);
      await refreshKnowledge();
    } catch (error) {
      showNotice(`索引重建失败：${error.message}`, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "重建索引";
    }
  });

  document.getElementById("knowledge-search-input").addEventListener("input", (e) => {
    const query = e.target.value.trim();
    clearTimeout(searchTimer);
    if (!query) {
      document.getElementById("search-console")?.remove();
      return;
    }
    searchTimer = setTimeout(() => void runSearchConsole(query), 280);
  });
}

export async function refreshKnowledge() {
  knowledgeData = await api.knowledge();
  renderStats();
  renderFiles();
  if (selectedDocId) {
    if (knowledgeData.documents.some((d) => d.id === selectedDocId)) {
      await renderDocDetail(selectedDocId);
    } else {
      selectedDocId = null;
      renderPanelDefault();
    }
  }
}

function renderStats() {
  const s = knowledgeData;
  document.getElementById("stat-docs").textContent = `${s.documents.length} 文件`;
  document.getElementById("stat-chunks").textContent = `${s.chunks.length} 块`;

  const indexed = s.chunks.filter((c) => c.hasEmbedding).length;
  const total = s.chunks.length;
  const indexEl = document.getElementById("stat-index");
  indexEl.textContent = `索引 ${indexed}/${total}`;
  indexEl.className = `badge mono ${total > 0 && indexed >= total ? "badge-lime" : "badge-amber"}`;

  const rated = s.metrics.filter((m) => m.feedback !== null);
  const adopted = rated.filter((m) => m.feedback === 1).length;
  const adoptEl = document.getElementById("stat-adopt");
  if (s.metrics.length > 0) {
    adoptEl.hidden = false;
    const rate = rated.length ? Math.round((adopted / rated.length) * 100) : null;
    adoptEl.textContent = `采纳率 ${rate === null ? "—" : `${rate}%`}`;
    adoptEl.title = `近 ${s.metrics.length} 次问答`;
  } else {
    adoptEl.hidden = true;
  }
  updateEngineStats();
}

// ———— 文件管理区 ————

function renderFiles() {
  const filter = document.getElementById("files-filter").value.trim().toLowerCase();
  const list = document.getElementById("files-list");
  list.innerHTML = "";
  const docs = knowledgeData.documents.filter((d) => !filter || d.name.toLowerCase().includes(filter));

  if (!docs.length) {
    const li = document.createElement("li");
    li.className = "files-empty";
    li.textContent = "暂无文件";
    list.appendChild(li);
    return;
  }

  for (const doc of docs) {
    const li = document.createElement("li");
    li.className = doc.id === selectedDocId ? "active" : "";

    const item = document.createElement("button");
    item.className = "file-item";
    item.title = doc.name;
    item.innerHTML = `<span>📄</span><span class="file-name"></span><span class="file-chunks mono">${doc.chunkCount}</span>`;
    item.querySelector(".file-name").textContent = doc.name;
    item.addEventListener("click", () => {
      selectedDocId = doc.id;
      renderFiles();
      void renderDocDetail(doc.id);
    });

    const del = document.createElement("button");
    del.className = "file-delete";
    del.title = "删除文件";
    del.textContent = "🗑";
    del.addEventListener("click", async () => {
      await api.deleteDocument(doc.id);
      if (selectedDocId === doc.id) { selectedDocId = null; renderPanelDefault(); }
      await refreshKnowledge();
    });

    li.appendChild(item);
    li.appendChild(del);
    list.appendChild(li);
  }
}

async function renderDocDetail(docId) {
  const panel = document.getElementById("knowledge-panel");
  panel.innerHTML = `<div class="document-detail"><p class="doc-hint">加载中…</p></div>`;
  let doc;
  try {
    doc = await api.getDocument(docId);
  } catch {
    panel.innerHTML = `<div class="document-detail"><p class="doc-hint">文档加载失败。</p></div>`;
    return;
  }
  const origin = doc.originUrl
    ? `<span class="badge badge-teal"><a href="${escapeAttr(doc.originUrl)}" target="_blank" rel="noopener">钉钉原文</a></span>`
    : "";
  const synced = doc.syncedAt
    ? `<span class="badge mono">同步于 ${new Date(doc.syncedAt).toLocaleDateString("zh-CN")}</span>`
    : "";
  panel.innerHTML = `
    <div class="document-detail">
      <div class="node-detail-title">
        <h2 title="${escapeAttr(doc.name)}">${escapeText(doc.name)}</h2>
        <button class="node-delete" id="doc-close" title="关闭预览">✕</button>
      </div>
      <div class="node-badges">
        <span class="badge badge-sky mono">${doc.content.length} 字</span>
        ${synced}${origin}
      </div>
      <div class="document-preview"></div>
      <button class="btn-ghost doc-delete-btn" id="doc-delete">🗑 删除此文件（含文本块）</button>
    </div>`;
  panel.querySelector(".document-preview").textContent = doc.content;
  panel.querySelector("#doc-close").addEventListener("click", () => {
    selectedDocId = null;
    renderFiles();
    renderPanelDefault();
  });
  panel.querySelector("#doc-delete").addEventListener("click", async () => {
    await api.deleteDocument(doc.id);
    selectedDocId = null;
    renderPanelDefault();
    await refreshKnowledge();
  });
}

function renderPanelDefault() {
  document.getElementById("knowledge-panel").innerHTML = `
    <div class="document-list">
      <h2>知识库概览</h2>
      <p class="doc-hint">左侧选择文件查看内容；顶部搜索框体验检索。</p>
    </div>`;
}

// ———— 检索测试台 ————

async function runSearchConsole(query) {
  let consoleEl = document.getElementById("search-console");
  if (!consoleEl) {
    consoleEl = document.createElement("div");
    consoleEl.className = "search-console";
    consoleEl.id = "search-console";
    consoleEl.innerHTML = '<div class="search-console-title mono">检索测试台</div>';
    document.getElementById("knowledge-graph").appendChild(consoleEl);
  }
  consoleEl.querySelector(".search-console-title").innerHTML = `检索测试台 · <span class="spin">◌</span> 检索中…`;

  try {
    const result = await api.search(query);
    const hitRate = knowledgeData.metrics.length
      ? Math.round((knowledgeData.metrics.filter((m) => m.hit !== "none").length / knowledgeData.metrics.length) * 100)
      : null;
    consoleEl.innerHTML = `
      <div class="search-console-title mono">检索测试台 · ${result.hits.length} 条命中 · ${result.elapsedMs}ms${hitRate !== null ? ` · 历史命中率 ${hitRate}%` : ""}</div>
      ${
        result.hits.length === 0
          ? '<div class="search-console-empty">无命中——试试函数名/参数名等精确词，或更短的关键词</div>'
          : `<div class="search-console-list">${result.hits
              .map(
                (hit) => `
                <div class="search-hit">
                  <div class="search-hit-head">
                    <span class="badge ${SOURCE_BADGE[hit.sourceType] || ""}">${SOURCE_LABEL[hit.sourceType] || hit.sourceType}</span>
                    <span class="badge">${METHOD_LABEL[hit.method] || hit.method}</span>
                    <span class="search-hit-score mono">${hit.score.toFixed(2)}</span>
                  </div>
                  <div class="search-hit-title">${escapeText(hit.title)}</div>
                  <div class="search-hit-snippet">${escapeText(hit.content.slice(0, 130))}${hit.content.length > 130 ? "…" : ""}</div>
                  <div class="search-hit-ref mono">${escapeText(hit.ref)}${hit.updatedAt ? ` · 更新于 ${new Date(hit.updatedAt).toLocaleDateString("zh-CN")}` : ""}</div>
                </div>`
              )
              .join("")}</div>`
      }`;
  } catch (error) {
    consoleEl.innerHTML = `<div class="search-console-title mono">检索测试台</div><div class="search-console-empty">检索失败：${error.message}</div>`;
  }
}

// ———— 上传 ————

async function onFiles(files) {
  if (!files || !files.length) return;
  let count = 0;
  for (const file of Array.from(files)) {
    const content = await file.text();
    if (!content.trim()) continue;
    await api.uploadDocument(file.name, content);
    count += 1;
  }
  showNotice(`已收录 ${count} 个文件（切分 + 向量化）`);
  selectedDocId = null;
  renderPanelDefault();
  await refreshKnowledge();
}

function showNotice(text, warn = false) {
  const slot = document.getElementById("knowledge-notice-slot");
  slot.innerHTML = `<div class="knowledge-notice ${warn ? "warn" : ""}">${warn ? "⚠" : "✓"} ${escapeText(text)}</div>`;
  setTimeout(() => { slot.innerHTML = ""; }, 6000);
}

function escapeText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(text) {
  return escapeText(text).replace(/"/g, "&quot;");
}
