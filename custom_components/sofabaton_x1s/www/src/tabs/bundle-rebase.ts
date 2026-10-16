/**
 * Rebase an open editor onto a newer structural bundle.
 *
 * The editor holds `base` (the bundle it captured) and `mine` (the user's
 * working copy). When the cache moves under it (another tab, a refresh, an
 * automation, a sync that partly landed), `theirs` is the new capture. The
 * result keeps every edit of the user's and takes every other change from
 * `theirs`: a three-way merge where the user wins on overlap.
 *
 * Granularity: device and activity rows are matched by id, and so are the
 * rows of the lists inside them: commands by command_id, button bindings
 * and macros by button_id, favorites by the command they point at (the
 * editor renumbers favorite button_ids on a reorder; the sync engine
 * matches favorites by content too). Inside a row, objects merge key by
 * key; any other array (macro steps, the quick-access order) is taken
 * whole. So renaming one command here and another elsewhere keeps both;
 * renaming the same command on both sides is an overlap.
 */
import type { BackupBundlePayload } from "../shared/ha-context";

export interface RebaseResult {
  bundle: BackupBundlePayload;
  /** Something the user changed was also changed elsewhere (the user's
   *  version was kept). */
  overlap: boolean;
}

type Json = unknown;
type Overlap = { hit: boolean };
/** A list row's identity within its list, null when it has none. */
type RowKey = (row: Record<string, Json>) => string | null;

function isPlainObject(value: Json): value is Record<string, Json> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function same(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

function clone<T>(value: T): T {
  return value === undefined ? value : structuredClone(value);
}

function idKey(value: Json): string | null {
  const id = Number(value);
  return value != null && Number.isFinite(id) ? String(id) : null;
}

/** The lists merged row by row, by the name they have in a device or
 *  activity row. */
const LIST_ROW_KEYS: Record<string, RowKey> = {
  commands: (row) => idKey(row.command_id),
  button_bindings: (row) => idKey(row.button_id),
  macros: (row) => idKey(row.button_id),
  favorite_slots: (row) => {
    const device = idKey(row.device_id);
    const command = idKey(row.command_id);
    return device && command ? `${device}:${command}` : null;
  },
};

/** Device and activity rows of the bundle, by their device id. */
const ENTITY_ROW_KEY: RowKey = (row) => (isPlainObject(row.device) ? idKey(row.device.device_id) : null);

/** The rows of *list* by key; null when a row has no key, so the list is
 *  merged whole. A key that repeats (two favorites of one command) gets
 *  its occurrence number. */
function keyedRows(list: Json, key: RowKey): Map<string, Json> | null {
  if (list === undefined || list === null) return new Map();
  if (!Array.isArray(list)) return null;
  const rows = new Map<string, Json>();
  const seen = new Map<string, number>();
  for (const row of list) {
    if (!isPlainObject(row)) return null;
    const base = key(row);
    if (base == null) return null;
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    rows.set(n === 1 ? base : `${base}#${n}`, row);
  }
  return rows;
}

/** Rows matched by key: the user's rows in the user's order, then rows only
 *  `theirs` has. A list only one side changed is taken whole from it. */
function mergeKeyedList(base: Json, mine: Json, theirs: Json, key: RowKey, overlap: Overlap): Json {
  if (same(mine, base)) return clone(theirs);
  if (same(theirs, base) || same(mine, theirs)) return mine;
  const baseRows = keyedRows(base, key);
  const mineRows = keyedRows(mine, key);
  const theirRows = keyedRows(theirs, key);
  if (!baseRows || !mineRows || !theirRows) return mergeWhole(base, mine, theirs, overlap);
  const out: Json[] = [];
  for (const [rowKey, row] of mineRows) {
    // A row gone elsewhere stays gone unless the user changed it.
    const merged = mergeValue(baseRows.get(rowKey), row, theirRows.get(rowKey), overlap);
    if (merged !== undefined) out.push(merged);
  }
  for (const [rowKey, row] of theirRows) {
    if (mineRows.has(rowKey)) continue;
    // Added elsewhere, or removed here: removed stays removed unless it
    // was also changed elsewhere (then the user's removal wins, noted).
    const merged = mergeValue(baseRows.get(rowKey), undefined, row, overlap);
    if (merged !== undefined) out.push(merged);
  }
  return out;
}

function mergeWhole(base: Json, mine: Json, theirs: Json, overlap: Overlap): Json {
  if (same(mine, base)) return clone(theirs);
  if (same(theirs, base) || same(mine, theirs)) return mine;
  overlap.hit = true;
  return mine;
}

function mergeValue(base: Json, mine: Json, theirs: Json, overlap: Overlap): Json {
  if (same(mine, base)) return clone(theirs);
  if (same(theirs, base) || same(mine, theirs)) return mine;
  if (isPlainObject(base) && isPlainObject(mine) && isPlainObject(theirs)) {
    const out: Record<string, Json> = {};
    for (const key of new Set([...Object.keys(theirs), ...Object.keys(mine)])) {
      const rowKey = LIST_ROW_KEYS[key];
      const value = rowKey
        ? mergeKeyedList(base[key], mine[key], theirs[key], rowKey, overlap)
        : mergeValue(base[key], mine[key], theirs[key], overlap);
      if (value !== undefined) out[key] = value;
    }
    return out;
  }
  overlap.hit = true;
  return mine;
}

export function rebaseBundle(
  base: BackupBundlePayload,
  mine: BackupBundlePayload,
  theirs: BackupBundlePayload,
): RebaseResult {
  const overlap: Overlap = { hit: false };
  const out: Record<string, Json> = {};
  const b = base as unknown as Record<string, Json>;
  const m = mine as unknown as Record<string, Json>;
  const t = theirs as unknown as Record<string, Json>;
  for (const key of new Set([...Object.keys(t), ...Object.keys(m)])) {
    const value = key === "devices" || key === "activities"
      ? mergeKeyedList(b[key], m[key], t[key], ENTITY_ROW_KEY, overlap)
      : mergeValue(b[key], m[key], t[key], overlap);
    if (value !== undefined) out[key] = value;
  }
  return { bundle: out as unknown as BackupBundlePayload, overlap: overlap.hit };
}
