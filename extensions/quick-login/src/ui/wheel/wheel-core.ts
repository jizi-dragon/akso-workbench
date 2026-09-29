import type { ParallelAccount, ParallelAccountStatus } from '../../shared/types';

/**
 * 扇形轮盘渲染核心（v3.9）——独立小窗页与页面内浮层共用的几何与 DOM 构建。
 *
 * v3.9 变更：
 *  - 中心翻页（Hub 切盒）：单环只显示「当前盒子」的账号；滚轮 / 点击 Hub 循环切换盒子；
 *    外圈细进度弧指示当前盒子序位；
 *  - 扇区按账号色着色（低饱和底 + 悬停加深），不再是纯白。
 *
 * 视觉契约（两个表面的 CSS 各自实现，类名由本模块约定）：
 *   svg.sector-svg > defs(#qlBoxGrad) + path.box-track（右侧 120° 固定装饰轨道）
 *     + circle.box-node（每盒一节点）+ circle.box-node-on（当前盒高亮节点）
 *     + g.sector(.is-online)[style --acc] > path.sector-hit + text.sector-label
 *       + g.sector-num(circle+text) + circle.sector-dot(.on)
 *   + g.hub(.hub-click) > circle.hub-bg + text.hub-box / text.hub-num(.den)
 *       / text.hub-sub /（空态）rect.hub-go + text.hub-go-text
 * 交互：悬停扇区高亮（CSS :hover），点击 → onPick；滚轮 / Hub 点击 → onPage(±1)。
 */
export type WheelAccount = Pick<ParallelAccount, 'id' | 'tabName' | 'username' | 'color' | 'box'> &
  Partial<ParallelAccountStatus>;

export const WHEEL_MAX = 10;

const NS = 'http://www.w3.org/2000/svg';
const SIZE = 520;
const C = SIZE / 2;
const R_OUT = 240;
const R_IN = 118;
const R_ARC = 254;
const GAP_DEG = 2;
/** 缺省盒子名（调用方可通过参数传入自定义名） */
export const DEFAULT_BOX = '默认盒子';

function polar(r: number, deg: number): { x: number; y: number } {
  const a = ((deg - 90) * Math.PI) / 180;
  return { x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
}

function sectorPath(a0: number, a1: number): string {
  const s = a0 + GAP_DEG / 2;
  const e = a1 - GAP_DEG / 2;
  const large = e - s > 180 ? 1 : 0;
  const p1 = polar(R_OUT, s);
  const p2 = polar(R_OUT, e);
  const p3 = polar(R_IN, e);
  const p4 = polar(R_IN, s);
  return [
    `M ${p1.x} ${p1.y}`,
    `A ${R_OUT} ${R_OUT} 0 ${large} 1 ${p2.x} ${p2.y}`,
    `L ${p3.x} ${p3.y}`,
    `A ${R_IN} ${R_IN} 0 ${large} 0 ${p4.x} ${p4.y}`,
    'Z',
  ].join(' ');
}

function el(name: string, attrs: Record<string, string | number>): SVGElement {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, String(v));
  }
  return node;
}

