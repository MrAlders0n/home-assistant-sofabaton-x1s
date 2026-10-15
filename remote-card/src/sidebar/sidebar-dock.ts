// Dock shortcuts for the sidebar remote: at most two favorites, macros or
// device commands pinned beside the pull handle, one set per activity
// (activity mode) and one per device (device mode). They live in this
// browser only (localStorage, keyed by the hub's remote entity); nothing
// reaches the hub or the integration. A pin names its item, it does not copy
// it: every render resolves it against the live lists, and a pin whose item
// is gone from the hub simply does not show.

export type DockSlot = "left" | "right";
export type DockKind = "favorites" | "macros" | "commands";

/** The item a shortcut points at. */
export interface DockRef {
  kind: DockKind;
  commandId: number;
  deviceId: number;
}

export interface DockPin extends DockRef {
  /** The chosen icon, DOCK_MONOGRAM for the initials, null for the automatic one. */
  icon: string | null;
}

export type DockSlots = Partial<Record<DockSlot, DockPin>>;

export const DOCK_SLOTS: readonly DockSlot[] = ["left", "right"];
export const DOCK_STORAGE_PREFIX = "sofabaton_x1s:sidebar:dock:";
/** The icon value that shows the label's initials instead of an icon. */
export const DOCK_MONOGRAM = "monogram";

/** Name fragments the hub's command names commonly carry, most specific first. */
const ICON_GUESSES: ReadonlyArray<[RegExp, string]> = [
  [/netflix/i, "mdi:netflix"],
  [/youtube/i, "mdi:youtube"],
  [/spotify/i, "mdi:spotify"],
  [/\bplex\b/i, "mdi:plex"],
  [/\bkodi\b/i, "mdi:kodi"],
  [/twitch/i, "mdi:twitch"],
  [/\bmute\b/i, "mdi:volume-mute"],
  [/\bvol(ume)?\s*(\+|up\b)/i, "mdi:volume-plus"],
  [/\bvol(ume)?\s*(-|−|down\b)/i, "mdi:volume-minus"],
  [/\bpower\b|\bon\s*\/\s*off\b/i, "mdi:power"],
  [/hdmi|\binput\b|\bsource\b/i, "mdi:video-input-hdmi"],
  [/play\s*\/?\s*pause/i, "mdi:play-pause"],
  [/subtitle/i, "mdi:subtitles-outline"],
  [/\bguide\b|\bepg\b/i, "mdi:television-guide"],
  [/\binfo\b/i, "mdi:information-outline"],
  [/\bsettings?\b|\bsetup\b/i, "mdi:cog-outline"],
  [/\bscene\b/i, "mdi:palette-outline"],
  [/\blights?\b|\blamps?\b|\bdim\b/i, "mdi:lightbulb-outline"],
  [/\bmovie|\bcinema\b|\bfilm\b/i, "mdi:movie-open-outline"],
  [/\bsleep\b/i, "mdi:sleep"],
  [/\bnight\b/i, "mdi:weather-night"],
  [/\bbass\b|\bsurround\b|\bsound\b|\baudio\b/i, "mdi:surround-sound"],
  [/\bmusic\b|\bradio\b/i, "mdi:music"],
  [/\bgam(e|es|ing)\b/i, "mdi:gamepad-variant-outline"],
  [/\bfan\b/i, "mdi:fan"],
  [/\bblinds?\b|\bcurtains?\b|\bshades?\b/i, "mdi:blinds"],
  [/\bpicture\b|\baspect\b|\bzoom\b/i, "mdi:aspect-ratio"],
];

/** The icon a label suggests, or null when nothing in it is recognised. */
export function guessDockIcon(label: string): string | null {
  for (const [pattern, icon] of ICON_GUESSES) if (pattern.test(label)) return icon;
  return null;
}

/** Initials for an icon-less shortcut: the first two words' first letters, or the one word's. */
export function dockMonogram(label: string): string {
  const words = label.split(/[\s\-_:/.,+()]+/u).filter(Boolean);
  const first = (word: string) => Array.from(word)[0] ?? "";
  const letters = words.length >= 2 ? first(words[0]) + first(words[1]) : first(words[0] ?? "");
  return letters.toLocaleUpperCase();
}

/** The icon a pinned shortcut shows: the chosen one, else the automatic one (the item's own, a guess, the initials). */
export function dockIconFor(pin: DockPin, label: string, ownIcon: string | null): string {
  return pin.icon ?? ownIcon ?? guessDockIcon(label) ?? DOCK_MONOGRAM;
}

