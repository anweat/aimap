/* ===== 领域树:原生 DOM 渲染(ES Module,零依赖)===== */
export class DomainTree {
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks || {};
    this.nodes = [];
    this.selected = null;
  }

  render(nodes) {
    this.nodes = nodes;
    this.container.innerHTML = "";
    const root = this._buildHierarchy(nodes);
    this._renderNode(root, 0);
  }

  _buildHierarchy(nodes) {
    const byKey = {};
    for (const n of nodes) byKey[n.key] = { ...n, children: [] };
    const roots = [];
    for (const n of nodes) {
      if (n.parent && byKey[n.parent]) byKey[n.parent].children.push(byKey[n.key]);
      else roots.push(byKey[n.key]);
    }
    return roots.length === 1 ? roots[0] : { key: "_root", name: "AI 领域", children: roots };
  }

  _renderNode(node, depth) {
    const row = document.createElement("div");
    row.className = "tree-node";
    row.style.paddingLeft = `${8 + depth * 16}px`;
    row.title = node.key + (node.description ? `\n${node.description}` : "");

    const tw = document.createElement("span");
    tw.className = "tw";
    tw.textContent = node.children && node.children.length ? "▾" : "·";
    row.appendChild(tw);

    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = this._colorFor(node);
    row.appendChild(dot);

    const name = document.createElement("span");
    const heatMark = node.heat === "hot" ? "🔥 " : "";
    name.textContent = (node.created_by === "ai" ? "✨ " : "") + heatMark + node.name;
    row.appendChild(name);
    if (node.heat === "cold") row.style.opacity = "0.55";

    const cnt = document.createElement("span");
    cnt.className = "cnt";
    cnt.textContent = node.paper_count ? `📄 ${node.paper_count}` : "";
    row.appendChild(cnt);

    row.addEventListener("click", () => {
      this.select(node.key);
      if (this.callbacks.onSelect) this.callbacks.onSelect(node);
    });
    this.container.appendChild(row);

    for (const child of node.children || []) this._renderNode(child, depth + 1);
  }

  select(key) {
    this.selected = key;
    for (const el of this.container.querySelectorAll(".tree-node")) {
      el.classList.toggle("selected", el.title === key);
    }
  }

  _colorFor(node) {
    const level = node.level || 0;
    const colors = ["#58a6ff", "#79c0ff", "#a5d6ff", "#d2e8ff"];
    return colors[Math.min(level, colors.length - 1)];
  }
}
