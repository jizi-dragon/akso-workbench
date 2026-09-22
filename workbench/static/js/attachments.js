/** 附件条：把用户选的图片/文件收在输入框上方，发送时一起带走。
 *
 *  ## 三条入口都要通（2026-09-22 用户下达「能够让用户正常的输入」）
 *
 *  | 入口 | 为什么必须有 |
 *  |---|---|
 *  | 📎 按钮 | 唯一**可发现**的入口；没有它，用户不会知道能传文件 |
 *  | **粘贴**（Ctrl+V） | 截图之后最自然的动作就是直接粘进输入框 |
 *  | **拖拽** | 文件在资源管理器里已经选好了，拖进来比"点按钮再翻目录"快 |
 *
 *  ## 为什么上传在"选好"时就开始，而不是等发送
 *
 *  大文件（几十 MB 的 Excel）解析要时间。等用户按下发送再上传，会让他对着一个
 *  不动的界面等；而**先传先解析**，发送时只剩"把 id 带上"这一件事，
 *  并且失败能在发送之前就说清楚（"这个 pdf 是扫描件、读不出文字"）。
 *
 *  ## 与后端的分工
 *
 *  前端只做"选 → 传 → 显示状态 → 带 id"；类型/大小/魔数校验**全部在服务端**
 *  （`routes_attachments` + `services.parsing`）——前端校验只是省一次往返，不是边界。
 */

import { api } from "./api.js";

const MAX_FILES = 5;

/** 人类可读的大小 */
function humanSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

/** 解析结果 → 一句人话（**失败与"读不出字"都要看得见**） */
function statusText(att) {
  if (att.kind === "image") return "图片 · 直接给模型看";
  if (att.status === "parsed") return `已解析 ${att.textChars} 字`;
  const note = String(att.parseNote || "");
  if (note.includes("扫描件")) return "⚠ 扫描件：读不出文字";
  if (note) return `⚠ ${note.slice(0, 40)}`;
  return "已收（无可读文本）";
}

export function createAttachTray({ strip, input, button, getConversationId, onError }) {
  /** @type {Array<{id:string,name:string,kind:string,bytes:number,status:string,textChars:number,parseNote:string}>} */
  let items = [];
  let uploading = 0;

  const render = () => {
    strip.replaceChildren();
    strip.hidden = items.length === 0 && uploading === 0;
    for (const att of items) {
      const chip = document.createElement("div");
      chip.className = "attach-chip";
      chip.dataset.kind = att.kind || "";
      if (att.kind === "image") {
        const thumb = document.createElement("img");
        thumb.className = "attach-thumb";
        thumb.src = api.attachmentRawUrl(att.id);
        thumb.alt = att.name;
        chip.appendChild(thumb);
      } else {
        chip.appendChild(Object.assign(document.createElement("span"),
          { className: "attach-glyph", textContent: "📄" }));
      }
      const main = document.createElement("div");
      main.className = "attach-main";
      main.appendChild(Object.assign(document.createElement("span"),
        { className: "attach-name", textContent: att.name }));
      main.appendChild(Object.assign(document.createElement("span"), {
        className: "attach-meta",
        textContent: `${humanSize(att.bytes)} · ${statusText(att)}`,
      }));
      chip.appendChild(main);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "attach-remove";
      remove.title = "移除这个附件";
      remove.textContent = "✕";
      remove.addEventListener("click", () => {
        items = items.filter((x) => x.id !== att.id);
        render();
        // 后端也删掉：留着只会占磁盘，而用户已经明确表示不要它了
        void api.deleteAttachment(att.id).catch(() => {});
      });
      chip.appendChild(remove);
      strip.appendChild(chip);
    }
    if (uploading > 0) {
      const chip = document.createElement("div");
      chip.className = "attach-chip is-uploading";
      chip.appendChild(Object.assign(document.createElement("span"),
        { className: "attach-glyph", textContent: "◌" }));
      chip.appendChild(Object.assign(document.createElement("span"),
        { className: "attach-name", textContent: `正在上传解析 ${uploading} 个…` }));
      strip.appendChild(chip);
    }
  };

  const addFiles = async (fileList) => {
    const files = [...(fileList || [])].filter((f) => f && f.name);
    if (!files.length) return;
    for (const file of files) {
      if (items.length + uploading >= MAX_FILES) {
        onError?.(`一次最多带 ${MAX_FILES} 个附件（已经够了）`);
        break;
      }
      uploading += 1;
      render();
      try {
        const payload = await api.uploadAttachment(file, getConversationId?.() || "");
        items.push(payload.attachment);
      } catch (error) {
        onError?.(`${file.name}：${error.message}`);
      } finally {
        uploading -= 1;
        render();
      }
    }
  };

  button?.addEventListener("click", () => input?.click());
  input?.addEventListener("change", () => {
    void addFiles(input.files);
    input.value = ""; // 同一个文件连选两次也要能触发 change
  });

  render();

  return {
    addFiles,
    /** 发送时带走的 id（顺序 = 用户添加顺序） */
    ids: () => items.map((a) => a.id),
    isEmpty: () => items.length === 0,
    /** 发送成功后清空（附件已经绑到那一条消息上，输入框不该继续挂着它们） */
    clear: () => {
      items = [];
      render();
    },
  };
}
