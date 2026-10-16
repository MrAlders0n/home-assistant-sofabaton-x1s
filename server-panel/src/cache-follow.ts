// How the panel follows the hub's cache (the HA card's model, 2026-10-16):
// every hub view carries the cache version (`HubView.cache`: epoch,
// generation, snapshot_id) and the stream pushes a new view whenever the
// hub, its jobs or its cache changed (`hub_update`, asked for with
// `?hub_updates=true`). A view that loaded rows remembers the version they
// came from and reads them again, coalesced, once a newer version with
// another snapshot id is reported. Pure helpers and an injectable timer, so
// the node tests cover them without a DOM.

import type { CacheVersion, HubView, JobView } from "./panel-api";
import { TERMINAL_JOB_STATES } from "./panel-api";

/** The version a view's rows come from: the snapshot's id and generation
 *  (GET /snapshot reports both), and the epoch the hub had when the read
 *  started. Null fields mean "not known": the next report reloads. */
export interface LoadedVersion {
  epoch: number | null;
  generation: number | null;
  snapshotId: string | null;
}

/** True when `a` is a later state of the cache than `b`: a newer proxy (epoch), or a later generation of the same one. */
export function isNewerVersion(a: CacheVersion | null | undefined, b: CacheVersion | null | undefined): boolean {
  if (!a) return false;
  if (!b) return true;
  if (a.epoch !== b.epoch) return a.epoch > b.epoch;
  return a.generation > b.generation;
}

/** What a view loaded: the snapshot it read, with the epoch reported when the read started. */
export function loadedVersion(
  atStart: CacheVersion | null | undefined,
  doc: { snapshot_id?: string | null; engine_generation?: number | null } | null | undefined,
): LoadedVersion {
  return {
    epoch: atStart?.epoch ?? null,
    generation: typeof doc?.engine_generation === "number" ? doc.engine_generation : null,
    snapshotId: doc?.snapshot_id ?? null,
  };
}

/** A view whose rows carry no snapshot (Wifi Devices, the Backup tab's lists): the version reported when the read started. */
export function versionAsLoaded(version: CacheVersion | null | undefined): LoadedVersion | null {
  return version ? { epoch: version.epoch, generation: version.generation, snapshotId: version.snapshot_id } : null;
}

/** True when the reported version says the loaded rows are out of date:
 *  another snapshot id, and not older than what was loaded. An equal
 *  generation never reloads, so a version that cannot be told apart never
 *  loops. */
export function needsReload(loaded: LoadedVersion | null, latest: CacheVersion | null | undefined): boolean {
  if (!latest) return false;
  if (!loaded || loaded.snapshotId === null) return true;
  if (latest.snapshot_id === loaded.snapshotId) return false;
  if (loaded.epoch === null || latest.epoch !== loaded.epoch) return loaded.epoch === null || latest.epoch > loaded.epoch;
  return loaded.generation === null || latest.generation > loaded.generation;
}

/** A short key for a version: views compare it to notice a report moved. */
export function versionKey(version: CacheVersion | null | undefined): string {
  return version ? `${version.epoch}:${version.generation}:${version.snapshot_id}` : "";
}

// -- one hub view over another ----------------------------------------------------------------------

const JOB_STATUS_RANK: Record<string, number> = { queued: 0, running: 1, done: 2, failed: 2, cancelled: 2 };

function isLive(job: JobView | null | undefined): job is JobView {
  return Boolean(job && !TERMINAL_JOB_STATES.has(job.status));
}

/** The later report of one job: further along its states, then further along its steps. */
function fresherJob(current: JobView, incoming: JobView): JobView {
  const rank = (job: JobView) => JOB_STATUS_RANK[job.status] ?? 0;
  if (rank(current) !== rank(incoming)) return rank(current) > rank(incoming) ? current : incoming;
  const steps = (job: JobView) => Number(job.progress?.completed_steps ?? -1);
  return steps(current) > steps(incoming) ? current : incoming;
}

