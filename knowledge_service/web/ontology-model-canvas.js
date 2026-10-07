/* ============================================================================
 * 本体建模层 · 画布引擎（ontology-model-canvas.js）—— 实现者 A
 *
 * P1（2026-10-04）：把结构层三页合成的「本体建模层」的中间画布交给我。
 * 契约：docs/2026-10-04-P1前端实现契约.md §2.4 ——
 *   window.OntologyCanvas = { mount(host, ctx), render(state), setView(mode),
 *                             focus(iri), destroy() }
 *
 * 数据从哪来：只读消费主控 window.OntologyModel 的 S（state.graph / state.selected /
 * state.view）。节点 = 类，边 = 继承（实线）/ 关系（紫虚线）/ 待收下（黄虚线）。
 * 选中、发命令的唯一入口也是主控：ctx.select(iri)（选中）、ctx.sendCommand(cmd)（编辑）。
 * 本文件不自己算门禁、不自己拼术语 IRI、不自己读后端 —— 口径只有一处出处（契约 §4）。
 *
 * 为什么用 Cytoscape 而不是手写 SVG（ontology-design.js 那套）：
 *   手写 SVG 要自己管理坐标/命中测试/拖拽/缩放；Cytoscape 自带力导向布局、节点拖拽、
 *   以光标为锚点的滚轮缩放、拖空白平移、双击适应 —— 这些是「可交互画布」的硬需求，
 *   交给成熟的图库比再写一遍更稳。分层视图里的「按继承深度分层带」算法仍照搬
 *   ontology-design.js（最长路径松弛 + 层带），保证两页口径一致（同一个类在同一层）。
 *
 * 功能一句话说明（做什么 / 不做什么）—— 用户核心诉求，见每个按钮的 title：
 *   · 图谱视图：力导向铺开全部关系，用来看整体 —— 不保证层级位置固定。
 *   · 分层视图：按继承深度分层带，看清谁在第几层 —— 关系线只作参考，不参与分层。
 *   · 点节点＝选中（唯一入口 ctx.select）—— 不在这里改数据。
 *   · 拖节点＝自由挪位（纯视觉）—— 不写回后端。
 *   · 改父类/连关系＝两步点击 + 确认（无橡皮筋）—— 确认后才发命令，写进草案待审核。
 *
 * 硬约束：不用任何内联事件字符串（全部 addEventListener）；注释解释「为什么」。
 * ========================================================================== */
