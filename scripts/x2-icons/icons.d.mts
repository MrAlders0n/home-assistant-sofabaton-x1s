// Types for icons.mjs (see geometry.d.mts): icon id -> [name, draw], where
// draw takes the geometry module and returns the parts geometry.build joins.

type IconTable = Readonly<Record<number, readonly [name: string, draw: (geometry: unknown) => unknown[]]>>;

/** The X2 set, keyed by X2 icon id (1-45). */
export const ICONS: IconTable;
/** The X1 icons without an X2 counterpart, keyed by X1 icon id. */
export const X1_ICONS: IconTable;