/** 径向排布的文本（左半侧翻转 180° 保证可读） */
function radialText(cls: string, mid: number, r: number, content: string): SVGElement {
  const p = polar(r, mid);
  const flip = mid > 90 && mid < 270;
  const rot = flip ? mid + 180 : mid;
  const t = el('text', {
    class: cls,
    x: p.x,
    y: p.y,
    'text-anchor': 'middle',
    'dominant-baseline': 'central',
    transform: `rotate(${rot} ${p.x} ${p.y})`,
  });
  t.textContent = content;
  return t;
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** 按盒子分页（保持账号固有顺序；缺省归「默认盒子」，可自定义其显示名） */
export function groupPagesByBox(
  accounts: WheelAccount[],
  defaultBoxName: string = DEFAULT_BOX,
): { label: string; accounts: WheelAccount[] }[] {
  const pages: { label: string; accounts: WheelAccount[] }[] = [];
  const byName = new Map<string, WheelAccount[]>();
  for (const a of accounts) {
    const name = a.box?.trim() || defaultBoxName;
    if (!byName.has(name)) {
      byName.set(name, []);
      pages.push({ label: name, accounts: byName.get(name)! });
    }
    byName.get(name)!.push(a);
  }
  return pages;
}

export interface SectorWheelOpts {
  /** 每个盒子一页（≤ WHEEL_MAX 个账号/页由调用方裁剪） */
  pages: { label: string; accounts: WheelAccount[] }[];
  pageIndex: number;
  /** Hub 点击 / 滚轮触发（调用方负责换页并重渲染） */
  onPage: (delta: number) => void;
  onPick: (accountId: string) => void;
  /** 全部账号为空时的中心按钮（不传则仅提示文案） */
  emptyAction?: { label: string; run: () => void };
}

/**
 * 在 root 中构建「当前盒子」的扇形轮盘（root 仅承担尺寸容器，全部内容在单个 SVG 内
 * 随 viewBox 等比缩放）。构建完成后由调用方给 root 加 `.in` 触发入场。
 */
export function buildSectorWheel(root: HTMLElement, opts: SectorWheelOpts): void {
  root.innerHTML = '';
  root.classList.add('sector-wheel');

  const pages = opts.pages;
  const page = pages[opts.pageIndex];
  const accounts = page?.accounts ?? [];
  const list = accounts.slice(0, WHEEL_MAX);
  const n = list.length;
  const onlineCount = accounts.filter((a) => (a.tabIds?.length ?? 0) > 0).length;
  const overflow = accounts.length > WHEEL_MAX;
  const pageCount = pages.length;
  const pagePos = pageCount > 0 ? (opts.pageIndex % pageCount) + 1 : 0;

  const svg = el('svg', { class: 'sector-svg', viewBox: `-18 0 596 520` });

  /* ---- 右侧固定装饰轨道：120° 弧 + 盒子节点（半径/粗细/渐变全周期恒定） ---- */
  const defs = el('defs', {});
  const grad = el('linearGradient', {
    id: 'qlBoxGrad',
    gradientUnits: 'userSpaceOnUse',
    x1: '387',
    y1: '40',
    x2: '387',
    y2: '480',
  });
  grad.append(
    el('stop', { offset: '0%', 'stop-color': '#1E6FFF' }),
    el('stop', { offset: '55%', 'stop-color': '#7C5CFF' }),
    el('stop', { offset: '100%', 'stop-color': '#22C55E' }),
  );
  defs.append(grad);
  svg.append(defs);

  const arcPt = (deg: number) => ({
    x: +(C + R_ARC * Math.cos((deg * Math.PI) / 180)).toFixed(2),
    y: +(C + R_ARC * Math.sin((deg * Math.PI) / 180)).toFixed(2),
  });
  const ARC_FROM = -60;
  const ARC_TO = 60;
  const pa = arcPt(ARC_FROM);
  const pb = arcPt(ARC_TO);
  svg.append(el('path', { class: 'box-track', d: `M ${pa.x} ${pa.y} A ${R_ARC} ${R_ARC} 0 0 1 ${pb.x} ${pb.y}` }));

  const nodeAngle = (i: number) => (pageCount > 1 ? ARC_FROM + ((ARC_TO - ARC_FROM) * i) / (pageCount - 1) : 0);
  for (let i = 0; i < pageCount; i++) {
    const pt = arcPt(nodeAngle(i));
    svg.append(el('circle', { class: 'box-node', cx: pt.x, cy: pt.y, r: 5 }));
  }
  /* 高亮节点 = 当前盒子；切盒时沿轨道从上一节点平滑滑入；节点径向外侧标注盒子名 */
  const curIdx = pageCount > 0 ? ((opts.pageIndex % pageCount) + pageCount) % pageCount : 0;
  const cur = arcPt(nodeAngle(curIdx));
  const labR = R_ARC + 22;
  const lab = {
    x: +(C + labR * Math.cos((nodeAngle(curIdx) * Math.PI) / 180)).toFixed(2),
    y: +(C + labR * Math.sin((nodeAngle(curIdx) * Math.PI) / 180)).toFixed(2),
  };
  const activeG = el('g', { class: 'box-node-on-g' });
  activeG.append(el('circle', { class: 'box-node-on', cx: cur.x, cy: cur.y, r: 7 }));
  const label = el('text', {
    class: 'box-node-label',
    x: lab.x,
    y: lab.y,
    'text-anchor': 'middle',
    'dominant-baseline': 'central',
  });
  label.textContent = truncate(page?.label ?? DEFAULT_BOX, 5);
  activeG.append(label);
  svg.append(activeG);
  const prevRaw = Number(root.dataset.qlWheelLastIdx ?? NaN);
  root.dataset.qlWheelLastIdx = String(curIdx);
  if (!Number.isNaN(prevRaw) && prevRaw !== curIdx && prevRaw >= 0 && prevRaw < pageCount) {
    const prev = arcPt(nodeAngle(prevRaw));
    const mid = arcPt((nodeAngle(prevRaw) + nodeAngle(curIdx)) / 2);
    activeG.animate(
      [
        { transform: `translate(${(prev.x - cur.x).toFixed(2)}px, ${(prev.y - cur.y).toFixed(2)}px)` },
        { transform: `translate(${(mid.x - cur.x).toFixed(2)}px, ${(mid.y - cur.y).toFixed(2)}px)`, offset: 0.5 },
        { transform: 'translate(0px, 0px)' },
      ],
      { duration: 280, easing: 'cubic-bezier(0.3, 0.9, 0.35, 1)' },
    );
  }

  if (n === 0) {
    /* ---- 当前盒子无账号（或全部为空）：中心提示 ---- */
    svg.append(el('circle', { class: 'hub-bg', cx: C, cy: C, r: R_IN - 6 }));
    const allEmpty = pages.every((p) => p.accounts.length === 0);
    const cap = el('text', { class: 'hub-cap', x: C, y: C - 44, 'text-anchor': 'middle' });
    cap.textContent = allEmpty ? '暂无并行账号' : truncate(page?.label ?? DEFAULT_BOX, 10);
    const sub = el('text', { class: 'hub-sub', x: C, y: C - 14, 'text-anchor': 'middle' });
    sub.textContent = allEmpty
      ? '添加账号后，这里即可快速切换身份'
      : '该盒子暂无账号 · 滚轮或点击 Hub 切换盒子';
    svg.append(cap, sub);
    if (allEmpty && opts.emptyAction) {
      const goW = 150;
      const go = el('g', { class: 'hub-go', tabindex: '0', role: 'button' });
      go.append(el('rect', { class: 'hub-go-rect', x: C - goW / 2, y: C + 10, width: goW, height: 38, rx: 19 }));
      const label = el('text', {
        class: 'hub-go-text',
        x: C,
        y: C + 29,
        'text-anchor': 'middle',
        'dominant-baseline': 'central',
      });
      label.textContent = opts.emptyAction.label;
      go.append(label);
      go.addEventListener('click', () => opts.emptyAction!.run());
      svg.append(go);
    }
    attachPageNav(root, opts);
    root.append(svg);
    return;
  }

  /* ---- 扇区（按账号色着色） ---- */
  for (let i = 0; i < n; i++) {
    const account = list[i];
    const online = (account.tabIds?.length ?? 0) > 0;
    const a0 = (360 * i) / n;
    const a1 = (360 * (i + 1)) / n;
    const mid = (a0 + a1) / 2;

    const g = el('g', {
      class: `sector${online ? ' is-online' : ''}`,
      style: `--acc:${account.color || '#1E6FFF'};--d:${(0.06 + i * 0.05).toFixed(2)}s`,
    });
    g.append(el('path', { class: 'sector-hit', d: sectorPath(a0, a1) }));

    const name = (account.tabName || account.username || '账号').trim();
    g.append(radialText('sector-label', mid, (R_IN + R_OUT) / 2 + 2, truncate(name, 8)));

    const numAt = polar(R_IN + 24, mid);
    const flip = mid > 90 && mid < 270;
    const numRot = flip ? mid + 180 : mid;
    const numG = el('g', { class: 'sector-num', transform: `rotate(${numRot} ${numAt.x} ${numAt.y})` });
    numG.append(el('circle', { cx: numAt.x, cy: numAt.y, r: 11 }));
    const numText = el('text', { x: numAt.x, y: numAt.y, 'text-anchor': 'middle', 'dominant-baseline': 'central' });
    numText.textContent = String(i < 9 ? i + 1 : 0);
    numG.append(numText);
    g.append(numG);

    const dotAt = polar(R_OUT - 22, mid);
    g.append(el('circle', { class: `sector-dot${online ? ' on' : ''}`, cx: dotAt.x, cy: dotAt.y, r: 5.5 }));

    g.addEventListener('click', () => opts.onPick(account.id));
    svg.append(g);
  }

  /* ---- 中心 Hub：当前盒子 + 汇总 + 切盒提示（点击 = 下一盒） ---- */
  const hub = el('g', { class: 'hub hub-click' });
  hub.append(el('circle', { class: 'hub-bg', cx: C, cy: C, r: R_IN - 8 }));
  const cap = el('text', { class: 'hub-box', x: C, y: C - 46, 'text-anchor': 'middle' });
  cap.textContent = truncate(page.label, 10);
  const num = el('text', { class: 'hub-num', x: C, y: C + 6, 'text-anchor': 'middle', 'dominant-baseline': 'central' });
  num.append(document.createTextNode(String(onlineCount)));
  const den = el('tspan', { class: 'den', dx: 3 });
  den.textContent = `/ ${n} 在线`;
  num.append(den);
  const sub = el('text', { class: 'hub-sub', x: C, y: C + 40, 'text-anchor': 'middle' });
  sub.textContent = overflow
    ? `盒子内 ${accounts.length} 个 · 仅显示前 ${WHEEL_MAX} 个`
    : `盒子 ${pagePos}/${pageCount} · 滚轮或点击切盒`;
  hub.append(cap, num, sub);
  hub.addEventListener('click', () => opts.onPage(1));
  svg.append(hub);

  attachPageNav(root, opts);
  root.append(svg);
}

/** 滚轮换页（附在 root 上；重渲染时防重复附着——监听器累积会导致一次滚动翻多页） */
function attachPageNav(root: HTMLElement, opts: SectorWheelOpts): void {
  if (root.dataset.qlWheelNav === '1') {
    return;
  }
  root.dataset.qlWheelNav = '1';
  root.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      opts.onPage(e.deltaY > 0 ? 1 : -1);
    },
    { passive: false },
  );
}


