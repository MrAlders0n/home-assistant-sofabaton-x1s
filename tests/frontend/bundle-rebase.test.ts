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

// -- lists merged row by row (server panel live check, X1S, 2026-10-16) ---------
//
// The editor had "Mute" renamed to "Mute A" while another client renamed
// "Input" to "Input B" on the same device. With the command list taken
// whole, the user's list won and the next Sync wrote "Input" back.

function deviceBundle(commands: Array<[number, string]>, extra: Record<string, unknown> = {}): BackupBundlePayload {
  return {
    kind: "hub_bundle",
    schema_version: 5,
    devices: [{
      device: { device_id: 10, name: "Amp" },
      commands: commands.map(([command_id, name]) => ({ command_id, name })),
      button_bindings: [],
      macros: [],
      ...extra,
    }],
    activities: [],
  } as unknown as BackupBundlePayload;
}

function commandNames(b: BackupBundlePayload): Record<number, string> {
  const rows = (b.devices as unknown as Array<{ commands: Array<{ command_id: number; name: string }> }>)[0].commands;
  return Object.fromEntries(rows.map((row) => [row.command_id, row.name]));
}

test("renames of two different commands both survive", () => {
  const base = deviceBundle([[1, "Mute"], [2, "Input"], [3, "Power"]]);
  const mine = deviceBundle([[1, "Mute A"], [2, "Input"], [3, "Power"]]);
  const theirs = deviceBundle([[1, "Mute"], [2, "Input B"], [3, "Power"]]);
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.deepEqual(commandNames(out), { 1: "Mute A", 2: "Input B", 3: "Power" });
  assert.equal(overlap, false);
});

test("a rename of the same command on both sides keeps the user's and says so", () => {
  const base = deviceBundle([[1, "Mute"], [2, "Input"]]);
  const mine = deviceBundle([[1, "Mute A"], [2, "Input"]]);
  const theirs = deviceBundle([[1, "Mute C"], [2, "Input"]]);
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.deepEqual(commandNames(out), { 1: "Mute A", 2: "Input" });
  assert.equal(overlap, true);
});

test("a command deleted elsewhere goes while the user's rename of another stays", () => {
  const base = deviceBundle([[1, "Mute"], [2, "Input"], [3, "Power"]]);
  const mine = deviceBundle([[1, "Mute A"], [2, "Input"], [3, "Power"]]);
  const theirs = deviceBundle([[1, "Mute"], [3, "Power"]]);
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.deepEqual(commandNames(out), { 1: "Mute A", 3: "Power" });
  assert.equal(overlap, false);
});

test("bindings on different buttons from both sides both land", () => {
  const binding = (button_id: number, command_id: number) => ({ button_id, device_id: 10, command_id });
  const base = deviceBundle([[1, "Mute"]], { button_bindings: [binding(0xB6, 1)] });
  const mine = deviceBundle([[1, "Mute"]], { button_bindings: [binding(0xB6, 1), binding(0xBE, 1)] });
  const theirs = deviceBundle([[1, "Mute"]], { button_bindings: [binding(0xB6, 1), binding(0xBF, 1)] });
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  const buttons = ((out.devices as unknown as Array<{ button_bindings: Array<{ button_id: number }> }>)[0].button_bindings)
    .map((row) => row.button_id).sort((a, b) => a - b);
  assert.deepEqual(buttons, [0xB6, 0xBE, 0xBF]);
  assert.equal(overlap, false);
});

test("a favorite reordered here and renamed elsewhere keeps both", () => {
  // The editor renumbers favorite button_ids on a reorder; favorites are
  // matched by the command they point at.
  const fav = (button_id: number, device_id: number, command_id: number, name: string) =>
    ({ button_id, device_id, command_id, name });
  const base = bundle();
  (base.activities as any)[0].favorite_slots = [fav(1, 1, 1, "Power"), fav(2, 2, 5, "Input")];
  (base.activities as any)[0].favorites_order = [1, 2];
  const mine = structuredClone(base);
  (mine.activities as any)[0].favorite_slots = [fav(1, 2, 5, "Input"), fav(2, 1, 1, "Power")];
  (mine.activities as any)[0].favorites_order = [1, 2];
  const theirs = structuredClone(base);
  (theirs.activities as any)[0].favorite_slots[1].name = "HDMI";
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  const slots = (out.activities as any)[0].favorite_slots as Array<ReturnType<typeof fav>>;
  assert.deepEqual(slots, [fav(1, 2, 5, "HDMI"), fav(2, 1, 1, "Power")]);
  assert.equal(overlap, false);
});

test("a macro edited here and another added elsewhere both stay", () => {
  const macro = (button_id: number, name: string) => ({ button_id, name, steps: [] });
  const base = bundle();
  (base.activities as any)[0].macros = [macro(2, "Movie")];
  const mine = structuredClone(base);
  (mine.activities as any)[0].macros = [macro(2, "Movie night")];
  const theirs = structuredClone(base);
  (theirs.activities as any)[0].macros = [macro(2, "Movie"), macro(3, "Party")];
  const { bundle: out, overlap } = rebaseBundle(base, mine, theirs);
  assert.deepEqual((out.activities as any)[0].macros, [macro(2, "Movie night"), macro(3, "Party")]);
  assert.equal(overlap, false);
});
