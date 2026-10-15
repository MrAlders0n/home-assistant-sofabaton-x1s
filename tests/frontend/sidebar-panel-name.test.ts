import test from "node:test";
import assert from "node:assert/strict";
import { html } from "lit";
import {
  normalizeSidebarPanelName,
  sanitizeSidebarPanelName,
  SIDEBAR_PANEL_NAME_MAX,
} from "../../custom_components/sofabaton_x1s/www/src/shared/sidebar-panel-name";
import { phraseWithName } from "../../custom_components/sofabaton_x1s/www/src/components/phrase-name";
import { TOOLS_CARD_STRINGS } from "../../custom_components/sofabaton_x1s/www/src/strings";
import { sidebarPanelTitle } from "../../custom_components/sofabaton_x1s/www/src/shared/utils/control-panel-selectors";
import type { ControlPanelSnapshot } from "../../custom_components/sofabaton_x1s/www/src/shared/ha-context";

test("the rename field drops control characters and caps the length in characters", () => {
  assert.equal(sanitizeSidebarPanelName("Living\troom\u0007"), "Living room");
  // Typing keeps a trailing space; only saving trims.
  assert.equal(sanitizeSidebarPanelName("Living "), "Living ");
  assert.equal(sanitizeSidebarPanelName("x".repeat(40)).length, SIDEBAR_PANEL_NAME_MAX);
  // Characters, not UTF-16 units, like the backend's len().
  assert.equal(Array.from(sanitizeSidebarPanelName("\u{1F4FA}".repeat(40))).length, SIDEBAR_PANEL_NAME_MAX);
});

test("the saved name collapses whitespace and trims, like the backend", () => {
  assert.equal(normalizeSidebarPanelName("  Living \t room \u0007 remote "), "Living room remote");
  assert.equal(normalizeSidebarPanelName("   "), "");
});

test("the Settings tab name falls back to the product name", () => {
  const snapshot = (title?: string) => ({ state: { sidebar_panel_title: title } }) as unknown as ControlPanelSnapshot;
  assert.equal(sidebarPanelTitle(snapshot("Remote")), "Remote");
  assert.equal(sidebarPanelTitle(snapshot("  ")), TOOLS_CARD_STRINGS.sidebarPanel.title);
  assert.equal(sidebarPanelTitle(snapshot(undefined)), TOOLS_CARD_STRINGS.sidebarPanel.title);
});

test("phraseWithName puts the chip and what follows it where the locale puts the name", () => {
  const pencil = html`<button>edit</button>`;
  const result = phraseWithName((name) => `Add ${name} to the sidebar`, "Remote", pencil);
  // head, the chip's name, what rides behind the chip, tail.
  assert.deepEqual(result.values, ["Add ", "Remote", pencil, " to the sidebar"]);
  assert.match(result.strings.join("|"), /<span class="phrase-name">\|<\/span>/);

  // zh puts the name first and wraps it in quotes: the split follows.
  const zh = phraseWithName((name) => `将“${name}”添加`, "遥控器");
  assert.deepEqual(zh.values.filter((value) => typeof value === "string"), ["将“", "遥控器", "”添加"]);
});
