/* 产物就地预览：markdown 渲染 / drawio 画成 SVG / json 与纯文本。
 *
 * 设计约束（都与本项目的运行形态有关，别按"网页"的默认假设改）：
 * 1. **完全离线自足**：不引第三方库、不连 CDN。产物是给用户看自己平台配置的，
 *    预览不该依赖外网是否可达（PITFALLS #9：这台机器的出网通道会翻转）。
 * 2. **不渲染外来 HTML**：markdown 走"先转义、再按白名单子集拼装"，链接只放行
 *    http(s)。产物文本里混着平台来的对象名/字段名，直接当 HTML 插进 DOM 就是注入面。
 * 3. **drawio 自己画**：官方 viewer 是联网服务，打包态用不了。我们只处理**本仓
 *    `drawio.py` 生成的子集**（顶点 + 正交边 + 多页），够用且可控；解析失败就退回源码。
 *
 * 供 insight 页调用：`previewArtifact({ jobId, name, size })`。
 */

import { el, fmtBytes } from './modules-common.js';

const MAX_PREVIEW_BYTES = 2_000_000;

/* ————————————————————————————— 通用取文 ————————————————————————————— */

async function fetchText(jobId, name) {
  const resp = await fetch(`/api/insight/artifacts/${jobId}/file?name=${encodeURIComponent(name)}`);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${resp.status}`);
  }
  return resp.text();
}

function fileUrl(jobId, name, download = false) {
  return `/api/insight/artifacts/${jobId}/file?name=${encodeURIComponent(name)}${download ? '&download=1' : ''}`;
}

/* ————————————————————————————— markdown ————————————————————————————— */

function esc(text) {
  return String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** 行内标记：先转义再套白名单子集，顺序不能反（否则等于放行原始 HTML）。 */
function inlineMd(text) {
  let out = esc(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return out;
}

function splitRow(line) {
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
}

function isTableSeparator(line) {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-');
}

/** 受控 markdown → HTML：标题/表格/列表/引用/围栏代码/分隔线/段落。 */
export function renderMarkdown(source) {
  const lines = String(source).replace(/\r\n?/g, '\n').split('\n');
  const html = [];
  let i = 0;
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMd(paragraph.join(' '))}</p>`);
    paragraph = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {                       // 围栏代码
      flushParagraph();
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i += 1; }
      i += 1;
      html.push(`<pre class="apv-code"><code>${esc(body.join('\n'))}</code></pre>`);
      continue;
    }

    if (/^\s*$/.test(line)) { flushParagraph(); i += 1; continue; }

    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {        // 分隔线
      flushParagraph();
      html.push('<hr />');
      i += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      const level = Math.min(heading[1].length + 1, 6);   // h1 留给弹窗标题，正文从 h2 起
      html.push(`<h${level}>${inlineMd(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushParagraph();
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      const thead = `<thead><tr>${head.map((c) => `<th>${inlineMd(c)}</th>`).join('')}</tr></thead>`;
      const tbody = `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inlineMd(c)}</td>`).join('')}</tr>`).join('')}</tbody>`;
      html.push(`<table class="apv-table">${thead}${tbody}</table>`);
      continue;
    }

    if (/^\s*>\s?/.test(line)) {                      // 引用块
      flushParagraph();
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      html.push(`<blockquote>${inlineMd(body.join(' '))}</blockquote>`);
      continue;
    }

    const bullet = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      const ordered = /\d/.test(bullet[2]);
      const items = [];
      let indent = bullet[1].length;
      while (i < lines.length) {
        const m = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(lines[i]);
        if (!m) {
          // 续行（缩进对齐的后续文本）并入上一项
          if (items.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*$/.test(lines[i])) {
            items[items.length - 1] += ' ' + lines[i].trim();
            i += 1;
            continue;
          }
          break;
        }
        indent = Math.min(indent, m[1].length);
        items.push(m[3]);
        i += 1;
      }
      const tag = ordered ? 'ol' : 'ul';
      html.push(`<${tag}>${items.map((t) => `<li>${inlineMd(t)}</li>`).join('')}</${tag}>`);
      continue;
    }

    paragraph.push(line.trim());
    i += 1;
  }
  flushParagraph();
  return html.join('\n');
}

/* ————————————————————————————— drawio → SVG ————————————————————————————— */

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function styleValue(style, key, fallback) {
  const m = new RegExp(`${key}=([^;]+)`).exec(style || '');
  return m ? m[1] : fallback;
}

function shapeFor(style, geom) {
  const stroke = styleValue(style, 'strokeColor', '#6c8ebf');
  const fill = styleValue(style, 'fillColor', '#ffffff');
  const dashed = styleValue(style, 'dashed', '0') === '1';
  const common = {
    class: 'apv-node', fill, stroke, 'stroke-width': 1.2,
    ...(dashed ? { 'stroke-dasharray': '5 4' } : {}),
  };
  const { x, y, w, h } = geom;
  if (style.includes('ellipse')) {
    return svgEl('ellipse', { cx: x + w / 2, cy: y + h / 2, rx: w / 2, ry: h / 2, ...common });
  }
  if (style.includes('rhombus')) {
    return svgEl('polygon', {
      points: `${x + w / 2},${y} ${x + w},${y + h / 2} ${x + w / 2},${y + h} ${x},${y + h / 2}`, ...common,
    });
  }
  return svgEl('rect', {
    x, y, width: w, height: h, rx: style.includes('rounded=1') ? 8 : 2, ...common,
  });
}

function labelText(lines, cx, cy, { size = 12, weight = '' } = {}) {
  const text = svgEl('text', {
    x: cx, y: cy, 'text-anchor': 'middle', 'font-size': size,
    'font-family': 'system-ui, "Microsoft YaHei", sans-serif', fill: '#1f2937',
    ...(weight ? { 'font-weight': weight } : {}),
    style: 'paint-order:stroke;stroke:#ffffff;stroke-width:3px;stroke-linejoin:round;',
  });
  const real = lines.filter((l) => String(l).trim());
  const total = real.length;
  real.forEach((line, index) => {
    const tspan = svgEl('tspan', {
      x: cx, y: cy + (index - (total - 1) / 2) * (size + 3) + size / 3,
    });
    tspan.textContent = line;
    text.appendChild(tspan);
  });
  return text;
}

function edgePath(a, b) {
  const ac = { x: a.x + a.w / 2, y: a.y + a.h / 2 };
  const bc = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  if (a.id === b.id) {                                   // 自环
    const x = a.x + a.w;
    const y = a.y + a.h / 2;
    return `M ${x} ${y} C ${x + 40} ${y - 30} ${x + 40} ${y + 30} ${x} ${y + 16}`;
  }
  const dx = bc.x - ac.x;
  const dy = bc.y - ac.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const rightward = dx >= 0;
    const sx = rightward ? a.x + a.w : a.x;
    const ex = rightward ? b.x : b.x + b.w;
    const mx = (sx + ex) / 2;
    return `M ${sx} ${ac.y} L ${mx} ${ac.y} L ${mx} ${bc.y} L ${ex} ${bc.y}`;
  }
  const downward = dy >= 0;
  const sy = downward ? a.y + a.h : a.y;
  const ey = downward ? b.y : b.y + b.h;
  const my = (sy + ey) / 2;
  return `M ${ac.x} ${sy} L ${ac.x} ${my} L ${bc.x} ${my} L ${bc.x} ${ey}`;
}

/** 单页 mxGraphModel → <svg>（本仓 drawio.py 生成的子集）。 */
function renderPage(model) {
  const cells = [...model.querySelectorAll('root > mxCell')];
  const vertices = [];
  const edges = [];
  for (const cell of cells) {
    const style = cell.getAttribute('style') || '';
    const value = (cell.getAttribute('value') || '').replace(/&#xa;/g, '\n');
    if (cell.getAttribute('vertex') === '1') {
      const g = cell.querySelector('mxGeometry');
      if (!g) continue;
      vertices.push({
        id: cell.getAttribute('id') || '',
        x: Number(g.getAttribute('x') || 0), y: Number(g.getAttribute('y') || 0),
        w: Number(g.getAttribute('width') || 120), h: Number(g.getAttribute('height') || 40),
        style, label: value,
      });
    } else if (cell.getAttribute('edge') === '1') {
      edges.push({
        source: cell.getAttribute('source') || '',
        target: cell.getAttribute('target') || '',
        style, label: value,
      });
    }
  }

  const pad = 24;
  let minX = 0;
  let minY = 0;
  let maxX = 480;
  let maxY = 200;
  if (vertices.length) {
    minX = Math.min(...vertices.map((v) => v.x));
    minY = Math.min(...vertices.map((v) => v.y));
    maxX = Math.max(...vertices.map((v) => v.x + v.w));
    maxY = Math.max(...vertices.map((v) => v.y + v.h));
  }
  const svg = svgEl('svg', {
    viewBox: `${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${maxY - minY + pad * 2}`,
    width: '100%', height: '100%', preserveAspectRatio: 'xMidYMid meet',
  });
  svg.appendChild(svgEl('rect', {
    class: 'apv-canvas-bg',
    x: minX - pad, y: minY - pad, width: maxX - minX + pad * 2, height: maxY - minY + pad * 2,
    fill: '#ffffff',
  }));
  const defs = svgEl('defs');
  const marker = svgEl('marker', {
    id: 'apv-arrow', viewBox: '0 0 10 10', refX: '9', refY: '5',
    markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse',
  });
  marker.appendChild(svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#6c8ebf' }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  const byId = new Map(vertices.map((v) => [v.id, v]));
  for (const edge of edges) {
    const a = byId.get(edge.source);
    const b = byId.get(edge.target);
    if (!a || !b) continue;                              // 端点不在本页：不画悬挂边
    svg.appendChild(svgEl('path', {
      class: 'apv-edge',
      d: edgePath(a, b), fill: 'none', stroke: '#6c8ebf', 'stroke-width': 1.3,
      'marker-end': 'url(#apv-arrow)',
      ...(edge.style.includes('dashed=1') ? { 'stroke-dasharray': '5 4' } : {}),
    }));
    if (edge.label) {
      const lx = (a.x + a.w / 2 + b.x + b.w / 2) / 2;
      const ly = (a.y + a.h / 2 + b.y + b.h / 2) / 2;
      svg.appendChild(labelText([edge.label], lx, ly, { size: 11 }));
    }
  }
  for (const vertex of vertices) {
    const shape = shapeFor(vertex.style, vertex);
    shape.dataset.cellId = vertex.id;
    svg.appendChild(shape);
    if (vertex.label) {
      svg.appendChild(labelText(vertex.label.split('\n'),
        vertex.x + vertex.w / 2, vertex.y + vertex.h / 2));
    }
  }
  return { svg, vertices: vertices.length, edges: edges.length };
}

/** .drawio 文本 → {pages:[{name, svg, vertices, edges}]} */
export function renderDrawio(xmlText) {
  const doc = new DOMParser().parseFromString(xmlText, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('不是良构 XML（drawio 解析失败）');
  const diagrams = [...doc.querySelectorAll('diagram')];
  if (!diagrams.length) throw new Error('mxfile 里没有 diagram 页');
  return diagrams.map((diagram, index) => {
    const model = diagram.querySelector('mxGraphModel');
    if (!model) throw new Error(`第 ${index + 1} 页缺少 mxGraphModel`);
    return { name: diagram.getAttribute('name') || `页 ${index + 1}`, ...renderPage(model) };
  });
}

/* ————————————————————————————— 弹窗装配 ————————————————————————————— */

let current = { jobId: '', name: '', text: '', raw: false, pages: null };

function close() {
  const dlg = el('artifact-preview');
  if (!dlg || !dlg.open) return;
  dlg.classList.add('closing');
  setTimeout(() => { dlg.classList.remove('closing'); dlg.close(); }, 160);
}

function bodyEl() { return el('apv-body'); }

function renderRaw() {
  const box = bodyEl();
  box.innerHTML = '';
  const pre = document.createElement('pre');
  pre.className = 'apv-source';
  pre.textContent = current.text;
  box.appendChild(pre);
}

function renderMarkdownInto() {
  const box = bodyEl();
  box.innerHTML = `<div class="apv-md">${renderMarkdown(current.text)}</div>`;
}

function renderDrawioInto() {
  const box = bodyEl();
  box.innerHTML = '';
  if (!current.pages) { renderRaw(); return; }
  const tabs = el('apv-tabs');
  tabs.innerHTML = '';
  box.innerHTML = '';
  const stage = document.createElement('div');
  stage.className = 'apv-stage';
  const canvases = current.pages.map((page) => {
    const wrap = document.createElement('div');
    wrap.className = 'apv-canvas';
    wrap.appendChild(page.svg);
    stage.appendChild(wrap);
    return wrap;
  });
  if (current.pages.length > 1) {
    current.pages.forEach((page, index) => {
      const btn = document.createElement('button');
      btn.className = `apv-tab${index === 0 ? ' active' : ''}`;
      btn.textContent = page.name;
      btn.onclick = () => {
        [...tabs.children].forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        canvases.forEach((c, k) => { c.hidden = k !== index; });
      };
      tabs.appendChild(btn);
    });
  }
  canvases.forEach((c, k) => { c.hidden = k !== 0; });
  box.appendChild(stage);
}

function renderJsonInto() {
  const box = bodyEl();
  let pretty = current.text;
  try { pretty = JSON.stringify(JSON.parse(current.text), null, 2); } catch { /* 原样展示 */ }
  box.innerHTML = '';
  const pre = document.createElement('pre');
  pre.className = 'apv-source';
  pre.textContent = pretty;
  box.appendChild(pre);
}

function paint() {
  const suffix = current.name.slice(current.name.lastIndexOf('.')).toLowerCase();
  el('apv-raw').hidden = !['.md', '.drawio'].includes(suffix);
  el('apv-raw').textContent = current.raw ? '渲染' : '原始';
  el('apv-tabs').innerHTML = '';
  if (current.raw) { renderRaw(); return; }
  if (suffix === '.md') renderMarkdownInto();
  else if (suffix === '.drawio') renderDrawioInto();
  else if (suffix === '.json') renderJsonInto();
  else renderRaw();
}

/** 打开产物预览。`file` = { name, size }（来自 /api/insight/artifacts/{job_id}）。 */
export async function previewArtifact({ jobId, name, size }) {
  const dlg = el('artifact-preview');
  if (!dlg) return;
  current = { jobId, name, text: '', raw: false, pages: null };
  el('apv-title').textContent = name;
  el('apv-meta').textContent = size == null ? '' : fmtBytes(size);
  el('apv-download').href = fileUrl(jobId, name, true);
  el('apv-download').setAttribute('download', name);
  bodyEl().innerHTML = '<div class="empty">读取中…</div>';
  if (!dlg.open) dlg.showModal();

  try {
    current.text = await fetchText(jobId, name);
  } catch (e) {
    bodyEl().innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`;
    return;
  }
  if (current.text.length > MAX_PREVIEW_BYTES) {
    current.text = `${current.text.slice(0, MAX_PREVIEW_BYTES)}\n…（超长截断，完整内容请下载）`;
  }
  const suffix = name.slice(name.lastIndexOf('.')).toLowerCase();
  if (suffix === '.drawio') {
    try { current.pages = renderDrawio(current.text); }
    catch (e) {
      current.pages = null;
      el('apv-meta').textContent = `${fmtBytes(size)} · 图形渲染失败（${e.message}），已显示源码`;
      current.raw = true;
    }
  }
  paint();
}

export function bindPreview() {
  el('apv-close').onclick = close;
  el('apv-raw').onclick = () => { current.raw = !current.raw; paint(); };
  el('artifact-preview').addEventListener('click', (event) => {
    if (event.target === el('artifact-preview')) close();   // 点遮罩关闭
  });
}
