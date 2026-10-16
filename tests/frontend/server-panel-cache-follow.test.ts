// The server panel follows the hub's cache (2026-10-16, the HA card's model):
// the version helpers, how a pushed or listed hub view lays over the one held,
// the coalescing timer, the draft carry-over helpers, and the device editor
// itself following a change made elsewhere (clean, dirty, overlapping,
// deleted), carrying a stored draft over, and its Sync after a 412 or a
// partly landed failure.

import assert from "node:assert/strict";
import test from "node:test";

import type { BackupBundlePayload } from "../../custom_components/sofabaton_x1s/www/src/shared/ha-context";
import { TOOLS_CARD_STRINGS } from "../../custom_components/sofabaton_x1s/www/src/strings";
import {
  Coalescer,
  isNewerVersion,
  loadedVersion,
  needsReload,
  reconcileHubView,
  versionAsLoaded,
} from "../../server-panel/src/cache-follow";
import type { ApiResponse, HubView, JobView, SnapshotDocument } from "../../server-panel/src/panel-api";
import type { Draft } from "../../server-panel/src/panel-store";
import { snapshotAsBundle } from "../../server-panel/src/views/device-editor-state";
import { SbPanelDeviceEditor, defineDeviceEditor } from "../../server-panel/src/views/device-editor";
import { draftSlot, entityDraftBase, entityElement, rebaseDraftOnto, withEntityElement } from "../../server-panel/src/views/entity-editor-state";

const v = (generation: number, snapshot_id = `s${generation}`, epoch = 1) => ({ epoch, generation, snapshot_id });

// -- versions ------------------------------------------------------------------------------------

test("a newer version is a newer proxy, or a later generation of the same one", () => {
  assert.equal(isNewerVersion(v(2), v(1)), true);
  assert.equal(isNewerVersion(v(1), v(2)), false);
  assert.equal(isNewerVersion(v(1), v(1)), false);
  assert.equal(isNewerVersion(v(0, "x", 2), v(99, "y", 1)), true, "a new epoch outranks any generation");
  assert.equal(isNewerVersion(v(1), null), true);
  assert.equal(isNewerVersion(null, v(1)), false);
});

test("rows reload only for another snapshot id that is not older than theirs", () => {
  const loaded = loadedVersion(v(3), { snapshot_id: "s3", engine_generation: 3 });
  assert.deepEqual(loaded, { epoch: 1, generation: 3, snapshotId: "s3" });
  assert.equal(needsReload(loaded, v(3)), false, "the same version");
  assert.equal(needsReload(loaded, v(5, "s3")), false, "the generation moved, the content did not");
  assert.equal(needsReload(loaded, v(5)), true);
  assert.equal(needsReload(loaded, v(2)), false, "an older report");
  assert.equal(needsReload(loaded, v(3, "other")), false, "an equal generation never reloads (no loop)");
  assert.equal(needsReload(loaded, v(0, "x", 2)), true, "a new proxy");
  assert.equal(needsReload(loaded, v(9, "x", 0)), false, "an older proxy");
  assert.equal(needsReload(loaded, null), false, "a server without versions: the job ends still reload");
  assert.equal(needsReload(null, v(1)), true);
  // Read before any version was known: the next report reloads once.
  assert.equal(needsReload(loadedVersion(null, { snapshot_id: "s3", engine_generation: 3 }), v(4)), true);
  assert.equal(needsReload(versionAsLoaded(v(4)), v(4)), false);
  assert.equal(needsReload(versionAsLoaded(v(4)), v(6)), true);
});

// -- one hub view over another ----------------------------------------------------------------------

function hub(overrides: Partial<HubView> = {}): HubView {
  return {
    hub_id: "a", enabled: true, config: { host: "h" }, added_at: "t", last_seen: null,
    status: null, active_job: null, last_job: null, ...overrides,
  } as HubView;
}

function job(id: string, status: JobView["status"], created = "2026-10-16T10:00:00Z", steps?: number): JobView {
  return {
    job_id: id, hub_id: "a", kind: "refresh", status, cancellable: false, created_at: created,
    started_at: null, finished_at: null, progress: steps === undefined ? null : ({ completed_steps: steps } as JobView["progress"]),
    result: null, error: null,
  };
}