(() => {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';

  // 分层带布局常量：搬 ontology-design.js 的口径（卡片尺寸/间距/每带张数只有一处出处）。
  const NODE_W = 168, NODE_H = 42, GAP_X = 28, GAP_Y = 70, MAX_PER_ROW = 4;
  const MIN_ZOOM = 0.25, MAX_ZOOM = 3;
  // 层带相对卡片区左右各外扩这么多；fit 时要连它一起算，否则"层级树"会偏（见 fitBox）。
  const BAND_PAD = 14, BAND_FIT_PAD = 24;

  // 工具条按钮的说明文案（集中一处：锁定态要能**换回**原文案，而不是越改越乱）
  const BAR_TITLES = {
    create: '做什么：新建实体类（也可以切到关系类型 / 实体属性）。不做什么：不直接改生效本体——先写进草案，审核发布后才生效。',
    parent: '做什么：把当前选中的类连到另一个类，作为它的父类（写入草案）。不做什么：不改名字/说明，不删除已有父类。',
    relation: '做什么：先点起点、再点终点，建一个新的关系类型（写入草案）。不做什么：不改已有关系，不做拖拽橡皮筋。',
    locked: '正在查看历史版本（只读）：编辑入口先锁住。要改就先点顶栏「回到最新」，或用「版本管理」里的「回到这一版」开一份新草案。',
  };

  // 浅青（teal）语义色：与整站 theme.css 同一套（白纸面 + 深青/靛蓝/橄榄黄）。
  // 节点默认面 = 白；青=类/继承，靛蓝=关系，teal=属性，橄榄黄=待收下，深青=选中发光。
  const C = {
    bg: '#f3f7f8', node: '#ffffff', cyan: '#0e7c7b', purple: '#6366f1',
    green: '#0d9488', teal: '#14b8a6', amber: '#ca8a04', ink: '#124242',
  };

  // 模块级状态（不用 this，避免方法被解构后丢上下文）。
  const M = {
    ready: false, host: null, ctx: null, cy: null, stage: null, svg: null, ro: null,
    onResize: null, view: 'graph', sig: '', graph: null, selected: null,
    mode: '', linkFrom: null, pending: null, bands: [], els: {}, didFit: false,
  };

  // ---- 小工具 --------------------------------------------------------------
  const CY = () => (typeof window !== 'undefined' ? window.cytoscape : undefined);

  function mkEl(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  function mkButton(text, handler, cls, title) {
    const b = mkEl('button', cls, text);
    b.type = 'button';
    if (title) b.title = title;
    b.addEventListener('click', handler);
    return b;
  }

  function svgEl(tag) { return document.createElementNS(NS, tag); }

  function shortIri(iri) {
    const h = M.ctx && M.ctx.helpers;
    if (h && typeof h.shortIri === 'function') return h.shortIri(iri);
    const s = String(iri || '');
    return s.length > 20 ? s.slice(0, 11) + '…' + s.slice(-6) : s;
  }

  // 取节点显示名：优先图上标签，回落到 helpers.shortIri（不自己造取名规则）。
  function labelOf(iri) {
    const g = M.graph;
    if (g && g.nodes) {
      const n = g.nodes.find(x => x.id === iri);
      if (n && n.label) return n.label;
    }
    return shortIri(iri);
  }

  function status(msg, err) {
    if (M.ctx && typeof M.ctx.status === 'function') M.ctx.status(msg, err);
    else if (typeof console !== 'undefined') console.log('[canvas]', msg);
  }

  // ---- Cytoscape 样式（浅青 teal + 白纸卡片 + 柔和投影）---------------------
  // 设计：节点是一张张「浮在浅青网格上的白纸卡」，靠投影而非粗边框表达层次；
  //   类型只染 1.5px 描边与一条隐形的「类型色」（类=青/关系=靛蓝/属性=teal）。
  //   选中用青色辉光（shadow）而不是再压一层底色，保持纸卡质感。
  // 顺序即优先级：后面的规则覆盖前面，「选中」必须排在 kind/pending 之后。
  function cyStyle() {
    return [
      { selector: 'node', style: {
        'width': NODE_W, 'height': NODE_H, 'shape': 'round-rectangle',
        // 白纸卡：顶光微渐变 + 细青灰描边 + 柔和下落阴影（=卡片浮起）
        'background-fill': 'linear-gradient',
        'background-gradient-stop-colors': '#ffffff #f6fbfa',
        'background-gradient-stop-positions': '0% 100%',
        'background-gradient-direction': 'to-bottom',
        'background-color': '#ffffff',
        'border-width': 1.5, 'border-color': '#9fb9b8', 'border-style': 'solid',
        'label': 'data(label)', 'color': C.ink,
        'font-size': 12, 'font-family': 'Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif',
        'font-weight': 600,
        'text-valign': 'center', 'text-halign': 'center',
        'text-wrap': 'ellipsis', 'text-max-width': (NODE_W - 20) + 'px',
        'text-margin-y': 0,
        // 卡片投影（浅青环境下柔和、不发脏）
        'shadow-blur': 14, 'shadow-color': 'rgba(13, 60, 58, .18)',
        'shadow-opacity': 1, 'shadow-offset-x': 0, 'shadow-offset-y': 4,
      } },
      // 类型描边区分：类=青，关系=靛蓝，属性=teal（只改描边色，纸卡不变）。
      { selector: 'node[kind="relation"]', style: { 'border-color': C.purple } },
      { selector: 'node[kind="attribute"]', style: { 'border-color': C.teal } },
      // 待收下候选：橄榄黄虚线 + 暖白卡面 —— 一眼区分「已发布」与「机器抽出、还没收下」。
      { selector: 'node[state="pending"]', style: {
        'border-style': 'dashed', 'border-color': C.amber,
        'background-gradient-stop-colors': '#fffdf4 #fdf6dd',
        'color': '#8a6108',
      } },
      // 选中：青色描边加粗 + 青色辉光（那圈光晕），纸卡保持白底。
      { selector: 'node[state="selected"]', style: {
        'border-color': C.green, 'border-width': 2.5, 'color': '#0f5a57',
        'shadow-blur': 22, 'shadow-color': 'rgba(13, 148, 136, .55)',
        'shadow-opacity': 1, 'shadow-offset-x': 0, 'shadow-offset-y': 0,
      } },
      // 悬停：微微提亮描边，给可点的节点一个反馈（不改变布局）。
      { selector: 'node:active', style: { 'overlay-opacity': 0 } },
      { selector: 'node:selected', style: { 'overlay-opacity': 0 } },
      { selector: 'edge', style: {
        'width': 2, 'curve-style': 'bezier', 'line-color': '#5f9b98',
        'target-arrow-color': '#5f9b98', 'target-arrow-shape': 'triangle', 'arrow-scale': 1.05,
        'line-cap': 'round',
      } },
      // 继承：父→子，深青实线 + 箭头（这就是「层级关系」本身）。
      { selector: 'edge[kind="inherit"]', style: {
        'line-color': C.cyan, 'target-arrow-color': C.cyan, 'line-style': 'solid',
      } },
      // 关系：domain→range，靛蓝虚线，带关系名标签，白底标签好读。
      { selector: 'edge[kind="relation"]', style: {
        'line-color': C.purple, 'line-style': 'dashed', 'target-arrow-shape': 'none',
        'label': 'data(label)', 'color': '#4f46e5', 'font-size': 11,
        'font-family': 'Inter, "Microsoft YaHei", sans-serif', 'text-rotation': 'autorotate',
        'text-background-color': '#ffffff', 'text-background-opacity': .92,
        'text-background-padding': 3, 'text-background-shape': 'roundrectangle',
        'text-border-color': 'rgba(99,102,241,.35)', 'text-border-width': 1, 'text-border-opacity': 1,
      } },
      // 待收下边：橄榄黄虚线 + 箭头。
      { selector: 'edge[kind="pending"]', style: {
        'line-color': C.amber, 'line-style': 'dashed', 'target-arrow-color': C.amber,
      } },
      { selector: 'edge:selected', style: { 'overlay-opacity': 0 } },
    ];
  }

  // ---- 元素同步（增量 diff：保留用户拖过的节点位置，只在增删时重排） ----------
  function applyElements(graph) {
    const cy = M.cy;
    const nodes = (graph && graph.nodes) || [];
    const edges = (graph && graph.edges) || [];
    const wantN = new Set(nodes.map(n => n.id));
    const wantE = new Set(edges.map(e => e.id));

    cy.batch(() => {
      cy.nodes().forEach(n => { if (!wantN.has(n.id())) n.remove(); });
      cy.edges().forEach(e => { if (!wantE.has(e.id())) e.remove(); });
    });

    cy.batch(() => {
      for (const n of nodes) {
        const data = {
          id: n.id, label: n.label || shortIri(n.id), state: n.state || 'published',
          kind: n.kind || 'class', dtype: n.dtype || '', iri: n.iri || n.id,
        };
        const el = cy.getElementById(n.id);
        if (el.length) el.data(data);
        else cy.add({ group: 'nodes', data });
      }
      for (const e of edges) {
        // 两端必须都在图上，否则 Cytoscape 会抛错（主控已过滤，这里再兜一层）。
        if (!cy.getElementById(e.source).length || !cy.getElementById(e.target).length) continue;
        const data = {
          id: e.id, source: e.source, target: e.target,
          kind: e.kind || 'inherit', label: e.label || '',
        };
        const el = cy.getElementById(e.id);
        if (el.length) el.data(data);
        else cy.add({ group: 'edges', data });
      }
    });
  }

  // 结构指纹：只有「节点/边集合」或「视图」变了才重排；选中变化不重排（否则会跳）。
  function signature(graph, view) {
    if (!graph) return view + '|';
    const n = graph.nodes.map(x => x.id).sort().join(',');
    const e = graph.edges.map(x => x.id).sort().join(',');
    return view + '|' + graph.nodes.length + '|' + n + '|' + graph.edges.length + '|' + e;
  }

  // ---- 图谱视图布局 ----------------------------------------------------------
  // 目标是「整体鸟瞰」：外接框的比例要接近容器（950×740），否则 fit 会被 min/maxZoom 卡住、图缩成一小团。
  // 实测：力导向 cose → 740×2126 竖条；breadthfirst → 3875×467 横条；concentric → 1951×2101
  // （比例对了但太大，zoom 仍只有 0.31）。唯一能同时控制「比例」和「大小」的是网格：
  // 让 cols/rows 匹配容器比例，外接框就接近容器尺寸，fit 后 zoom 可 >1（节点清晰可读）。
  function layoutGraph() {
    const cy = M.cy;
    const n = cy.nodes().length;
    let done = false;
    try {
      const el = cy.container();
      const cw = (el && el.clientWidth) || 950, ch = (el && el.clientHeight) || 740;
      // 节点宽高比约 4:1；要让网格外接框比例 ≈ 容器比例 r，则 cols/rows ≈ r·(NODE_H/NODE_W)。
      const cols = Math.max(2, Math.round(Math.sqrt(Math.max(1, n) * (cw / ch) * (NODE_H / NODE_W))));
      cy.layout({
        name: 'grid', fit: true, padding: 46, cols: cols, condense: false,
        avoidOverlap: true, avoidOverlapPadding: 14,
        // 按名字排序，与左栏清单同序，用户好对照。
        sort: (a, b) => String(a.data('label') || '').localeCompare(String(b.data('label') || '')),
      }).run();
      done = true;
    } catch (e) { done = false; }
    if (!done) {
      // 兜底：concentric 异常时退回力导向，再不行退回网格（至少不空白）。
      const repulsion = n > 40 ? 260000 : n > 18 ? 160000 : 90000;
      const ideal = n > 40 ? 150 : n > 18 ? 110 : 80;
      try {
        cy.layout({
          name: 'cose', animate: false, fit: true, padding: 46, randomize: true,
          nodeRepulsion: () => repulsion, idealEdgeLength: () => ideal,
          nodeOverlap: 40, gravity: 0.06, numIter: 2500, componentSpacing: 140,
        }).run();
      } catch (e2) {
        cy.layout({ name: 'grid', fit: true, padding: 30 }).run();
      }
    }
    // 布局后按**当前**容器尺寸重新量一次，再做「避让浮层」的 fit。
    // 为什么不能直接 cy.fit(elems,46)：统一 padding 只在元素框四周留等量空白，
    // 但画布上有两块**内嵌浮层**——底部工具条（约 76px 高）和右上角图例（约 150px 宽），
    // 等量留白会让下方节点被工具条压住、右侧节点被图例压（实测可见）。
    try { cy.resize(); fitWithChrome(); } catch (e) {
      try { cy.fit(undefined, 46); } catch (e2) { /* 量不到就算了 */ }
    }
    clearBands();
    syncBands();
  }

  // 「避让浮层」的居中拟合：把节点框放进扣掉【底部工具条 / 右上角例】后的可用区。
  // 与 fitBox 同一套思路，但这里框的不是层带矩形而是**全部元素的外接框**，
  // 且四边留白不对称（底部、右侧留更多），保证没有节点被浮层挡住。
  function fitWithChrome() {
    const cy = M.cy;
    const eles = cy.elements();
    if (!eles || !eles.length) return;
    const el = cy.container();
    if (!el) return;
    const cw = el.clientWidth || 0, ch = el.clientHeight || 0;
    if (!cw || !ch) return;

    // 浮层预留。图例默认【收成右上角小按钮】，上下预留设成【相等】，
    // 让节点群组的几何中心严格落在容器中心（offsetY=0）。
    // 底部 72 足够避开悬浮工具条，顶部 72 也远高于小图例按钮。
    const PAD = { left: 30, top: 72, right: 30, bottom: 72 };

    const bb = eles.boundingBox();
    const bw = Math.max(1, bb.w), bh = Math.max(1, bb.h);
    // 可用宽高（左右对称；顶部让出图例带、底部让出工具条）
    const availW = cw - PAD.left - PAD.right;
    const availH = ch - PAD.top - PAD.bottom;
    let zoom = Math.min(availW / bw, availH / bh);
    zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
    cy.zoom(zoom);

    // 可用区中心：水平方向严格居中，垂直方向位于「图例之下、工具条之上」的区间中心。
    const centerX = cw / 2;
    const centerY = (PAD.top + (ch - PAD.bottom)) / 2;
    cy.pan({
      x: centerX - (bb.x1 + bw / 2) * zoom,
      y: centerY - (bb.y1 + bh / 2) * zoom,
    });
  }

  // ---- 分层视图：最长父链求深度（搬 ontology-design.js 的松弛法） ------------
  function computeDepths(graph) {
    const inp = (graph.edges || []).filter(e => e.kind === 'inherit');
    const ids = new Set((graph.nodes || []).map(n => n.id));
    const depth = new Map((graph.nodes || []).map(n => [n.id, 0]));
    for (let round = 0; round <= (graph.nodes || []).length; round++) {
      let changed = false;
      for (const e of inp) {
        if (!ids.has(e.source) || !ids.has(e.target)) continue;
        const d = depth.get(e.target), p = depth.get(e.source);
        // 上限 12 层：坏数据（环）最多撑到 12，不会把画布高度炸掉。
        if (d < p + 1 && d < 12) { depth.set(e.target, p + 1); changed = true; }
      }
      if (!changed) break;
    }
    return depth;
  }

  // 分层带布局：同深度同一横带；带内按父类所在列排序（子类跟在父类下方）；
  // 每带最多 MAX_PER_ROW 张，超了换下一带（同层，位置在下，标「（续）」）。
  function layoutHierarchy() {
    const cy = M.cy;
    const graph = M.graph || { nodes: [], edges: [] };
    const depth = computeDepths(graph);
    const rows = new Map();
    for (const n of graph.nodes) {
      const d = depth.get(n.id) || 0;
      if (!rows.has(d)) rows.set(d, []);
      rows.get(d).push(n);
    }
    const rank = new Map();
    const order = [...rows.keys()].sort((a, b) => a - b);
    let cursor = 0;
    const positions = {};
    const bands = [];

    const parentRank = nd => {
      const vs = graph.edges.filter(e => e.kind === 'inherit' && e.target === nd.id)
        .map(e => rank.get(e.source)).filter(v => v !== undefined);
      return vs.length ? Math.min(...vs) : Number.MAX_SAFE_INTEGER;
    };

    order.forEach(depthKey => {
      const row = rows.get(depthKey);
      row.sort((a, b) => (parentRank(a) - parentRank(b)) ||
        String(a.label || '').localeCompare(String(b.label || ''), 'zh-CN'));
      row.forEach((nd, index) => {
        const column = index % MAX_PER_ROW;
        const y = cursor * (NODE_H + GAP_Y);
        if (column === 0) {
          const cols = Math.min(MAX_PER_ROW, row.length - index);
          // shade：隔层交替上色（按深度序号奇偶），既分得清层又不至于整屏色块。
          bands.push({ depth: depthKey, y, cols, cont: index > 0, shade: order.indexOf(depthKey) % 2 === 0 });
        }
        positions[nd.id] = { x: column * (NODE_W + GAP_X), y };
        rank.set(nd.id, cursor * MAX_PER_ROW + column);
        if (column === MAX_PER_ROW - 1 || index === row.length - 1) cursor += 1;
      });
    });

    // 不要用预设布局的 fit：它拟合的只是**节点**外接框，而层带是更宽的一圈 SVG 覆盖层
    // （左右各多 BAND_PAD）—— fit 不知道它存在，实测右边缘 966 > 容器 902，
    // 表现为"层级树不居中、右边被切掉"。所以先摆位，再按**层带外框**自己算缩放与平移。
    cy.layout({ name: 'preset', positions, fit: false, animate: false }).run();
    const frame = drawBands(bands);
    if (!frame || !fitBox(frame, BAND_FIT_PAD)) fitAll();
    syncBands();
  }

  function runLayout() {
    if (!M.cy) return;
    if (M.view === 'hierarchy') layoutHierarchy();
    else layoutGraph();
  }

  // ---- 层带覆盖层（HTML/SVG，叠在 Cytoscape 画布之上，pointer-events:none） -----
  // 为什么不用 compound 节点：Cytoscape 的父子节点坐标是相对父级的，套层带要再折算；
  // 这里用绝对覆盖层，按 pan/zoom 直接映射模型坐标，简单且不依赖核心渲染。
  // 返回所有带的**外接框**（模型坐标）：层级树的居中判据就是这个框，不是节点框。
  function drawBands(bands) {
    const svg = M.svg;
    if (!svg) return null;
    svg.replaceChildren();
    M.bands = [];
    if (!bands.length) return null;
    const maxCols = bands.reduce((m, b) => Math.max(m, b.cols), 1);
    const contentW = (maxCols - 1) * (NODE_W + GAP_X) + NODE_W;
    let top = Infinity, bottom = -Infinity;
    for (const b of bands) {
      const rect = svgEl('rect');
      rect.setAttribute('class', 'om-canvas-band' + (b.shade ? ' is-shade' : ''));
      rect.setAttribute('rx', '14');
      const label = svgEl('text');
      label.setAttribute('class', 'om-canvas-band-label');
      label.textContent = `第 ${b.depth + 1} 层${b.depth === 0 ? '（根类）' : ''}${b.cont ? '（续）' : ''}`;
      svg.appendChild(rect);
      svg.appendChild(label);
      // 层带上下也要对称：节点中心在 b.y、卡片高 NODE_H → 范围 [b.y-21, b.y+21]，
      // 层带 = 卡片范围上下各外扩 BAND_PAD（[b.y-35, b.y+35]）。原来 my=b.y-16、h=NODE_H+32
      // 是 [b.y-16, b.y+58]——上只留 5px、下空出 37px，框在节点下方空一大截，看着"不对应"。
      const my = b.y - NODE_H / 2 - BAND_PAD, h = NODE_H + BAND_PAD * 2;
      // 层带横向必须从**第一列卡片的左边缘**起（中心在 0 的卡片左缘是 -NODE_W/2），
      // 再向两侧外扩 BAND_PAD。原来从 -BAND_PAD 起，最左一列节点的左半边整段露在框外，
      // 且整个框相对卡片右偏 NODE_W/2，fit 后节点整体偏左——层级树"节点不在框里"由此而来。
      const mx = -NODE_W / 2 - BAND_PAD;
      M.bands.push({
        el: rect, label,
        mx, my, w: contentW + BAND_PAD * 2, h,
        lx: mx + 4, ly: my + 4,   // 层号标签画在带**内侧**左上角（随带左/顶缘走）
      });
      top = Math.min(top, my);
      bottom = Math.max(bottom, my + h);
    }
    syncBands();
    return { x1: -NODE_W / 2 - BAND_PAD, y1: top,
             x2: -NODE_W / 2 - BAND_PAD + contentW + BAND_PAD * 2, y2: bottom };
  }

  function clearBands() {
    if (M.svg) M.svg.replaceChildren();
    M.bands = [];
  }

  // 把模型坐标 × zoom + pan 映射成像素坐标（与 Cytoscape 同一套变换）。
  function syncBands() {
    const cy = M.cy;
    if (!cy) return;
    const zoom = cy.zoom(), pan = cy.pan();
    for (const b of M.bands) {
      b.el.setAttribute('x', (b.mx * zoom + pan.x).toFixed(1));
      b.el.setAttribute('y', (b.my * zoom + pan.y).toFixed(1));
      b.el.setAttribute('width', (b.w * zoom).toFixed(1));
      b.el.setAttribute('height', (b.h * zoom).toFixed(1));
      b.label.setAttribute('x', (b.lx * zoom + pan.x).toFixed(1));
      b.label.setAttribute('y', (b.ly * zoom + pan.y).toFixed(1));
      b.label.setAttribute('font-size', Math.max(8, Math.min(14, 11 * zoom)).toFixed(1));
    }
  }

  function fitAll() {
    if (!M.cy) return;
    const eles = M.cy.elements();
    if (!eles.length) return;
    try { M.cy.fit(eles, 40); } catch (e) { /* 忽略：空图时 fit 无意义 */ }
    syncBands();
  }

  // 把**一块矩形区域**（层带外框）缩放到容器里并居中，返回是否成功。
  // 为什么不用 cy.fit()：它只吃元素集合，吃不了矩形；而且卡片之外还有更宽的层带，
  // 用节点框去 fit 就必然偏。这里直接算 zoom/pan：
  //   zoom = min(可用宽/框宽, 可用高/框高)（夹在 MIN_ZOOM..MAX_ZOOM 之间）
  //   pan  = 让框的中心落在容器中心
  function fitBox(box, pad) {
    const cy = M.cy; if (!cy || !box) return false;
    const el = cy.container(); if (!el) return false;
    const w = el.clientWidth || 0, h = el.clientHeight || 0;
    if (!w || !h) return false;     // 容器还没量到尺寸：让调用方回落到 fitAll
    const bw = Math.max(1, box.x2 - box.x1), bh = Math.max(1, box.y2 - box.y1);
    const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM,
      Math.min((w - pad * 2) / bw, (h - pad * 2) / bh)));
    cy.zoom(zoom);
    cy.pan({ x: (w - bw * zoom) / 2 - box.x1 * zoom,
             y: (h - bh * zoom) / 2 - box.y1 * zoom });
    return true;
  }

  // ---- 交互：选中 / 连线（两步点击 + 确认，无橡皮筋） ------------------------
  function onNodeTap(evt) {
    const node = evt.target;
    const iri = node.data('iri') || node.id();
    if (!iri) return;

    // 改父类模式：当前点到的卡片 = 父类（被选中的那份是子类）。
    if (M.mode === 'parent') {
      const child = M.linkFrom;
      if (!child) { cancelLink(); return; }
      if (child === iri) { status('父类和子类不能是同一个类。', true); return; }
      M.pending = { type: 'parent', child, parent: iri };
      renderConfirm();
      updateBar();
      return;
    }

    // 连关系模式：第一次点＝起点(domain)，第二次点＝终点(range)。
    if (M.mode === 'relation') {
      if (!M.linkFrom) {
        M.linkFrom = iri;
        status(`已选起点「${labelOf(iri)}」：请点终点（range）卡片。`);
        updateBar();
        return;
      }
      if (M.linkFrom === iri) { status('起点和终点不能是同一个类。', true); return; }
      M.pending = { type: 'relation', domain: M.linkFrom, range: iri };
      renderConfirm();
      updateBar();
      return;
    }

    // 普通点击：唯一的选中入口（主控负责广播给右栏与左清单）。
    if (M.ctx && typeof M.ctx.select === 'function') M.ctx.select(iri);
  }

  function onBackgroundTap() {
    // 点空白 = 退出连线/未确认状态（用户不用记 Esc）。没有挂起操作时什么都不做。
    if (M.mode || M.pending) {
      cancelLink();
      status('已退出连线模式，没有改动草案。');
    }
  }

  // 工具条「＋ 新建类」：右栏的「新建术语」表单只在**没有选中对象**时渲染，
  // 所以这里先清空选中，再让面板把表单调出来并聚焦到名称输入框（两步都不写数据）。
  function openCreate() {
    if (M.ctx && typeof M.ctx.select === 'function') M.ctx.select(null);
    const panel = window.OntologyPanel;
    if (panel && typeof panel.focusCreate === 'function') panel.focusCreate();
    status('右栏已打开「新建术语」：填中文名即可新建类，归属（父类）可以留空。');
  }

  function startParentLink() {
    if (!M.selected) { status('先在画布上点选一个类，再点「改父类（连线）」。', true); return; }
    M.mode = 'parent';
    M.linkFrom = M.selected;
    M.pending = null;
    renderConfirm();
    updateBar();
    status(`连线中：点另一个类，把它设为「${labelOf(M.selected)}」的父类。点空白处或「取消」退出。`);
  }

  function startRelationLink() {
    M.mode = 'relation';
    M.linkFrom = null;
    M.pending = null;
    renderConfirm();
    updateBar();
    status('连线中：先点「起点（domain）」的类，再点「终点（range）」的类。');
  }

  function cancelLink() {
    if (!M.mode && !M.pending) return;
    M.mode = '';
    M.linkFrom = null;
    M.pending = null;
    renderConfirm();
    updateBar();
  }

  // 确认条：把"待写入的变更"摆出来再让用户确认（对应两步点击的第二步）。
  function renderConfirm() {
    const host = M.els.confirm;
    if (!host) return;
    host.replaceChildren();
    if (!M.pending) { host.hidden = true; return; }
    host.hidden = false;

    if (M.pending.type === 'parent') {
      host.appendChild(mkEl('span', 'om-canvas-confirm-copy',
        `建立父子关系：${labelOf(M.pending.parent)}（父类） ⊑ ${labelOf(M.pending.child)}（子类）`));
      host.appendChild(mkButton('确认建立父级', confirmParent, 'primary'));
      host.appendChild(mkButton('取消', cancelLink));
      return;
    }
    // 关系：需要名字，给一个输入框（默认空，提示示例）。
    host.appendChild(mkEl('span', 'om-canvas-confirm-copy',
      `建立关系：${labelOf(M.pending.domain)} → ${labelOf(M.pending.range)}`));
    const input = mkEl('input', 'om-canvas-confirm-input');
    input.type = 'text';
    input.placeholder = '关系名（如：属于）';
    input.setAttribute('aria-label', '关系名');
    host.appendChild(input);
    host.appendChild(mkButton('确认建关系', () => confirmRelation(input.value), 'primary'));
    host.appendChild(mkButton('取消', cancelLink));
    const focusIt = () => { try { input.focus(); } catch (e) { /* 忽略 */ } };
    setTimeout(focusIt, 0);
  }

  async function confirmParent() {
    const p = M.pending;
    if (!p) return;
    try {
      // target_iri = 子类（要改的那个），parent_iri / value = 父类；形状见契约 §3.3。
      await M.ctx.sendCommand({
        action: 'add_parent', target_iri: p.child, parent_iri: p.parent,
        value: p.parent, reason: '画布连线：建立父级',
      });
      status(`已把「${labelOf(p.parent)}」加为「${labelOf(p.child)}」的父类（写入草案，待审核）。`);
    } catch (e) {
      status(`建立父级失败：${e && e.message ? e.message : e}`, true);
    }
    M.pending = null; M.mode = ''; M.linkFrom = null;
    renderConfirm(); updateBar();
  }

  async function confirmRelation(name) {
    const p = M.pending;
    if (!p) return;
    const label = String(name || '').trim();
    if (!label) { status('请先填写关系名。', true); return; }
    try {
      await createRelation(label, p.domain, p.range);
      status(`已新增关系类型「${label}」：${labelOf(p.domain)} → ${labelOf(p.range)}（写入草案，待审核）。`);
    } catch (e) {
      status(`建立关系失败：${e && e.message ? e.message : e}`, true);
    }
    M.pending = null; M.mode = ''; M.linkFrom = null;
    renderConfirm(); updateBar();
  }

  // 建关系为何走 /ontology/terms 而不是 create_term 命令：
  //   create_term 要求客户端自己给出 target_iri，而术语 IRI 的命名规则只在服务端
  //   （ontology-design.js 的 createTerm 同款做法）。这条路由接受 draft_id +
  //   expected_revision，会把改动编译成原子变更写进当前草案（仍要审核）。
  async function createRelation(labelZh, domainIri, rangeIri) {
    const globalApi = typeof api !== 'undefined' ? api : (window.api || null);
    const globalEndpoint = typeof endpoint !== 'undefined' ? endpoint : (window.endpoint || null);
    if (!globalApi || !globalEndpoint) throw new Error('缺少 API 通道（api/endpoint 未就绪）');
    const draft = M.ctx && M.ctx.S && M.ctx.S.draft;
    if (!draft || !draft.id) throw new Error('还没有可编辑的草案，请先发一条编辑命令');
    await globalApi(globalEndpoint('/ontology/terms'), {
      kind: 'relation', uri: '', label: labelZh, label_zh: labelZh,
      parent: '', domain: domainIri, range: rangeIri,
      domains: [domainIri], ranges: [rangeIri],
      expected_ontology_id: draft.base_ontology_id || '',
      draft_id: draft.id, expected_revision: draft.revision,
    });
    if (typeof M.ctx.refresh === 'function') await M.ctx.refresh();
  }

  // ---- 工具条 / 图例 / 空态 -------------------------------------------------
  function buildBar(stage) {
    const bar = mkEl('div', 'om-canvas-bar');
    const stats = mkEl('span', 'om-canvas-stats', '实体类 0 · 继承线 0 · 关系类型 0（画得出线 0）');
    const hint = mkEl('span', 'om-canvas-hint', '');

    // 「新建类」是 P1 重写时漏掉的入口：以前整页只有「改父类（连线）」，而它要求先选中
    // 一个**已存在**的类 —— 于是空本体上"想加个父类"根本无从下手（用户原话："现在父类也加不了"）。
    const btnNew = mkButton('＋ 新建类', openCreate, '', BAR_TITLES.create);
    const btnParent = mkButton('改父类（连线）', startParentLink, '', BAR_TITLES.parent);
    const btnRelation = mkButton('连关系', startRelationLink, '', BAR_TITLES.relation);
    const btnFit = mkButton('适应画布', fitAll, '',
      '做什么：把所有节点缩放到刚好铺满画布。不做什么：不改动任何数据。');
    const btnCancel = mkButton('取消', cancelLink, 'om-canvas-cancel',
      '做什么：退出连线模式，不写任何改动。');
    btnCancel.hidden = true;

    bar.append(stats, hint, btnNew, btnParent, btnRelation, btnFit, btnCancel);
    stage.appendChild(bar);
    Object.assign(M.els, { bar, stats, hint, btnNew, btnParent, btnRelation, btnCancel });
  }

  function buildLegend(stage) {
    // 图例默认【收起】：只占右上角一个小按钮，把垂直空间还给画布，让图谱真正居中。
    // 点开才展开线义浮卡（点空白/再点按钮收起）。
    const box = mkEl('div', 'om-canvas-legend');
    const toggle = mkEl('button', 'om-canvas-legend-toggle', '图例');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-haspopup', 'true');
    toggle.title = '做什么：展开「线/颜色代表什么」的说明。不做什么：不改任何数据。';

    const body = mkEl('div', 'om-canvas-legend-body');
    const items = [
      ['line', '深青实线 = 父子（父→子）'],
      ['line rel', '靛蓝虚线 = 关系（domain→range）'],
      ['line pending', '橄榄黄虚线 = 待收下候选'],
      ['dot', '青绿发光 = 当前选中'],
    ];
    for (const [cls, text] of items) {
      const row = mkEl('span', 'om-canvas-legend-item');
      row.appendChild(mkEl('i', cls));   // 形状由 CSS 的 .line / .dot 决定
      row.appendChild(mkEl('span', '', text));
      body.appendChild(row);
    }

    function setOpen(open) {
      box.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', String(open));
    }
    toggle.addEventListener('click', evt => {
      evt.stopPropagation();
      setOpen(!box.classList.contains('is-open'));
    });
    // 点浮卡之外的地方收起（吃一次 stage 级点击；不影响 Cytoscape 的交互）。
    stage.addEventListener('click', () => setOpen(false));
    body.addEventListener('click', evt => evt.stopPropagation());

    box.append(toggle, body);
    stage.appendChild(box);
    M.els.legend = box;
  }

  function buildEmpty(stage) {
    const empty = mkEl('div', 'om-canvas-empty', '还没有可画的类：先在左侧或右栏新增实体类。');
    stage.appendChild(empty);
    M.els.empty = empty;
  }

  function buildConfirm(stage) {
    const confirm = mkEl('div', 'om-canvas-confirm');
    confirm.hidden = true;
    stage.appendChild(confirm);
    M.els.confirm = confirm;
  }

  function updateEmpty() {
    const empty = M.els.empty;
    if (!empty) return;
    const has = !!(M.graph && M.graph.nodes && M.graph.nodes.length);
    empty.hidden = has;
  }

  function updateBar() {
    const e = M.els;
    if (!e.stats) return;
    const nodes = (M.graph && M.graph.nodes) || [];
    const edges = (M.graph && M.graph.edges) || [];
    const inh = edges.filter(x => x.kind === 'inherit').length;
    const rel = edges.filter(x => x.kind === 'relation').length;
    const pend = nodes.filter(x => x.state === 'pending').length;
    const classes = nodes.filter(x => x.kind === 'class').length;
    // 两个"关系"数必须分开说：左栏数的是本体里**有几条关系类型**，这里数的是**画得出几条线**
    // （只有两端 domain/range 都知道才画得出来）。以前两边都写"关系 N"，同一屏出现
    // "关系类型 9"和"关系 0"，用户第一反应就是"关系怎么没了"——这正是这轮反馈的起因。
    const summary = (M.ctx && M.ctx.S && M.ctx.S.summary) || null;
    const relTotal = summary ? (summary.relations || []).filter(r => r.active !== false).length : null;
    e.stats.textContent = `实体类 ${classes} · 继承线 ${inh}`
      + (relTotal === null ? ` · 关系线 ${rel}` : ` · 关系类型 ${relTotal}（画得出线 ${rel}）`)
      + (pend ? ` · 待收下 ${pend}` : '');
    let hint = '';
    // 历史版本是只读预览：编辑入口一律锁住，并把"去哪才能改"写在工具条上
    //（灰按钮不解释原因，用户只会以为页面坏了）。
    const locked = !!(M.ctx && M.ctx.S && M.ctx.S.preview);
    if (locked) hint = '历史版本（只读）：要点顶栏「回到最新」，或用「版本管理」→「回到这一版」开一份新草案才能改。';
    else if (M.pending) hint = '确认后才写进草案（待审核）。';
    else if (M.mode === 'parent') hint = `连线中：点目标卡片设为「${labelOf(M.linkFrom)}」的父类`;
    else if (M.mode === 'relation') hint = M.linkFrom ? '连线中：点终点（range）卡片' : '连线中：先点起点（domain）卡片';
    e.hint.textContent = hint;
    e.btnNew.disabled = locked;
    e.btnParent.disabled = locked || !M.selected;
    e.btnRelation.disabled = locked || !nodes.length;
    e.btnNew.title = locked ? BAR_TITLES.locked : BAR_TITLES.create;
    e.btnParent.title = locked ? BAR_TITLES.locked : BAR_TITLES.parent;
    e.btnRelation.title = locked ? BAR_TITLES.locked : BAR_TITLES.relation;
    e.btnCancel.hidden = !(M.mode || M.pending);
  }

  // ---- 生命周期 -------------------------------------------------------------
  function bindCyEvents() {
    const cy = M.cy;
    cy.on('tap', 'node', onNodeTap);
    cy.on('tap', evt => { if (evt.target === cy) onBackgroundTap(); });
    // 双击空白 = 适应画布（双击节点不触发，避免误操作）。
    cy.on('dblclick', evt => { if (evt.target === cy) fitAll(); });
    cy.on('doubleTap', evt => { if (evt.target === cy) fitAll(); });
    // 平移/缩放后同步层带位置（自带滚轮以光标为锚点缩放 + 拖空白平移）。
    cy.on('pan zoom', syncBands);
  }

  function mount(host, ctx) {
    if (!host) return;
    if (ctx) M.ctx = ctx;
    // 换容器（切项目会重建骨架、传入新的 host）：先销毁旧的再重建，否则画布丢进旧节点。
    if (M.ready && M.host && M.host !== host) destroy();
    if (M.ready && M.host === host) return;

    M.host = host;
    host.replaceChildren();
    const stage = mkEl('div', 'om-canvas-stage');
    host.appendChild(stage);
    M.stage = stage;

    const cyto = CY();
    if (cyto) {
      M.cy = cyto({
        container: stage,
        wheelSensitivity: 0.2,
        minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM,
        boxSelectionEnabled: false,
        userZoomingEnabled: true, userPanningEnabled: true,
        styleEnabled: true, motionBlur: false,
        style: cyStyle(),
        layout: { name: 'preset' },
        elements: [],
      });
      bindCyEvents();
    }

    // 覆盖层与工具条都建在 stage 上（Cytoscape 的 canvas 在其下方）。
    const svg = svgEl('svg');
    svg.setAttribute('class', 'om-canvas-bands');
    svg.setAttribute('aria-hidden', 'true');
    stage.appendChild(svg);
    M.svg = svg;

    buildEmpty(stage);
    buildLegend(stage);
    buildBar(stage);
    buildConfirm(stage);

    if (!cyto) {
      // 兜底：测试 mock / vendor 缺失时不崩，只提示。
      M.els.empty.hidden = false;
      M.els.empty.textContent = '画布引擎未加载（vendor/cytoscape.min.js 缺失）。';
    }

    // 尺寸变化（初始隐藏、窗口缩放、栏宽变化）：resize + 首次量到尺寸时补一次布局。
    if (typeof ResizeObserver !== 'undefined') {
      M.ro = new ResizeObserver(() => {
        if (!M.cy) return;
        M.cy.resize();
        syncBands();
        if (!M.didFit && M.stage && M.stage.clientWidth > 0) {
          M.didFit = true; M.sig = ''; runLayout();
        }
      });
      M.ro.observe(stage);
    }
    M.onResize = () => { if (M.cy) { M.cy.resize(); syncBands(); } };
    window.addEventListener('resize', M.onResize);

    M.ready = true;
  }

  function render(state) {
    if (!M.ready) return;
    const S = state || (M.ctx && M.ctx.S) || {};
    M.graph = S.graph || { nodes: [], edges: [] };
    M.selected = S.selected || null;
    if (S.view) M.view = S.view;

    const cy = M.cy;
    if (!cy) { updateEmpty(); updateBar(); return; }

    applyElements(M.graph);
    updateEmpty();
    updateBar();

    const sig = signature(M.graph, M.view);
    let need = sig !== M.sig;
    if (!M.didFit && M.stage && M.stage.clientWidth > 0) { M.didFit = true; need = true; }
    if (need) { M.sig = sig; runLayout(); }
  }

  function setView(mode) {
    M.view = mode === 'hierarchy' ? 'hierarchy' : 'graph';
    if (!M.ready || !M.cy) return;
    M.sig = signature(M.graph, M.view);   // 记下指纹，避免紧随其后的 render 再排一次
    runLayout();
    updateBar();
  }

  function focus(iri) {
    const cy = M.cy;
    if (!cy || !iri) return;
    const node = cy.getElementById(iri);
    if (!node || !node.length) return;
    try {
      cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.1), duration: 300 });
    } catch (e) {
      try { cy.center(node); } catch (e2) { /* 忽略 */ }
    }
    syncBands();
  }

  function destroy() {
    if (M.ro) { try { M.ro.disconnect(); } catch (e) { /* 忽略 */ } M.ro = null; }
    if (M.onResize) { window.removeEventListener('resize', M.onResize); M.onResize = null; }
    if (M.cy) { try { M.cy.destroy(); } catch (e) { /* 忽略 */ } M.cy = null; }
    if (M.host) { try { M.host.replaceChildren(); } catch (e) { /* 忽略 */ } }
    M.ready = false; M.host = null; M.stage = null; M.svg = null;
    M.bands = []; M.els = {}; M.sig = ''; M.didFit = false;
    M.mode = ''; M.linkFrom = null; M.pending = null;
  }

  window.OntologyCanvas = {
    mounted: false,
    // 只读调试出口：真机排查布局/缩放用（返回内部 Cytoscape 实例），不参与业务逻辑。
    __cy: () => M.cy,
    mount(host, ctx) { mount(host, ctx); this.mounted = M.ready; },
    render(state) { render(state); },
    setView(mode) { setView(mode); },
    focus(iri) { focus(iri); },
    destroy() { destroy(); this.mounted = false; },
  };
})();