/** The set of shortcuts the remote is on: per activity, or per device in device mode. */
export function dockScope(mode: string, activityId: unknown, deviceId: unknown): string | null {
  const id = mode === "device" ? deviceId : activityId;
  if (id == null || id === "") return null;
  const n = Number(id);
  return Number.isFinite(n) ? `${mode === "device" ? "device" : "activity"}:${n}` : null;
}

export function sameDockRef(a: DockRef, b: DockRef): boolean {
  return a.kind === b.kind && a.commandId === b.commandId && a.deviceId === b.deviceId;
}

export function dockIsFull(slots: DockSlots): boolean {
  return DOCK_SLOTS.every((slot) => slots[slot] != null);
}

export function dockSlotOf(slots: DockSlots, ref: DockRef): DockSlot | null {
  return DOCK_SLOTS.find((slot) => slots[slot] && sameDockRef(slots[slot]!, ref)) ?? null;
}

/** Pins into the first free slot (left, then right); null when both are taken. */
export function pinToFreeSlot(slots: DockSlots, ref: DockRef): { slots: DockSlots; slot: DockSlot } | null {
  const slot = DOCK_SLOTS.find((s) => !slots[s]);
  if (!slot) return null;
  return { slots: { ...slots, [slot]: pinOf(ref) }, slot };
}

export function unpinDockSlot(slots: DockSlots, slot: DockSlot): DockSlots {
  const next = { ...slots };
  delete next[slot];
  return next;
}

/** Moves a shortcut to the other side; one already there trades places with it. */
export function moveDockSlot(slots: DockSlots, from: DockSlot, to: DockSlot): DockSlots {
  if (from === to || !slots[from]) return slots;
  const next: DockSlots = { ...slots, [to]: slots[from] };
  if (slots[to]) next[from] = slots[to];
  else delete next[from];
  return next;
}

export function setDockIcon(slots: DockSlots, slot: DockSlot, icon: string | null): DockSlots {
  const pin = slots[slot];
  return pin ? { ...slots, [slot]: { ...pin, icon } } : slots;
}

function pinOf(ref: DockRef): DockPin {
  return { kind: ref.kind, commandId: ref.commandId, deviceId: ref.deviceId, icon: null };
}

// ---------- storage: one JSON table per remote entity, keyed by scope ----------

export type DockTable = Record<string, DockSlots>;

function cleanPin(value: unknown): DockPin | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const kind = v.kind;
  if (kind !== "favorites" && kind !== "macros" && kind !== "commands") return null;
  const commandId = Number(v.commandId);
  const deviceId = Number(v.deviceId);
  if (!Number.isFinite(commandId) || !Number.isFinite(deviceId)) return null;
  const icon = typeof v.icon === "string" && v.icon ? v.icon : null;
  return { kind, commandId, deviceId, icon };
}

/** A stored table, keeping only well-formed pins (storage is the user's to edit or corrupt). */
export function parseDockTable(raw: string | null): DockTable {
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const table: DockTable = {};
  for (const [scope, slots] of Object.entries(parsed as Record<string, unknown>)) {
    if (!slots || typeof slots !== "object") continue;
    const clean: DockSlots = {};
    for (const slot of DOCK_SLOTS) {
      const pin = cleanPin((slots as Record<string, unknown>)[slot]);
      if (pin) clean[slot] = pin;
    }
    if (clean.left || clean.right) table[scope] = clean;
  }
  return table;
}

/** The table with one scope's slots replaced; a scope left empty is dropped. */
export function withDockSlots(table: DockTable, scope: string, slots: DockSlots): DockTable {
  const next = { ...table };
  if (slots.left || slots.right) next[scope] = slots;
  else delete next[scope];
  return next;
}

export function readDockTable(entity: string): DockTable {
  try {
    return typeof window === "undefined" ? {} : parseDockTable(window.localStorage.getItem(`${DOCK_STORAGE_PREFIX}${entity}`));
  } catch {
    return {};
  }
}

export function writeDockTable(entity: string, table: DockTable): void {
  try {
    if (typeof window === "undefined") return;
    const key = `${DOCK_STORAGE_PREFIX}${entity}`;
    if (Object.keys(table).length) window.localStorage.setItem(key, JSON.stringify(table));
    else window.localStorage.removeItem(key);
  } catch {
    /* storage unavailable: the shortcuts last until the page reloads */
  }
}
