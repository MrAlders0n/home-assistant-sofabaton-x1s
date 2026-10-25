// Types for icons.mjs (see geometry.d.mts): icon id -> [name, draw], where
// draw takes the geometry module and returns the parts geometry.build joins.

export const ICONS: Readonly<Record<number, readonly [name: string, draw: (geometry: unknown) => unknown[]]>>;
