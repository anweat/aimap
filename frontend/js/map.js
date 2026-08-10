/* ===== 四元数领域地图渲染 v3(ES Module)=====
 * 视觉关联度与可用性增强:
 *   - 领域球半径 ∝ 论文数(热度可视),hot 领域发光;
 *   - 论文点 → 所属领域连线(关联可视化);
 *   - hover 显示 tooltip(论文标题/领域信息);
 *   - 着色切换:视角(根领域色)/ 学科(arXiv 分类色);
 *   - 图例说明;
 *   - WebGL 不可用时自动降级 Canvas 2D(同等功能)。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const ROOT_COLORS = {
  models: 0x58a6ff,
  algorithm: 0x3fb950,
  infra: 0xd29922,
  other: 0x8b949e,
};
const LEVEL_COLORS = ["#58a6ff", "#79c0ff", "#a5d6ff", "#d2e8ff"];
const HEAT_EMISSIVE = { hot: 0.55, normal: 0.15, cold: 0.05 };

// 学科 → 稳定颜色(哈希 HSL)
function categoryColor(key) {
  if (!key) return 0x8b949e;
  let h = 0;
  for (const c of String(key)) h = (h * 31 + c.charCodeAt(0)) % 360;
  return new THREE.Color().setHSL(h / 360, 0.72, 0.55).getHex();
}
function categoryColor2d(key) {
  const c = new THREE.Color(categoryColor(key));
  return "#" + c.getHexString();
}

export class QuaternionMap {
  /** 门面:按 WebGL 可用性选择 3D 或 2D 渲染器,接口一致。 */
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks || {};
    this.mode = "3d";
    try {
      this.impl = new Map3DRenderer(container, this.callbacks);
      this._notifyMode("3d");
    } catch (e) {
      console.warn("[map] WebGL 不可用,降级为 2D 渲染:", e.message);
      this.mode = "2d";
      this.impl = new Map2DRenderer(container, this.callbacks);
      this._notifyMode("2d");
    }
  }
  _notifyMode(mode) {
    if (this.callbacks.onMode) this.callbacks.onMode(mode);
  }
  _clearLoading() {
    const hint = this.container.querySelector(".loading-hint");
    if (hint) hint.remove();
  }
  render(mapData) {
    this._clearLoading();
    this.impl.render(mapData);
  }
  setColorMode(mode) { this.impl.setColorMode(mode); }
  focusDomain(key) { this.impl.focusDomain(key); }
  focusPaper(id) { this.impl.focusPaper(id); }
  highlightPapers(keys) { this.impl.highlightPapers(keys); }
  resetHighlight() { this.impl.resetHighlight(); }
  setAutoRotate(on) { this.impl.setAutoRotate(on); }
  setPanMode(on) { this.impl.setPanMode(on); }
  /** 进入子领域视图:focusChain 为从根到当前领域的 key 数组 */
  setFocus(focusChain) { this.impl.setFocus(focusChain); }
  /** 回到根视图并对齐零点 */
  goHome() { this.impl.goHome(); }
  /** 当前是否处于子领域视图 */
  inFocus() { return this.impl.inFocus(); }
}

// ===========================================================================
// 四元数工具:局部坐标(相对指定中心展开,与后端 Projector 同公式)
// ===========================================================================
function qToThree(qArr) {
  // 后端 [w,x,y,z] → THREE.Quaternion(x,y,z,w)
  return new THREE.Quaternion(qArr[1], qArr[2], qArr[3], qArr[0]);
}
function localXYZ(qArr, centerQ, scale) {
  const ql = centerQ.clone().conjugate().multiply(qToThree(qArr));
  const denom = 1 - ql.w;
  const s = scale / (Math.abs(denom) < 1e-9 ? 1 : denom);
  return [ql.x * s, ql.y * s, ql.z * s];
}

// ===========================================================================
// 公共 tooltip
// ===========================================================================
class MapTooltip {
  constructor(container) {
    this.el = document.createElement("div");
    this.el.className = "map-tooltip";
    this.el.style.display = "none";
    container.appendChild(this.el);
  }
  show(text, x, y) {
    this.el.textContent = text;
    this.el.style.display = "block";
    this.el.style.left = `${x + 14}px`;
    this.el.style.top = `${y + 14}px`;
  }
  move(x, y) {
    this.el.style.left = `${x + 14}px`;
    this.el.style.top = `${y + 14}px`;
  }
  hide() { this.el.style.display = "none"; }
}

