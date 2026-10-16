import test from "node:test";
import assert from "node:assert/strict";
import { rebaseBundle } from "../../custom_components/sofabaton_x1s/www/src/tabs/bundle-rebase";
import type { BackupBundlePayload } from "../../custom_components/sofabaton_x1s/www/src/shared/ha-context";

function bundle(overrides: Partial<Record<string, unknown>> = {}): BackupBundlePayload {
  return {
    kind: "hub_bundle",
    schema_version: 5,
    captured_at: "t0",
    devices: [
      { device: { device_id: 1, name: "TV" }, commands: [{ command_id: 1, name: "Power" }] },
      { device: { device_id: 2, name: "Amp" }, commands: [] },
    ],
    activities: [
      {
        device: { device_id: 101, name: "Watch TV" },
        favorite_slots: [{ button_id: 1, device_id: 1, command_id: 1 }],
        macros: [],
      },
    ],
    ...overrides,
  } as unknown as BackupBundlePayload;
}

function activity(b: BackupBundlePayload) {
  return (b.activities as unknown as Array<Record<string, any>>)[0];
}

test("an unchanged editor takes the new capture whole", () => {
  const base = bundle();
  const theirs = bundle({ captured_at: "t1" });
  activity(theirs).device.name = "Watch TV 2";
  const { bundle: out, overlap } = rebaseBundle(base, structuredClone(base), theirs);
  assert.deepEqual(out, theirs);
  assert.equal(overlap, false);
});

test("an edit here and a different change elsewhere both survive", () => {
  const base = bundle();
  const mine = structuredClone(base);
  activity(mine).favorite_slots.push({ button_id: 2, device_id: 2, command_id: 5 });
  const theirs = bundle({ captured_at: "t1" });
  activity(theirs).device.name = "Renamed elsewhere";
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.equal(activity(out).device.name, "Renamed elsewhere");
  assert.equal(activity(out).favorite_slots.length, 2);
  assert.equal(out.captured_at, "t1");
  assert.equal(overlap, false);
});

test("the same field changed on both sides keeps the user's version and says so", () => {
  const base = bundle();
  const mine = structuredClone(base);
  activity(mine).device.name = "Mine";
  const theirs = bundle();
  activity(theirs).device.name = "Theirs";
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.equal(activity(out).device.name, "Mine");
  assert.equal(overlap, true);
});

test("the same change on both sides is no overlap", () => {
  const base = bundle();
  const mine = structuredClone(base);
  activity(mine).device.name = "Same";
  const theirs = bundle();
  activity(theirs).device.name = "Same";
  const { overlap } = rebaseBundle(base, mine, theirs);
  assert.equal(overlap, false);
});

test("a device created elsewhere appears and one deleted elsewhere goes", () => {
  const base = bundle();
  const mine = structuredClone(base);
  activity(mine).device.name = "Mine";
  const theirs = bundle();
  (theirs.devices as unknown as unknown[]).splice(1, 1);
  (theirs.devices as unknown as unknown[]).push({ device: { device_id: 7, name: "New" }, commands: [] });
  const { bundle: out } = rebaseBundle(base, mine, theirs);
  const ids = (out.devices as unknown as Array<{ device: { device_id: number } }>).map((row) => row.device.device_id);
  assert.deepEqual(ids, [1, 7]);
  assert.equal(activity(out).device.name, "Mine");
});

test("a partial sync: what landed is no longer a diff, what did not still is", () => {
  // The user added two favorites; the sync landed the first, then failed.
  const base = bundle();
  const mine = structuredClone(base);
  activity(mine).device.name = "Renamed";
  activity(mine).favorite_slots.push({ button_id: 2, device_id: 2, command_id: 5 });
  const landed = structuredClone(base);
  activity(landed).device.name = "Renamed";
  const { bundle: out, overlap } = rebaseBundle(base, mine, landed);
  assert.equal(overlap, false);
  assert.equal(activity(out).device.name, "Renamed");
  assert.equal(activity(out).favorite_slots.length, 2);
  // Against the new baseline (what landed) only the favorite is left to write.
  assert.equal(activity(landed).device.name, activity(out).device.name);
  assert.notDeepEqual(activity(landed).favorite_slots, activity(out).favorite_slots);
});
