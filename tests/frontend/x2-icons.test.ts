import test from "node:test";
import assert from "node:assert/strict";

import { X2_ICON_PATHS } from "../../custom_components/sofabaton_x1s/www/src/shared/x2-icons";
import * as geometry from "../../scripts/x2-icons/geometry.mjs";
import { ICONS } from "../../scripts/x2-icons/icons.mjs";

test("x2 icon set covers ids 1-45", () => {
  assert.deepEqual(
    Object.keys(X2_ICON_PATHS).map(Number),
    Array.from({ length: 45 }, (_, i) => i + 1),
  );
});

test("x2 icon paths are single relative-path strings", () => {
  for (const [id, path] of Object.entries(X2_ICON_PATHS)) {
    assert.match(path, /^M[\d.]/, `icon ${id} starts with an absolute move`);
    assert.match(path, /^[MmLlHhVvCcQqAaZz\d.\s-]+$/, `icon ${id} uses path syntax only`);
    assert.ok(path.endsWith("z"), `icon ${id} closes its last subpath`);
  }
});

test("generated x2-icons.ts matches scripts/x2-icons (run npm run build:x2-icons)", () => {
  for (const [id, [name, draw]] of Object.entries(ICONS)) {
    assert.equal(X2_ICON_PATHS[Number(id)], geometry.build(draw(geometry)), `icon ${id} (${name}) is stale`);
  }
});