// ===========================================================================
// 3D 渲染器(WebGL)
// ===========================================================================
class Map3DRenderer {
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks || {};
    this.colorMode = "view";
    this.focus = null;              // {key, chain:[...]} 子领域视图状态
    this.domainMesh = new Map();
    this.paperMesh = new Map();
    this._buildScene();
  }

  _buildScene() {
    const w = this.container.clientWidth || 800;
    const h = this.container.clientHeight || 500;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d1117);

    this.camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 100);
    this.camera.position.set(5.5, 3.2, 5.5);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(w, h);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.6;
    this.controls.minDistance = 1.5;
    this.controls.maxDistance = 30;

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(4, 6, 3);
    this.scene.add(dir);

    // 辅助:细网格(零点居中,不再下沉)+ 坐标轴
    const grid = new THREE.GridHelper(12, 24, 0x2d333b, 0x21262d);
    grid.position.y = 0;
    this.scene.add(grid);
    this.scene.add(new THREE.AxesHelper(2.2));

    this.raycaster = new THREE.Raycaster();
    this.mouse = new THREE.Vector2();
    this.lines = [];               // 记录连线对象,render 时清理(避免残留)
    this.tooltip = new MapTooltip(this.container);
    this.renderer.domElement.addEventListener("click", (e) => this._onClick(e));
    this.renderer.domElement.addEventListener("dblclick", (e) => this._onDblClick(e));
    this.renderer.domElement.addEventListener("mousemove", (e) => this._onHover(e));
    this.renderer.domElement.addEventListener("mouseleave", () => this.tooltip.hide());

    this._animate();
    new ResizeObserver(() => this._resize()).observe(this.container);
  }

  render(mapData) {
    this._clear();
    this.data = mapData;
    const view = this._computeView(mapData);

    // 领域球:半径 ∝ 论文数,hot 发光
    const maxCount = Math.max(1, ...(view.domains || []).map((d) => d.paper_count || 0));
    for (const d of view.domains || []) {
      const isCenter = this.focus && d.key === this.focus.key;
      const level = Math.min(d.level || 1, 3);
      const radius = isCenter ? 0.22 : 0.09 + 0.05 * Math.sqrt((d.paper_count || 0) / maxCount) + (3 - level) * 0.015;
      const geo = new THREE.SphereGeometry(Math.min(radius, 0.26), 20, 20);
      const mat = new THREE.MeshPhongMaterial({
        color: isCenter ? 0xffffff : LEVEL_COLORS[level],
        transparent: true, opacity: 0.95,
        emissive: isCenter ? 0xffffff : LEVEL_COLORS[level],
        emissiveIntensity: isCenter ? 0.35 : HEAT_EMISSIVE[d.heat] || 0.15,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...d.xyz);
      mesh.userData = { type: "domain", key: d.key, name: d.name, paper_count: d.paper_count, heat: d.heat };
      this.scene.add(mesh);
      this.domainMesh.set(d.key, mesh);
    }

    // 领域父子连线(仅域内)
    for (const d of view.domains || []) {
      const parentKey = d.parent;
      if (!parentKey || !this.domainMesh.has(parentKey)) continue;
      const child = this.domainMesh.get(d.key);
      const parent = this.domainMesh.get(parentKey);
      if (child && parent) this._line(parent.position, child.position, 0x2d333b, 0.6);
    }

    // 论文点 + 关联连线
    const paperGroup = [];
    for (const p of view.papers || []) {
      const color = this._paperColor(p);
      const geo = new THREE.SphereGeometry(0.045, 10, 10);
      const mat = new THREE.MeshPhongMaterial({
        color, emissive: color, emissiveIntensity: 0.55, transparent: true, opacity: 0.92,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...p.xyz);
      mesh.userData = { type: "paper", id: p.id, title: p.title, domain_key: p.domain_key, category: p.category };
      this.scene.add(mesh);
      this.paperMesh.set(p.id, mesh);
      paperGroup.push({ mesh, domainKey: p.domain_key });
    }
    for (const { mesh, domainKey } of paperGroup) {
      const domain = this.domainMesh.get(domainKey);
      if (domain) this._line(mesh.position, domain.position, 0x3fb950, 0.1);
    }
    this._fit();
  }

  /** 子领域视图:以焦点领域为投影中心,子树节点/论文相对展开 */
  _computeView(mapData) {
    if (!this.focus) return mapData;
    const prefix = this.focus.key + ".";
    const centerQ = qToThree(this._centerQ(mapData));
    const scale = this.focus.level <= 1 ? 2.6 : 2.2;
    const domains = (mapData.domains || [])
      .filter((d) => d.key === this.focus.key || d.key.startsWith(prefix))
      .map((d) => ({
        ...d,
        xyz: d.key === this.focus.key ? [0, 0, 0] : localXYZ(d.q, centerQ, scale),
      }));
    const inView = new Set(domains.map((d) => d.key));
    for (const d of domains) {
      if (d.parent && !inView.has(d.parent)) d.parent = null;  // 域外父节点断开连线
    }
    const papers = (mapData.papers || [])
      .filter((p) => p.domain_key === this.focus.key || p.domain_key.startsWith(prefix))
      .map((p) => ({ ...p, xyz: localXYZ(p.q, centerQ, scale) }));
    return { domains, papers };
  }

  _centerQ(mapData) {
    const d = (mapData.domains || []).find((x) => x.key === this.focus.key);
    return d ? d.q : [1, 0, 0, 0];
  }

  _paperColor(p) {
    if (this.colorMode === "category") return categoryColor(p.category);
    const root = String(p.domain_key || "").split(".")[0];
    return ROOT_COLORS[root] !== undefined ? ROOT_COLORS[root] : ROOT_COLORS.other;
  }

  setColorMode(mode) {
    this.colorMode = mode;
    if (!this.data) return;
    for (const p of this.data.papers || []) {
      const mesh = this.paperMesh.get(p.id);
      if (!mesh) continue;
      const color = this._paperColor(p);
      mesh.material.color.setHex(color);
      mesh.material.emissive.setHex(color);
    }
  }

  _onClick(e) {
    const hit = this._pick(e);
    if (!hit) return;
    if (hit.type === "paper" && this.callbacks.onPaperClick) this.callbacks.onPaperClick(hit.id);
    else if (hit.type === "domain" && this.callbacks.onDomainClick) this.callbacks.onDomainClick(hit.key);
  }

  _onDblClick(e) {
    const hit = this._pick(e);
    if (hit && hit.type === "domain" && this.callbacks.onEnterDomain) {
      this.callbacks.onEnterDomain(hit.key);
    }
  }

  // -- 导航 ------------------------------------------------------------
  setAutoRotate(on) { this.controls.autoRotate = on; }
  setPanMode(on) {
    this.panMode = on;
    // 平移模式:左键拖动 = 移动视图中心(pan);旋转移到右键
    this.controls.mouseButtons = on
      ? { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.DOLLY }
      : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    this.renderer.domElement.style.cursor = on ? "move" : "grab";
  }
  setFocus(chain) {
    this.focus = chain && chain.length ? { key: chain[chain.length - 1], chain } : null;
    this.controls.autoRotate = false;  // 进入子领域时暂停旋转,便于观察
    if (this.data) this.render(this.data);
  }
  goHome() {
    this.focus = null;
    this.controls.autoRotate = true;
    this.camera.position.set(5.5, 3.2, 5.5);
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    if (this.data) this.render(this.data);
  }
  inFocus() { return !!this.focus; }

  _onHover(e) {
    const hit = this._pick(e);
    this.renderer.domElement.style.cursor = hit ? "pointer" : "grab";
    if (this._hovered) this._hovered.material.emissiveIntensity = this._hoveredBase;
    this._hovered = null;
    if (hit) {
      this._hovered = hit.mesh;
      this._hoveredBase = hit.mesh.material.emissiveIntensity;
      hit.mesh.material.emissiveIntensity = 1.0;
      const rect = this.renderer.domElement.getBoundingClientRect();
      if (hit.type === "paper") {
        this.tooltip.show(`${hit.title}`, e.clientX - rect.left, e.clientY - rect.top);
      } else {
        this.tooltip.show(`${hit.name} · ${hit.paper_count} 篇${hit.heat === "hot" ? " · 🔥热门" : hit.heat === "cold" ? " · 冷门" : ""}`,
          e.clientX - rect.left, e.clientY - rect.top);
      }
      if (this.callbacks.onHover) this.callbacks.onHover(hit);
    } else {
      this.tooltip.hide();
    }
  }

  _pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.mouse, this.camera);
    const hits = this.raycaster.intersectObjects(this.scene.children, false)
      .filter((h) => h.object.userData && h.object.userData.type);
    if (!hits.length) return null;
    const obj = hits[0].object;
    return { ...obj.userData, mesh: obj };
  }

  focusDomain(key) {
    const mesh = this.domainMesh.get(key);
    if (!mesh) return;
    this.controls.autoRotate = false;
    this._tween(mesh.position, 3.2);
  }

  highlightPapers(keys) {
    for (const [id, mesh] of this.paperMesh) {
      const active = keys.has(id);
      mesh.visible = active || keys.size === 0;
      mesh.material.emissiveIntensity = active ? 1.0 : 0.35;
    }
  }

  resetHighlight() {
    for (const [, mesh] of this.paperMesh) {
      mesh.material.emissiveIntensity = 0.55;
      mesh.visible = true;
    }
  }

  focusPaper(id) {
    const mesh = this.paperMesh.get(id);
    if (!mesh) return;
    this.controls.autoRotate = false;
    this._tween(mesh.position, 1.6);
  }

  _tween(target, dist) {
    const goal = target.clone().normalize().multiplyScalar(dist);
    const start = this.camera.position.clone();
    const t0 = performance.now();
    const step = () => {
      const t = Math.min(1, (performance.now() - t0) / 700);
      const k = t * t * (3 - 2 * t);
      this.camera.position.lerpVectors(start, goal, k);
      this.camera.lookAt(0, 0, 0);
      if (t < 1) requestAnimationFrame(step);
    };
    step();
  }

  _line(a, b, color, opacity) {
    const geo = new THREE.BufferGeometry().setFromPoints([a, b]);
    const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    const line = new THREE.Line(geo, mat);
    this.scene.add(line);
    this.lines.push(line);
    return line;
  }

  _fit() {
    // 仅基于领域/论文 mesh 计算包围盒(排除网格/坐标轴),保证零点居中
    const box = new THREE.Box3();
    for (const m of this.domainMesh.values()) box.expandByObject(m);
    for (const m of this.paperMesh.values()) box.expandByObject(m);
    if (box.isEmpty()) {
      this.camera.position.set(5.5, 3.2, 5.5);
      this.controls.target.set(0, 0, 0);
      return;
    }
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    this.camera.position.copy(center).add(new THREE.Vector3(size, size * 0.6, size));
    this.camera.lookAt(center);
    this.controls.target.copy(center);
  }

  _clear() {
    // 先清连线(渲染时残留会导致子领域外连线不消失)
    for (const line of this.lines) this.scene.remove(line);
    this.lines = [];
    for (const mesh of this.domainMesh.values()) this.scene.remove(mesh);
    for (const mesh of this.paperMesh.values()) this.scene.remove(mesh);
    this.domainMesh.clear();
    this.paperMesh.clear();
  }

  _resize() {
    const w = this.container.clientWidth || 800;
    const h = this.container.clientHeight || 500;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

// ===========================================================================
// 2D 渲染器(Canvas 2D,WebGL 不可用时的降级)
// ===========================================================================
class Map2DRenderer {
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks || {};
    this.colorMode = "view";
    this.focus = null;
    this.rotY = 0.6;
    this.rotX = 0.35;
    this.zoom = 1.0;
    this.panMode = false;
    this.offsetX = 0;
    this.offsetY = 0;
    this.data = { domains: [], papers: [] };
    this.highlight = null;

    this.canvas = document.createElement("canvas");
    this.canvas.style.width = "100%";
    this.canvas.style.height = "100%";
    this.canvas.style.display = "block";
    this.container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    if (!this.ctx) throw new Error("Canvas 2D 不可用");
    this.tooltip = new MapTooltip(this.container);

    this._bindEvents();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(this.container);
    this._draw();
  }

  _bindEvents() {
    const c = this.canvas;
    let dragging = false, lastX = 0, lastY = 0;
    c.addEventListener("pointerdown", (e) => {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      const rect = c.getBoundingClientRect();
      if (dragging) {
        if (this.panMode) {
          // 平移:移动视图中心
          this.offsetX += e.clientX - lastX;
          this.offsetY += e.clientY - lastY;
        } else {
          this.rotY += (e.clientX - lastX) * 0.01;
          this.rotX = Math.max(-1.2, Math.min(1.2, this.rotX + (e.clientY - lastY) * 0.01));
        }
        lastX = e.clientX; lastY = e.clientY;
        this._draw();
      } else {
        this._hover(e, e.clientX - rect.left, e.clientY - rect.top);
      }
    });
    c.addEventListener("pointerup", (e) => {
      dragging = false;
      if (Math.abs(e.clientX - lastX) < 4 && Math.abs(e.clientY - lastY) < 4) this._click(e);
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoom = Math.max(0.4, Math.min(6, this.zoom * (e.deltaY > 0 ? 0.9 : 1.1)));
      this._draw();
    }, { passive: false });
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("dblclick", (e) => {
      const hit = this._pickAt(e);
      if (hit && hit.type === "domain" && this.callbacks.onEnterDomain) {
        this.callbacks.onEnterDomain(hit.key);
      }
    });
  }

  // -- 导航 ------------------------------------------------------------
  setAutoRotate() {}  // 2D 无自动旋转
  setPanMode(on) {
    this.panMode = on;
    this.canvas.style.cursor = on ? "move" : "grab";
  }
  setFocus(chain) {
    this.focus = chain && chain.length ? { key: chain[chain.length - 1], chain } : null;
    this._draw();
  }
  goHome() {
    this.focus = null;
    this.rotY = 0.6;
    this.rotX = 0.35;
    this.zoom = 1.0;
    this.offsetX = 0;
    this.offsetY = 0;
    this._draw();
  }
  inFocus() { return !!this.focus; }

  /** 子领域视图数据(与 3D 同公式) */
  _viewData() {
    if (!this.focus) return this.data;
    const prefix = this.focus.key + ".";
    const centerD = this.data.domains.find((d) => d.key === this.focus.key);
    const centerQ = centerD ? qToThree(centerD.q) : new THREE.Quaternion();
    const scale = this.focus.key.split(".").length <= 1 ? 2.6 : 2.2;
    const domains = this.data.domains
      .filter((d) => d.key === this.focus.key || d.key.startsWith(prefix))
      .map((d) => ({
        ...d,
        xyz: d.key === this.focus.key ? [0, 0, 0] : localXYZ(d.q, centerQ, scale),
      }));
    const inView = new Set(domains.map((d) => d.key));
    for (const d of domains) {
      if (d.parent && !inView.has(d.parent)) d.parent = null;  // 域外父节点断开连线
    }
    const papers = this.data.papers
      .filter((p) => p.domain_key === this.focus.key || p.domain_key.startsWith(prefix))
      .map((p) => ({ ...p, xyz: localXYZ(p.q, centerQ, scale) }));
    return { domains, papers };
  }

  _project(x, y, z) {
    let x1 = x * Math.cos(this.rotY) + z * Math.sin(this.rotY);
    let z1 = -x * Math.sin(this.rotY) + z * Math.cos(this.rotY);
    let y1 = y * Math.cos(this.rotX) - z1 * Math.sin(this.rotX);
    let z2 = y * Math.sin(this.rotX) + z1 * Math.cos(this.rotX);
    const w = this.canvas.clientWidth || 800;
    const h = this.canvas.clientHeight || 500;
    const s = Math.min(w, h) * 0.32 * this.zoom;
    return { sx: w / 2 + x1 * s + this.offsetX, sy: h / 2 - y1 * s + this.offsetY, depth: z2 };
  }

  _paperColor(p) {
    if (this.colorMode === "category") return categoryColor2d(p.category);
    const root = String(p.domain_key || "").split(".")[0];
    return "#" + (ROOT_COLORS[root] !== undefined ? ROOT_COLORS[root] : ROOT_COLORS.other).toString(16).padStart(6, "0");
  }

  setColorMode(mode) {
    this.colorMode = mode;
    this._draw();
  }

  render(mapData) {
    this.data = mapData;
    this._draw();
  }

  _draw() {
    const ctx = this.ctx;
    const view = this._viewData();
    const w = this.canvas.clientWidth || 800;
    const h = this.canvas.clientHeight || 500;
    this.canvas.width = w * (window.devicePixelRatio || 1);
    this.canvas.height = h * (window.devicePixelRatio || 1);
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);

    ctx.fillStyle = "#0d1117";
    ctx.fillRect(0, 0, w, h);

    // 网格
    ctx.strokeStyle = "#1a2028";
    ctx.lineWidth = 1;
    for (let i = -5; i <= 5; i++) {
      const a = this._project(i * 0.8, 0, -4), b = this._project(i * 0.8, 0, 4);
      const c = this._project(-4, 0, i * 0.8), d = this._project(4, 0, i * 0.8);
      ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(c.sx, c.sy); ctx.lineTo(d.sx, d.sy); ctx.stroke();
    }

    const domainByKey = new Map(view.domains.map((d) => [d.key, d]));
    const domainPos = new Map(view.domains.map((d) => [d.key, this._project(...d.xyz)]));

    // 领域父子连线
    ctx.strokeStyle = "#2d333b";
    for (const d of view.domains) {
      if (!d.parent || !domainByKey.has(d.parent)) continue;
      const a = domainPos.get(d.key), b = domainPos.get(d.parent);
      ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
    }

    // 论文 → 领域关联连线
    ctx.strokeStyle = "rgba(63,185,80,0.10)";
    for (const p of view.papers) {
      const dp = domainPos.get(p.domain_key);
      if (!dp) continue;
      const pp = this._project(...p.xyz);
      ctx.beginPath(); ctx.moveTo(pp.sx, pp.sy); ctx.lineTo(dp.sx, dp.sy); ctx.stroke();
    }

    // 论文点
    const papers = [...(view.papers || [])].sort((p, q) => {
      return this._project(...p.xyz).depth - this._project(...q.xyz).depth;
    });
    for (const p of papers) {
      const pos = this._project(...p.xyz);
      const highlighted = this.highlight && this.highlight.has(p.id);
      const dimmed = this.highlight && !this.highlight.has(p.id);
      if (dimmed) continue;
      ctx.globalAlpha = highlighted ? 1.0 : 0.85;
      ctx.fillStyle = this._paperColor(p);
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, highlighted ? 4 : 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1.0;
    }

    // 领域节点(半径 ∝ 论文数)+ 标签
    const maxCount = Math.max(1, ...view.domains.map((d) => d.paper_count || 0));
    for (const d of view.domains) {
      const pos = domainPos.get(d.key);
      const level = Math.min(d.level || 1, 3);
      ctx.fillStyle = LEVEL_COLORS[level];
      ctx.globalAlpha = d.heat === "hot" ? 1.0 : d.heat === "cold" ? 0.35 : 0.85;
      const r = 3.5 + 3 * Math.sqrt((d.paper_count || 0) / maxCount);
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, Math.min(r, 9), 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1.0;
      if (d.level <= 2) {
        ctx.fillStyle = d.heat === "cold" ? "#484f58" : d.level <= 1 ? "#a5d6ff" : "#8b949e";
        ctx.font = d.level <= 1 ? "11px sans-serif" : "9px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText((d.heat === "hot" ? "🔥 " : "") + d.name, pos.sx, pos.sy - 8);
      }
    }

    // 模式角标
    ctx.fillStyle = "#484f58";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`2D 兼容模式 · ${this.colorMode === "view" ? "视角着色" : "学科着色"} · 拖动旋转 · 滚轮缩放`, 10, 14);
  }

  _screenPositions() {
    const view = this._viewData();
    const map = [];
    for (const d of view.domains) map.push({ ...d, pos: this._project(...d.xyz) });
    for (const p of view.papers) map.push({ ...p, pos: this._project(...p.xyz) });
    return map;
  }

  _pickAt(e, offsetX = 0, offsetY = 0) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left - offsetX, my = e.clientY - rect.top - offsetY;
    let best = null, bestDist = 14;
    for (const item of this._screenPositions()) {
      const dist = Math.hypot(item.pos.sx - mx, item.pos.sy - my);
      if (dist < bestDist) { bestDist = dist; best = item; }
    }
    return best;
  }

  _hover(e, cx, cy) {
    const hit = this._pickAt(e);
    this.canvas.style.cursor = hit ? "pointer" : "grab";
    if (hit) {
      if (hit.type === "paper") {
        this.tooltip.show(hit.title, cx, cy);
      } else {
        this.tooltip.show(`${hit.name} · ${hit.paper_count} 篇${hit.heat === "hot" ? " · 🔥热门" : ""}`, cx, cy);
      }
      if (this.callbacks.onHover) this.callbacks.onHover(
        hit.type === "paper"
          ? { type: "paper", id: hit.id, title: hit.title }
          : { type: "domain", key: hit.key, name: hit.name, paper_count: hit.paper_count, heat: hit.heat }
      );
    } else {
      this.tooltip.hide();
    }
    this._draw();
  }

  _click(e) {
    const hit = this._pickAt(e);
    if (!hit) return;
    if (hit.type === "paper" && this.callbacks.onPaperClick) this.callbacks.onPaperClick(hit.id);
    else if (hit.type === "domain" && this.callbacks.onDomainClick) this.callbacks.onDomainClick(hit.key);
  }

  focusDomain(key) { this.highlight = null; this._draw(); }
  focusPaper(id) { this.highlight = new Set([id]); this._draw(); }
  highlightPapers(keys) { this.highlight = keys.size ? new Set(keys) : null; this._draw(); }
  resetHighlight() { this.highlight = null; this._draw(); }

  _resize() { this._draw(); }
}