/**
 * A hub view that just arrived (a REST list, a pushed `hub_update`) laid
 * over the one the panel holds. A view may have been built before a
 * `job_event` the panel already applied (a list request that was in flight
 * when the job started), so it never takes back what the job events said:
 * a job seen ending stays ended, a job seen starting stays running until a
 * view knows its end, the last job never goes back to an older one. The
 * cache version only moves forward within a running proxy; a view without
 * one (the hub stopped) clears it.
 */
export function reconcileHubView(current: HubView | null | undefined, incoming: HubView): HubView {
  if (!current || current.hub_id !== incoming.hub_id) return incoming;
  let active: JobView | null = incoming.active_job ?? null;
  let last: JobView | null = incoming.last_job ?? null;
  const knownActive = isLive(current.active_job) ? current.active_job : null;
  const knownLast = current.last_job && TERMINAL_JOB_STATES.has(current.last_job.status) ? current.last_job : null;

  // The view shows running a job the panel saw end: it predates that end.
  if (active && knownLast && knownLast.job_id === active.job_id) {
    active = null;
    if (!last || last.created_at < knownLast.created_at) last = knownLast;
  }
  if (knownActive) {
    if (active && active.job_id === knownActive.job_id) {
      active = fresherJob(knownActive, active);
    } else if (active && active.created_at < knownActive.created_at) {
      // An older job the view still shows running: one job runs at a time, so it ended.
      active = knownActive;
    } else if (!active) {
      const ended = last !== null && (last.job_id === knownActive.job_id || last.created_at > knownActive.created_at);
      // Neither running nor finished in the view: it predates the start.
      if (!ended) active = knownActive;
    }
  }
  if (knownLast && (!last || (last.job_id !== knownLast.job_id && last.created_at < knownLast.created_at))) last = knownLast;

  let cache = incoming.cache ?? null;
  if (cache && current.cache && isNewerVersion(current.cache, cache)) cache = current.cache;

  if (active === (incoming.active_job ?? null) && last === (incoming.last_job ?? null) && cache === (incoming.cache ?? null)) return incoming;
  return { ...incoming, active_job: active, last_job: last, cache };
}

// -- coalescing ---------------------------------------------------------------------------------

type TimerHandle = unknown;

export interface CoalescerOptions {
  /** Run this long after the last poke... */
  debounceMs: number;
  /** ...but never later than this after the first one. */
  maxWaitMs: number;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
}

/** Runs `fn` once for a burst of pokes: `debounceMs` after the last, at most `maxWaitMs` after the first. */
export class Coalescer {
  private readonly _fn: () => void;
  private readonly _debounceMs: number;
  private readonly _maxWaitMs: number;
  private readonly _now: () => number;
  private readonly _setTimer: (fn: () => void, ms: number) => TimerHandle;
  private readonly _clearTimer: (handle: TimerHandle) => void;
  private _first: number | null = null;
  private _timer: TimerHandle | null = null;

  constructor(fn: () => void, options: CoalescerOptions) {
    this._fn = fn;
    this._debounceMs = options.debounceMs;
    this._maxWaitMs = options.maxWaitMs;
    this._now = options.now ?? (() => Date.now());
    this._setTimer = options.setTimer ?? ((run, ms) => setTimeout(run, ms));
    this._clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  }

  get pending(): boolean {
    return this._timer !== null;
  }

  poke(): void {
    const now = this._now();
    if (this._first === null) this._first = now;
    const delay = Math.max(0, Math.min(this._debounceMs, this._first + this._maxWaitMs - now));
    if (this._timer !== null) this._clearTimer(this._timer);
    this._timer = this._setTimer(() => {
      this._timer = null;
      this._first = null;
      this._fn();
    }, delay);
  }

  cancel(): void {
    if (this._timer !== null) this._clearTimer(this._timer);
    this._timer = null;
    this._first = null;
  }
}

/** The views' cadence: a quarter second after the last change, at most 1.5 s while changes keep coming. */
export const FOLLOW_DEBOUNCE_MS = 250;
export const FOLLOW_MAX_WAIT_MS = 1500;

/** After held hub work ends, the job's last rows land a moment later (the
 *  end-of-job hub update): an editor held during the work waits this long,
 *  restarted by every version that lands, then follows once (the card's
 *  FOLLOW_SETTLE_MS). */
export const FOLLOW_SETTLE_MS = 600;