test("a view never takes back what the job events said", () => {
  // Built before the job started: the job stays running.
  assert.equal(reconcileHubView(hub({ active_job: job("j1", "running") }), hub()).active_job?.job_id, "j1");
  // It knows the end: the job ends.
  const ended = reconcileHubView(hub({ active_job: job("j1", "running") }), hub({ last_job: job("j1", "done") }));
  assert.equal(ended.active_job, null);
  // A later job that already ended also means the first one is over.
  const later = reconcileHubView(hub({ active_job: job("j1", "running") }), hub({ last_job: job("j2", "done", "2026-10-16T11:00:00Z") }));
  assert.equal(later.active_job, null);
  // Built before the end the panel saw: still shows it running.
  const stale = reconcileHubView(hub({ last_job: job("j1", "done") }), hub({ active_job: job("j1", "running") }));
  assert.equal(stale.active_job, null);
  assert.equal(stale.last_job?.job_id, "j1");
  // An older job still running in the view, a newer one known: the newer one runs.
  const older = reconcileHubView(hub({ active_job: job("j2", "running", "2026-10-16T11:00:00Z") }), hub({ active_job: job("j1", "running") }));
  assert.equal(older.active_job?.job_id, "j2");
  // The same job: the later report of it (further along).
  const progress = reconcileHubView(hub({ active_job: job("j1", "running", undefined, 4) }), hub({ active_job: job("j1", "running", undefined, 2) }));
  assert.equal(progress.active_job?.progress?.completed_steps, 4);
  // The last job never goes back to an older one.
  const back = reconcileHubView(hub({ last_job: job("j2", "done", "2026-10-16T11:00:00Z") }), hub({ last_job: job("j1", "done") }));
  assert.equal(back.last_job?.job_id, "j2");
  // A new job shown by the view is believed.
  assert.equal(reconcileHubView(hub(), hub({ active_job: job("j3", "queued") })).active_job?.job_id, "j3");
});

test("the cache version only moves forward, and a view without one clears it", () => {
  assert.deepEqual(reconcileHubView(hub({ cache: v(5) }), hub({ cache: v(4) })).cache, v(5));
  assert.deepEqual(reconcileHubView(hub({ cache: v(5) }), hub({ cache: v(6) })).cache, v(6));
  assert.equal(reconcileHubView(hub({ cache: v(5) }), hub({ cache: null })).cache, null);
  const incoming = hub({ cache: v(6) });
  assert.equal(reconcileHubView(hub({ cache: v(5) }), incoming), incoming, "nothing to correct: the same object");
});

// -- coalescing -----------------------------------------------------------------------------------

class Clock {
  now = 0;
  private _timers: { id: number; at: number; fn: () => void }[] = [];
  private _seq = 0;
  setTimer = (fn: () => void, ms: number): unknown => {
    const id = ++this._seq;
    this._timers.push({ id, at: this.now + ms, fn });
    return id;
  };
  clearTimer = (handle: unknown): void => {
    this._timers = this._timers.filter((t) => t.id !== handle);
  };
  advance(ms: number): void {
    const until = this.now + ms;
    for (;;) {
      const next = this._timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      this._timers = this._timers.filter((t) => t.id !== next.id);
      this.now = next.at;
      next.fn();
    }
    this.now = until;
  }
}

