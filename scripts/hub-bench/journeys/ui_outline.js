// Debug helper: an indented outline of the Sofabaton sidebar panel's DOM
// (through shadow roots): tag.classes [disabled] "own text". Icons left out.
(maxLines) => {
  const out = [];
  const skip = new Set(["ha-svg-icon", "svg", "path", "g"]);
  const walk = (node, depth) => {
    if (depth > 60 || out.length > (maxLines || 3000)) return;
    for (const el of node.children || []) {
      const tag = el.tagName.toLowerCase();
      if (skip.has(tag)) continue;
      const cls = (el.getAttribute && el.getAttribute("class")) || "";
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").slice(0, 70);
      const attrs = [];
      if (el.hasAttribute("disabled")) attrs.push("disabled");
      for (const a of ["aria-label", "title", "placeholder", "type", "value", "role"]) {
        if (el.hasAttribute(a) && el.getAttribute(a)) attrs.push(`${a}=${el.getAttribute(a).slice(0, 40)}`);
      }
      if (tag !== "ha-icon" && (tag.includes("-") || cls || own || attrs.length)) {
        out.push(`${"  ".repeat(depth)}${tag}${cls ? "." + cls.trim().split(/\s+/).join(".") : ""}${attrs.length ? " [" + attrs.join(" ") + "]" : ""}${own ? '  "' + own + '"' : ""}`);
      }
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
      walk(el, depth + 1);
    }
  };
  const find = (root) => {
    if (!root) return null;
    const hit = root.querySelector("sofabaton-x-panel");
    if (hit) return hit;
    for (const e of root.querySelectorAll("*")) if (e.shadowRoot) { const x = find(e.shadowRoot); if (x) return x; }
    return null;
  };
  const p = find(document);
  if (!p) return "no panel";
  walk(p.shadowRoot || p, 0);
  return out.join("\n");
}
