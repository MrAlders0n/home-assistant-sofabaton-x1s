/**
 * Rebase an open editor onto a newer structural bundle.
 *
 * The editor holds `base` (the bundle it captured) and `mine` (the user's
 * working copy). When the cache moves under it (another tab, a refresh, an
 * automation, a sync that partly landed), `theirs` is the new capture. The
 * result keeps every edit of the user's and takes every other change from
 * `theirs`: a three-way merge where the user wins on overlap.
 *
 * Granularity: device and activity rows are matched by id; inside a row,
 * objects merge key by key and arrays (favorites, bindings, macros, steps)
 * are taken whole. So a rename elsewhere and a favorites edit here both
 * survive; two edits of the same favorites list are an overlap.
 */
import type { BackupBundlePayload } from "../shared/ha-context";

export interface RebaseResult {
  bundle: BackupBundlePayload;
  /** Something the user changed was also changed elsewhere (the user's
   *  version was kept). */
  overlap: boolean;
}

type Json = unknown;

function isPlainObject(value: Json): value is Record<string, Json> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function same(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

function rowId(row: Json): number | null {
  if (!isPlainObject(row) || !isPlainObject(row.device)) return null;
  const id = Number(row.device.device_id);
  return Number.isFinite(id) ? id : null;
}

function isRowList(value: Json): value is Json[] {
  return Array.isArray(value) && value.every((row) => rowId(row) != null);
}

function clone<T>(value: T): T {
  return value === undefined ? value : structuredClone(value);
}

function mergeValue(base: Json, mine: Json, theirs: Json, overlap: { hit: boolean }): Json {
  if (same(mine, base)) return clone(theirs);
  if (same(theirs, base) || same(mine, theirs)) return mine;
  if (isPlainObject(base) && isPlainObject(mine) && isPlainObject(theirs)) {
    const out: Record<string, Json> = {};
    const keys = new Set([...Object.keys(theirs), ...Object.keys(mine)]);
    for (const key of keys) {
      const value = mergeValue(base[key], mine[key], theirs[key], overlap);
      if (value !== undefined) out[key] = value;
    }
    return out;
  }
  overlap.hit = true;
  return mine;
}

/** Rows matched by id, in `theirs` order, then rows only the user has. */
function mergeRows(base: Json[], mine: Json[], theirs: Json[], overlap: { hit: boolean }): Json[] {
  const byId = (rows: Json[]) => new Map(rows.map((row) => [rowId(row) as number, row]));
  const baseRows = byId(base);
  const mineRows = byId(mine);
  const out: Json[] = [];
  for (const row of theirs) {
    const id = rowId(row) as number;
    const merged = mergeValue(baseRows.get(id), mineRows.get(id), row, overlap);
    if (merged !== undefined) out.push(merged);
  }
  const theirIds = new Set(theirs.map((row) => rowId(row)));
  for (const row of mine) {
    const id = rowId(row) as number;
    if (theirIds.has(id)) continue;
    // Deleted elsewhere: gone too unless the user changed it.
    const merged = mergeValue(baseRows.get(id), row, undefined, overlap);
    if (merged !== undefined) out.push(merged);
  }
  return out;
}

export function rebaseBundle(
  base: BackupBundlePayload,
  mine: BackupBundlePayload,
  theirs: BackupBundlePayload,
): RebaseResult {
  const overlap = { hit: false };
  const out: Record<string, Json> = {};
  const b = base as unknown as Record<string, Json>;
  const m = mine as unknown as Record<string, Json>;
  const t = theirs as unknown as Record<string, Json>;
  for (const key of new Set([...Object.keys(t), ...Object.keys(m)])) {
    if ((key === "devices" || key === "activities")
        && isRowList(b[key] ?? []) && isRowList(m[key] ?? []) && isRowList(t[key] ?? [])) {
      out[key] = mergeRows((b[key] ?? []) as Json[], (m[key] ?? []) as Json[], (t[key] ?? []) as Json[], overlap);
      continue;
    }
    const value = mergeValue(b[key], m[key], t[key], overlap);
    if (value !== undefined) out[key] = value;
  }
  return { bundle: out as unknown as BackupBundlePayload, overlap: overlap.hit };
}
