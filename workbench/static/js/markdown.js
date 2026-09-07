/**
 * 轻量 Markdown 渲染（流式安全）：
 * 先整体 HTML 转义，再按行解析 Markdown 子集，
 * 合规引用【依据：…】【知识库：…】【来源：…】转为高亮徽章。
 */

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/【依据：([^】]+)】/g, '<span class="cite-chip cite-sky">依据：$1</span>');
  out = out.replace(/【知识库：([^】]+)】/g, '<span class="cite-chip cite-teal">知识库：$1</span>');
  out = out.replace(/【来源：([^】]+)】/g, '<span class="cite-chip cite-teal">来源：$1</span>');
  out = out.replace(/【待核实：([^】]+)】/g, '<span class="cite-chip cite-sky">待核实：$1</span>');
  return out;
}

export function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const html = [];
  let i = 0;
  let inCode = false;
  let codeBuf = [];
  let listType = null;
  let paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) {
      html.push(`<p>${inline(paraBuf.join("<br/>"))}</p>`);
      paraBuf = [];
    }
  };
  const flushList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块
    if (/^```/.test(line.trim())) {
      if (inCode) {
        html.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushPara();
        flushList();
        inCode = true;
      }
      i += 1;
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      i += 1;
      continue;
    }

    // 表格（| a | b | 形式，含分隔行）
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flushPara();
      flushList();
      const header = line.split("|").slice(1, -1).map((c) => c.trim());
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].split("|").slice(1, -1).map((c) => c.trim()));
        i += 1;
      }
      html.push("<table><thead><tr>");
      for (const cell of header) html.push(`<th>${inline(cell)}</th>`);
      html.push("</tr></thead><tbody>");
      for (const row of rows) {
        html.push("<tr>");
        for (const cell of row) html.push(`<td>${inline(cell)}</td>`);
        html.push("</tr>");
      }
      html.push("</tbody></table>");
      continue;
    }

    // 标题
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    // 分隔线
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      flushPara();
      flushList();
      html.push("<hr/>");
      i += 1;
      continue;
    }

    // 引用
    if (/^&gt;\s?/.test(line) || /^>\s?/.test(line)) {
      flushPara();
      flushList();
      const quote = [line.replace(/^(&gt;|>)\s?/, "")];
      i += 1;
      while (i < lines.length && (/^&gt;\s?/.test(lines[i]) || /^>\s?/.test(lines[i]))) {
        quote.push(lines[i].replace(/^(&gt;|>)\s?/, ""));
        i += 1;
      }
      html.push(`<blockquote><p>${inline(quote.join("<br/>"))}</p></blockquote>`);
      continue;
    }

    // 列表
    const ulMatch = line.match(/^\s*[-*]\s+(.*)$/);
    const olMatch = line.match(/^\s*\d+[.、]\s+(.*)$/);
    if (ulMatch || olMatch) {
      flushPara();
      const want = ulMatch ? "ul" : "ol";
      if (listType !== want) {
        flushList();
        html.push(`<${want}>`);
        listType = want;
      }
      html.push(`<li>${inline((ulMatch || olMatch)[1])}</li>`);
      i += 1;
      continue;
    }

    if (!line.trim()) {
      flushPara();
      flushList();
      i += 1;
      continue;
    }

    paraBuf.push(line);
    i += 1;
  }

  if (inCode && codeBuf.length) html.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
  flushPara();
  flushList();
  return html.join("");
}
