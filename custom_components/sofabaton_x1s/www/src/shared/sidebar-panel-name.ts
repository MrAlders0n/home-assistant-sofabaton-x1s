// The "Sofabaton X" sidebar panel's name: one name for everyone who sees
// the panel (Home Assistant panels carry a single title). The backend
// applies the same rule (ui_settings_store.py normalize_sidebar_panel_title).

export const SIDEBAR_PANEL_NAME_MAX = 30;

// C0 and C1 control characters.
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]+/g;

/** What the rename field keeps as the user types: no control characters, the length cap. */
export function sanitizeSidebarPanelName(value: unknown): string {
  const text = String(value ?? "").replace(/[\t\n\r]/g, " ").replace(CONTROL_CHARS, "");
  return Array.from(text).slice(0, SIDEBAR_PANEL_NAME_MAX).join("");
}

/** The name as stored: whitespace collapsed to single spaces, trimmed. */
export function normalizeSidebarPanelName(value: unknown): string {
  return sanitizeSidebarPanelName(value).replace(/\s+/g, " ").trim();
}
