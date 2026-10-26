// Activity/device icons per hub line. Every line stores the icon as one
// byte in the entity record, but the vendor app offers a different set per
// line: the X2 45 icons, the X1S the first 16 of them (same ids, same
// meaning, older artwork), the X1 25 icons numbered on their own. The app
// shows icon 1 (a TV) for an id outside its line's set, and so does
// entityIconPath.

import { X1_ICON_PATHS, X2_ICON_PATHS } from "./x2-icons";

export type IconHubLine = "X1" | "X1S" | "X2";

/** How many icons each line's app offers; ids run 1..count. */
export const ENTITY_ICON_COUNT: Readonly<Record<IconHubLine, number>> = { X1: 25, X1S: 16, X2: 45 };

/** X1 icon ids drawn with an X2 icon: the same object, or the X2's nearest
 *  one (2 HD TV, 3 TV on a stand, 11 gamepad, 19 speaker, 21 bulb, 24 disc
 *  player). The other X1 ids have their own drawings in X1_ICON_PATHS. */
const X1_ICON_AS_X2: Readonly<Record<number, number>> = {
  1: 1, 2: 18, 3: 32, 4: 2, 5: 3, 9: 7, 11: 6, 13: 13, 15: 14, 16: 16, 19: 25, 20: 9, 21: 12, 23: 10, 24: 22,
};

/** The hub line a hub version names ("X1" / "X1S" / "X2", any case, banner
 *  suffixes allowed), or null when it names none of them. */
export function iconHubLine(hubVersion: string | null | undefined): IconHubLine | null {
  const version = String(hubVersion ?? "").trim().toUpperCase();
  if (version.includes("X1S")) return "X1S";
  if (version.includes("X2")) return "X2";
  if (version.includes("X1")) return "X1";
  return null;
}

/** The icon ids an icon picker offers on this hub, in the app's order. */
export function entityIconIds(hubVersion: string | null | undefined): number[] {
  const line = iconHubLine(hubVersion);
  return line ? Array.from({ length: ENTITY_ICON_COUNT[line] }, (_, i) => i + 1) : [];
}

/** The 24x24 path for the icon an activity/device record stores, as this
 *  hub's app shows it; null only when the hub version names no line. */
export function entityIconPath(hubVersion: string | null | undefined, iconId: number): string | null {
  const line = iconHubLine(hubVersion);
  if (!line) return null;
  const id = Number.isInteger(iconId) && iconId >= 1 && iconId <= ENTITY_ICON_COUNT[line] ? iconId : 1;
  if (line === "X1") return X1_ICON_PATHS[id] ?? X2_ICON_PATHS[X1_ICON_AS_X2[id]];
  return X2_ICON_PATHS[id];
}