test("the coalescer runs once after a quiet spell, and no later than the max wait while pokes keep coming", () => {
  const clock = new Clock();
  const runs: number[] = [];
  const c = new Coalescer(() => runs.push(clock.now), { debounceMs: 250, maxWaitMs: 1500, now: () => clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  c.poke();
  clock.advance(100);
  c.poke();
  clock.advance(249);
  assert.deepEqual(runs, []);
  clock.advance(1);
  assert.deepEqual(runs, [350]);
  // A steady stream every 100 ms: it still runs, at 1.5 s.
  runs.length = 0;
  const start = clock.now;
  for (let i = 0; i < 25; i++) {
    c.poke();
    clock.advance(100);
  }
  assert.ok(runs.length >= 1 && runs[0] - start <= 1500, `ran at ${runs.map((t) => t - start)}`);
  c.cancel();
  const count = runs.length;
  clock.advance(5000);
  assert.equal(runs.length, count, "cancel drops the pending run");
});

// -- draft carry-over helpers ---------------------------------------------------------------------

function doc(id: string, generation: number, tv: Record<string, unknown> = {}, extra: Record<string, unknown>[] = []): SnapshotDocument {
  return {
    snapshot_id: id,
    captured_at: "t",
    engine_generation: generation,
    complete: true,
    payload_profile: "structural",
    hub: { name: "X1S HUB", version: "X1S" },
    devices: [
      { kind: "device_backup", device: { device_id: 1, name: "TV", brand: "Sony", device_class: "ir", idle_behavior: 1, ...tv }, complete: true, editable: true, fetched_at: "t", commands: [{ command_id: 1, name: "Power" }], button_bindings: [], macros: [] },
      ...extra,
    ],
    activities: [],
  } as unknown as SnapshotDocument;
}

function renamed(bundle: BackupBundlePayload, field: string, value: unknown): BackupBundlePayload {
  const tv = entityElement(bundle, "device", 1)!;
  return withEntityElement(bundle, "device", 1, { ...tv, device: { ...tv.device, [field]: value } } as typeof tv);
}

test("a draft slot holds the edit and the rows as they were, and carries over three-way", () => {
  const before = snapshotAsBundle(doc("s1", 1));
  const working = renamed(before, "name", "Mine");
  const slot = draftSlot(working, before, "device", 1)!;
  assert.equal(slot.data.element.device?.name, "Mine");
  assert.equal(slot.base.element.device?.name, "TV");
  const draft: Draft = { scope: "hub/devices/1", snapshotId: "s1", data: slot.data, base: slot.base, updatedAt: 1 };
  assert.equal(entityDraftBase(draft, "device", 1)?.element.device?.name, "TV");

  // Elsewhere the brand changed: both survive.
  const fresh = snapshotAsBundle(doc("s2", 2, { brand: "LG" }));
  const carried = rebaseDraftOnto(fresh, "device", 1, slot.data, slot.base);
  assert.equal(entityElement(carried.bundle, "device", 1)?.device?.name, "Mine");
  assert.equal(entityElement(carried.bundle, "device", 1)?.device?.brand, "LG");
  assert.equal(carried.overlap, false);

  // Elsewhere the name changed too: the user's name wins, and says so.
  const clash = rebaseDraftOnto(snapshotAsBundle(doc("s3", 3, { name: "Telly" })), "device", 1, slot.data, slot.base);
  assert.equal(entityElement(clash.bundle, "device", 1)?.device?.name, "Mine");
  assert.equal(clash.overlap, true);

  // A draft from before 2026-10-16 has no base: the user's row is kept whole.
  const legacy = rebaseDraftOnto(fresh, "device", 1, slot.data, null);
  assert.equal(entityElement(legacy.bundle, "device", 1)?.device?.brand, "Sony");
  assert.equal(legacy.overlap, true);
  assert.equal(entityDraftBase({ ...draft, base: undefined }, "device", 1), null);
});

// -- the device editor following the cache ----------------------------------------------------------

function ok<T>(body: T, status = 200): ApiResponse<T> {
  return { ok: status < 400, status, statusText: "", headers: [], text: JSON.stringify(body), body };
}

class EditorApi {
  doc: SnapshotDocument;
  snapshots = 0;
  edits: { snapshotId: string; name: unknown }[] = [];
  editAnswers: number[] = [];
  jobStatus: JobView["status"] = "done";
  afterJob: (() => void) | null = null;
  constructor(initial: SnapshotDocument) {
    this.doc = initial;
  }
  snapshot = async () => {
    this.snapshots++;
    return ok(structuredClone(this.doc));
  };
  request = async () => ok({ device_id: null });
  editDevice = async (_hub: string, _id: number, element: { device?: { name?: unknown } }, snapshotId: string) => {
    this.edits.push({ snapshotId, name: element.device?.name });
    const status = this.editAnswers.shift() ?? 202;
    if (status === 412) return { ok: false, status: 412, statusText: "", headers: [], text: "", body: { type: "snapshot_outdated", title: "The snapshot moved", status: 412 } } as unknown as ApiResponse<JobView>;
    return ok(job("jx", "queued"), 202);
  };
  followJob = async () => {
    this.afterJob?.();
    return { ...job("jx", this.jobStatus), error: this.jobStatus === "failed" ? { type: "write_failed", title: "Write failed", status: 500, detail: "no ack" } : null } as JobView;
  };
}

class FakeStore {
  draft: Draft | null = null;
  setDraft = (_hub: string, draft: Omit<Draft, "updatedAt"> | null) => {
    this.draft = draft ? { ...draft, updatedAt: 1 } : null;
  };
  discardDraft = () => {
    this.draft = null;
  };
  noteResponse = () => false;
}

// The editor's private and protected members, as the test drives them.
type Editor = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

async function openEditor(initial: SnapshotDocument, draft: Draft | null = null): Promise<{ editor: Editor; api: EditorApi; store: FakeStore; setCache: (version: ReturnType<typeof v>) => void }> {
  defineDeviceEditor();
  const api = new EditorApi(initial);
  const store = new FakeStore();
  store.draft = draft;
  const editor = new SbPanelDeviceEditor() as unknown as Editor;
  editor.api = api as never;
  editor.store = store as never;
  editor.deviceId = 1;
  const setCache = (version: ReturnType<typeof v>) => {
    editor.ctx = {
      hub: hub({ status: { hub_connected: true, app_connected: false, controllable: true, mode: "control", hub_version: "X1S", proxy_enabled: true, running_activity: null, activities_cached: 0, devices_cached: 1, catalog_ready: true } as HubView["status"], cache: version }),
      runtime: { draft: store.draft } as never,
      gate: "pass",
      busy: null,
      interaction: { kind: "free" },
      free: true,
      api: api as never,
    };
  };
  setCache(v(initial.engine_generation, initial.snapshot_id));
  editor._loadedKey = "a:1";
  await editor._load();
  return { editor, api, store, setCache };
}

const tvName = (editor: Editor, which: "_working" | "_baseline") => entityElement(editor[which], "device", 1)?.device?.name;
const tvBrand = (editor: Editor, which: "_working" | "_baseline") => entityElement(editor[which], "device", 1)?.device?.brand;

test("a clean editor takes a change made elsewhere silently", async () => {
  const { editor, api, store, setCache } = await openEditor(doc("s1", 1));
  assert.equal(editor._stage, "editing");
  api.doc = doc("s2", 2, { name: "Telly" });
  setCache(v(2));
  await editor._followCache();
  assert.equal(tvName(editor, "_baseline"), "Telly");
  assert.equal(tvName(editor, "_working"), "Telly");
  assert.equal(editor._dirty, false);
  assert.equal(editor._snapshot.snapshot_id, "s2");
  assert.equal(store.draft, null);
  assert.equal(editor._changedElsewhere, false);
  // The same version again reads nothing.
  const reads = api.snapshots;
  editor._maybeFollowCache();
  assert.equal(api.snapshots, reads);
  assert.equal(editor._follow.pending, false);
});

test("an editor with unsynced edits keeps them on top of the change made elsewhere", async () => {
  const { editor, api, store, setCache } = await openEditor(doc("s1", 1));
  editor._commit(renamed(editor._working, "name", "Mine"));
  assert.equal(store.draft?.snapshotId, "s1");
  api.doc = doc("s2", 2, { brand: "LG" });
  setCache(v(2));
  await editor._followCache();
  assert.equal(tvName(editor, "_working"), "Mine");
  assert.equal(tvBrand(editor, "_working"), "LG");
  assert.equal(tvBrand(editor, "_baseline"), "LG");
  assert.equal(editor._dirty, true);
  assert.equal(editor._changedElsewhere, false, "no overlap, no line");
  // The draft follows: the new snapshot, the new base.
  assert.equal(store.draft?.snapshotId, "s2");
  assert.equal((store.draft?.base as { element: { device: { brand: string } } }).element.device.brand, "LG");
});

test("where both changed the same thing the user's edit wins and one line says so", async () => {
  const { editor, api, setCache } = await openEditor(doc("s1", 1));
  editor._commit(renamed(editor._working, "name", "Mine"));
  api.doc = doc("s2", 2, { name: "Telly" });
  setCache(v(2));
  await editor._followCache();
  assert.equal(tvName(editor, "_working"), "Mine");
  assert.equal(editor._changedElsewhere, true);
  assert.match(TOOLS_CARD_STRINGS.activities.changedElsewhere("device"), /your edit is kept/);
});

test("deleted elsewhere: a clean editor closes, a dirty one shows the missing screen and drops its draft", async () => {
  const clean = await openEditor(doc("s1", 1));
  let navigated = false;
  clean.editor._goToList = () => { navigated = true; };
  clean.api.doc = { ...doc("s2", 2), devices: [] } as SnapshotDocument;
  clean.setCache(v(2));
  await clean.editor._followCache();
  assert.equal(navigated, true);

  const dirty = await openEditor(doc("s1", 1));
  dirty.editor._commit(renamed(dirty.editor._working, "name", "Mine"));
  dirty.api.doc = { ...doc("s2", 2), devices: [] } as SnapshotDocument;
  dirty.setCache(v(2));
  await dirty.editor._followCache();
  assert.equal(dirty.editor._stage, "missing");
  assert.equal(dirty.store.draft, null);
});

test("a stored draft from an older snapshot is carried over when the editor opens, never asked about", async () => {
  const before = snapshotAsBundle(doc("s1", 1));
  const slot = draftSlot(renamed(before, "name", "Mine"), before, "device", 1)!;
  const draft: Draft = { scope: "hub/devices/1", snapshotId: "s1", data: slot.data, base: slot.base, updatedAt: 1 };
  const { editor, store } = await openEditor(doc("s2", 2, { brand: "LG" }), draft);
  assert.equal(editor._stage, "editing");
  assert.equal(tvName(editor, "_working"), "Mine");
  assert.equal(tvBrand(editor, "_working"), "LG");
  assert.equal(editor._changedElsewhere, false);
  assert.equal(store.draft?.snapshotId, "s2", "stored again against the current snapshot");

  // The same snapshot: spliced back as it is.
  const same = await openEditor(doc("s1", 1), draft);
  assert.equal(tvName(same.editor, "_working"), "Mine");
  assert.equal(same.store.draft, draft);
});

test("a Sync refused because the snapshot moved is carried over and sent once more", async () => {
  const { editor, api, store } = await openEditor(doc("s1", 1));
  editor._commit(renamed(editor._working, "name", "Mine"));
  // Elsewhere, just before the Sync: the brand changed.
  api.doc = doc("s2", 2, { brand: "LG" });
  api.editAnswers = [412, 202];
  api.afterJob = () => { api.doc = doc("s3", 3, { name: "Mine", brand: "LG" }); };
  const synced = await editor._sync();
  assert.equal(synced, true);
  assert.deepEqual(api.edits, [{ snapshotId: "s1", name: "Mine" }, { snapshotId: "s2", name: "Mine" }]);
  assert.equal(editor._stage, "editing");
  assert.equal(tvName(editor, "_baseline"), "Mine");
  assert.equal(tvBrand(editor, "_baseline"), "LG");
  assert.equal(store.draft, null);
});

test("a 412 while the snapshot did not move is not sent again: the stale state shows", async () => {
  const { editor, api } = await openEditor(doc("s1", 1));
  editor._commit(renamed(editor._working, "name", "Mine"));
  api.editAnswers = [412, 412];
  const synced = await editor._sync();
  assert.equal(synced, false);
  assert.equal(api.edits.length, 1);
  assert.equal(editor._stage, "sync_failed");
  assert.equal(editor._syncFailed?.stale, true);
});

test("a failed sync carries the edit over to what landed; Retry with nothing left goes back to editing", async () => {
  const { editor, api } = await openEditor(doc("s1", 1));
  editor._commit(renamed(editor._working, "name", "Mine"));
  api.jobStatus = "failed";
  // The write landed after all; the server read it back before reporting the failure.
  api.afterJob = () => { api.doc = doc("s2", 2, { name: "Mine" }); };
  const synced = await editor._sync();
  assert.equal(synced, false);
  assert.equal(editor._stage, "sync_failed");
  assert.equal(tvName(editor, "_baseline"), "Mine");
  const edits = api.edits.length;
  editor._retrySync();
  assert.equal(editor._stage, "editing");
  assert.equal(api.edits.length, edits, "nothing was written again");
  assert.equal(editor._dirty, false);
});
