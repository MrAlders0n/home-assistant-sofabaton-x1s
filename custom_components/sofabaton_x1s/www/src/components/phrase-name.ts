import { css, html, nothing } from "lit";

/** Phrases interpolate a user-chosen name (Wifi Event, activity, the
 *  sidebar panel) into a translated sentence, and translations may place
 *  it anywhere (zh wraps it in quotes). Render via a sentinel split so
 *  the name gets its own chip span without touching the translations;
 *  `after` lands right behind the chip (the Settings tab's rename pencil). */
const NAME_SENTINEL = "\u0000";
export function phraseWithName(phrase: (name: string) => string, name: string, after: unknown = nothing) {
  const [head, tail = ""] = phrase(NAME_SENTINEL).split(NAME_SENTINEL);
  return html`${head}<span class="phrase-name">${name}</span>${after}${tail}`;
}

/** The chip: a subtle tint that marks the name as the user's own rather
 *  than part of the sentence. */
export const phraseNameStyles = css`
  .phrase-name { background: color-mix(in srgb, var(--secondary-text-color) 14%, transparent); border-radius: 4px; padding: 0 4px; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
`;
