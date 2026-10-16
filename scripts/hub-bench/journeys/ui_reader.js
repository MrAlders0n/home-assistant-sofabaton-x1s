// What the Sofabaton sidebar panel shows right now, read through the shadow
// roots. Pure reading: nothing here clicks or changes the page.
// Returns null until the panel has rendered.
() => {
  const findDeep = (root, selector) => {
    if (!root) return null;
    const hit = root.querySelector(selector);
    if (hit) return hit;
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) {
        const found = findDeep(el.shadowRoot, selector);
        if (found) return found;
      }
    }
    return null;
  };
  const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
  const isDisabled = (el) => !!el && (
    el.disabled === true
    || el.hasAttribute("disabled")
    || el.getAttribute("aria-disabled") === "true"
    || /\b(disabled|is-disabled|busy|inert)\b/.test(el.getAttribute("class") || "")
  );

  const panel = findDeep(document, "sofabaton-x-panel");
  const proot = panel && (panel.shadowRoot || panel);
  if (!proot) return null;
  const cp = proot.querySelector("sofabaton-control-panel");
  const root = cp && (cp.shadowRoot || cp);
  const out = {
    hub: text(proot.querySelector(".hub .name")),
    view: text(proot.querySelector(".tabs .tab.active .label")),
    tab: null, sub: null, counts: {}, entities: [], controls: [], dock: null, notices: [],
  };
  if (!root) return out;
  out.tab = text(root.querySelector(".tab-btn.active .tab-btn-label"));
  for (const btn of root.querySelectorAll(".secondary-tab-btn")) {
    const label = text(btn.querySelector(".secondary-tab-btn-label"));
    out.counts[label] = Number(text(btn.querySelector(".secondary-tab-btn-count")) || NaN);
    if (btn.classList.contains("active")) out.sub = label;
  }
  for (const block of root.querySelectorAll(".entity-block")) {
    const badge = block.querySelectorAll(".id-badge span");
    out.entities.push({
      name: text(block.querySelector(".entity-name-label")),
      id: Number(text(badge[badge.length - 1]) || NaN),
      count: text(block.querySelector(".entity-count")),
      buttons: [...block.querySelectorAll(".entity-meta button")].map((b) => isDisabled(b)),
    });
  }
  // Every write-capable control on the visible tab, by label.
  const controls = root.querySelectorAll(
    ".cache-footer-btn, .refresh-action button, .entity-meta button, .tab-btn, .secondary-tab-btn",
  );
  for (const c of controls) {
    out.controls.push({
      label: text(c) || c.getAttribute("aria-label") || c.getAttribute("title") || c.className,
      disabled: isDisabled(c),
      cls: c.getAttribute("class") || "",
    });
  }
  out.dock = text(root.querySelector(".card-bottom-dock-center"));
  // "Cache was updated externally. Refresh to see latest data."
  out.staleBanner = !!findDeep(root, ".stale-banner");
  out.dockFull = text(root.querySelector(".card-bottom-dock"));
  for (const n of root.querySelectorAll(".blocked-state, .card-blocked-state, .guard-title, .dock-notice, .notice")) {
    out.notices.push(text(n));
  }
  return out;
}
