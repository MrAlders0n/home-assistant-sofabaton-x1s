import test from "node:test";
import assert from "node:assert/strict";

import {
  entityIconIds,
  entityIconPath,
  iconHubLine,
} from "../../custom_components/sofabaton_x1s/www/src/shared/entity-icons";
import { X1_ICON_PATHS, X2_ICON_PATHS } from "../../custom_components/sofabaton_x1s/www/src/shared/x2-icons";

test("iconHubLine reads the line out of a hub version", () => {
  assert.equal(iconHubLine("X1"), "X1");
  assert.equal(iconHubLine("x1s"), "X1S");
  assert.equal(iconHubLine(" Sofabaton X1S "), "X1S");
  assert.equal(iconHubLine("X2"), "X2");
  assert.equal(iconHubLine(""), null);
  assert.equal(iconHubLine(null), null);
  assert.equal(iconHubLine("U2"), null);
});

test("entityIconIds offers each line's own set", () => {
  assert.deepEqual(entityIconIds("X1"), Array.from({ length: 25 }, (_, i) => i + 1));
  assert.deepEqual(entityIconIds("X1S"), Array.from({ length: 16 }, (_, i) => i + 1));
  assert.deepEqual(entityIconIds("X2"), Array.from({ length: 45 }, (_, i) => i + 1));
  assert.deepEqual(entityIconIds(undefined), []);
});

test("X1S and X2 share ids: the X1S shows X2 paths 1-16", () => {
  for (const id of entityIconIds("X1S")) assert.equal(entityIconPath("X1S", id), X2_ICON_PATHS[id]);
  for (const id of entityIconIds("X2")) assert.equal(entityIconPath("X2", id), X2_ICON_PATHS[id]);
});

test("every X1 id resolves: its own drawing, else the mapped X2 one", () => {
  const x2Paths = new Set(Object.values(X2_ICON_PATHS));
  for (const id of entityIconIds("X1")) {
    const path = entityIconPath("X1", id);
    if (X1_ICON_PATHS[id]) assert.equal(path, X1_ICON_PATHS[id], `X1 icon ${id}`);
    else assert.ok(path && x2Paths.has(path), `X1 icon ${id} maps to an X2 path`);
  }
  assert.equal(entityIconPath("X1", 1), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("X1", 20), X2_ICON_PATHS[9]);
  assert.equal(entityIconPath("X1", 23), X2_ICON_PATHS[10]);
});

test("ids outside a line's set show icon 1, as the app does", () => {
  assert.equal(entityIconPath("X1S", 17), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("X2", 46), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("X2", 0), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("X2", 2.5), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("X1", 26), X2_ICON_PATHS[1]);
  assert.equal(entityIconPath("U2", 3), null);
});
