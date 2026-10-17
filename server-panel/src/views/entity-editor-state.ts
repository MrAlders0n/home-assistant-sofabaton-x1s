// Pure helpers both editors share (activity editor plan, decision 3): the
// entity's element inside the snapshot-as-bundle, the draft slot's shape and
// the macro time bytes. The device editor's own helpers stay in
// device-editor-state.ts, the activity editor's in activity-editor-state.ts.

import type {
  BackupBundleActivityPayload,
  BackupBundleDevicePayload,
  BackupBundlePayload,
} from "../../../custom_components/sofabaton_x1s/www/src/shared/ha-context";
import { rebaseBundle, type RebaseResult } from "../../../custom_components/sofabaton_x1s/www/src/tabs/bundle-rebase";
import type { Draft } from "../panel-store";

export type EntityKind = "device" | "activity";
export type EntityElement = BackupBundleDevicePayload | BackupBundleActivityPayload;

function rowsOf(bundle: BackupBundlePayload | null, kind: EntityKind): EntityElement[] {
  return ((kind === "device" ? bundle?.devices : bundle?.activities) ?? []) as EntityElement[];
}

function idOf(entry: EntityElement | null | undefined): number {
  return Number(entry?.device?.device_id ?? -1);
}

export function entityElement(bundle: BackupBundlePayload | null, kind: EntityKind, entityId: number): EntityElement | null {
  return rowsOf(bundle, kind).find((entry) => idOf(entry) === Number(entityId)) ?? null;
}

/** The bundle with one element replaced (a draft's element spliced into a fresh snapshot). */
export function withEntityElement(bundle: BackupBundlePayload, kind: EntityKind, entityId: number, element: EntityElement): BackupBundlePayload {
  const id = Number(entityId);
  const swap = (rows: EntityElement[] | null | undefined) => (rows ?? []).map((entry) => (idOf(entry) === id ? element : entry));
  return kind === "device"
    ? { ...bundle, devices: swap(bundle.devices) as BackupBundleDevicePayload[] }
    : { ...bundle, activities: swap(bundle.activities as EntityElement[]) as BackupBundlePayload["activities"] };
}

/** The list subtab an entity kind lives under. */
export function entityListSub(kind: EntityKind): "devices" | "activities" {
  return kind === "device" ? "devices" : "activities";
}

/** The draft scope (state plan decision 8): the editor's route scope. */
export function entityDraftScope(kind: EntityKind, entityId: number): string {
  return `hub/${entityListSub(kind)}/${Number(entityId)}`;
}

/** What an editor keeps in the draft slot: its element, and for an activity the device elements its edit touched. */
export interface EntityDraftData {
  element: EntityElement;
  devices?: BackupBundleDevicePayload[];
}

/** The stored draft's data when it belongs to this entity and looks like one; null otherwise. */
export function entityDraftData(draft: Draft | null | undefined, kind: EntityKind, entityId: number): EntityDraftData | null {
  if (!draft || draft.scope !== entityDraftScope(kind, entityId)) return null;
  const data = draft.data as Partial<EntityDraftData> | null | undefined;
  const element = data?.element;
  if (!element || typeof element !== "object" || idOf(element) !== Number(entityId)) return null;
  const devices = Array.isArray(data?.devices) ? data!.devices!.filter((entry) => entry && typeof entry === "object" && idOf(entry) > 0) : [];
  return devices.length ? { element, devices } : { element };
}

/** Device elements of `working` that differ from `baseline` (an activity edit's device-side effects, e.g. Set input). */
export function touchedDevices(working: BackupBundlePayload | null, baseline: BackupBundlePayload | null): BackupBundleDevicePayload[] {
  const before = new Map((baseline?.devices ?? []).map((entry) => [idOf(entry), JSON.stringify(entry)]));
  return (working?.devices ?? []).filter((entry) => before.has(idOf(entry)) && before.get(idOf(entry)) !== JSON.stringify(entry));
}

/** A bundle with the draft's data spliced back in. */
export function withDraftData(bundle: BackupBundlePayload, kind: EntityKind, entityId: number, data: EntityDraftData): BackupBundlePayload {
  let next = withEntityElement(bundle, kind, entityId, data.element);
  for (const device of data.devices ?? []) next = withEntityElement(next, "device", idOf(device), device);
  return next;
}

/** What the draft's data was before the edit (the `base` slot), in the data's own shape; null when the draft has none. */
export function entityDraftBase(draft: Draft | null | undefined, kind: EntityKind, entityId: number): EntityDraftData | null {
  if (!draft || draft.base === undefined) return null;
  return entityDraftData({ ...draft, data: draft.base }, kind, entityId);
}

/** The draft slot for a working copy: the entity's element (and the devices an activity edit touched), and the same rows as the baseline had them. */
export function draftSlot(working: BackupBundlePayload, baseline: BackupBundlePayload | null, kind: EntityKind, entityId: number): { data: EntityDraftData; base: EntityDraftData } | null {
  const element = entityElement(working, kind, entityId);
  const before = entityElement(baseline, kind, entityId);
  if (!element || !before) return null;
  const devices = kind === "activity" ? touchedDevices(working, baseline) : [];
  const baseDevices = devices.map((device) => entityElement(baseline, "device", idOf(device))).filter((row): row is EntityElement => row !== null) as BackupBundleDevicePayload[];
  return {
    data: devices.length ? { element, devices } : { element },
    base: baseDevices.length ? { element: before, devices: baseDevices } : { element: before },
  };
}

/**
 * A stored draft carried over to the hub's current snapshot (`fresh`):
 * the three-way merge of bundle-rebase.ts with the draft's base, so a
 * change made elsewhere meanwhile is kept unless the user changed the same
 * thing (`overlap`). A draft stored without a base (before 2026-10-16)
 * keeps the user's rows whole; `overlap` then says whether they differ
 * from the hub's.
 */
export function rebaseDraftOnto(fresh: BackupBundlePayload, kind: EntityKind, entityId: number, data: EntityDraftData, base: EntityDraftData | null): RebaseResult {
  const mine = withDraftData(fresh, kind, entityId, data);
  if (!base) {
    const changed = JSON.stringify(entityElement(mine, kind, entityId)) !== JSON.stringify(entityElement(fresh, kind, entityId))
      || (data.devices ?? []).some((device) => JSON.stringify(device) !== JSON.stringify(entityElement(fresh, "device", idOf(device))));
    return { bundle: mine, overlap: changed };
  }
  return rebaseBundle(withDraftData(fresh, kind, entityId, base), mine, fresh);
}

// -- macro time bytes (0.5 s units; a byte of 4 = 2.0 s; 0 = a click / no wait) -----------------

/** The card's rule (shared/hub-rules.ts, CR-X6-3). */
export { byteToSeconds, secondsToByte } from "../../../custom_components/sofabaton_x1s/www/src/shared/hub-rules";
