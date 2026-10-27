// The body of the Edit activity / Edit device dialog: the tile beside the
// name field, the icon (and X2 color) picker on demand so the dialog stays
// the size of a rename until it is wanted. The editor and the Wifi Commands
// device view share it; each keeps its own dialog frame and state.

import { css, html, nothing, type TemplateResult } from "lit";
import { entityColor, entityColorHex, entityColorsSupported } from "../../../../../remote-card/src/entity-icons/entity-colors";
import { entityTile, renderEntityTile, type EntityTile } from "../../../../../remote-card/src/entity-icons/entity-tile";
import { renderEntityColorPicker, renderEntityIconPicker } from "../../../../../remote-card/src/entity-icons/entity-picker";
import { TOOLS_CARD_STRINGS } from "../strings";

export type AppearanceTab = "icon" | "color";

/** What the dialog shows; null = the record carries none (drawn as icon 1,
 *  color 0). */
export interface AppearanceDraft {
  icon: number | null;
  colorId: number | null;
}

export function renderAppearanceEditor(params: {
  hubVersion: string | null | undefined;
  draft: AppearanceDraft;
  open: boolean;
  tab: AppearanceTab;
  nameField: TemplateResult;
  onToggle: () => void;
  onTab: (tab: AppearanceTab) => void;
  onPick: (draft: AppearanceDraft) => void;
}): TemplateResult {
  const S = TOOLS_CARD_STRINGS.backup;
  const version = params.hubVersion;
  const colors = entityColorsSupported(version);
  const draft = params.draft;
  const icon = draft.icon ?? 1;
  const colorId = draft.colorId ?? 0;
  const tile = entityTile(version, icon, colorId);
  const changeLabel = colors ? S.changeIconAndColor : S.changeIcon;
  const tab = colors ? params.tab : "icon";
  return html`
    <div class="appearance-ident">
      <button class="appearance-trigger" title=${changeLabel} aria-label=${changeLabel} aria-expanded=${params.open ? "true" : "false"} @click=${params.onToggle}>
        ${tile ? renderEntityTile(tile) : nothing}
        <span class="appearance-trigger-edit" aria-hidden="true"><ha-icon icon="mdi:pencil"></ha-icon></span>
      </button>
      <div class="appearance-name">${params.nameField}</div>
    </div>
    ${params.open
      ? html`
          ${colors
            ? html`<div class="appearance-tabs" role="tablist">
                <button role="tab" class="appearance-tab${tab === "icon" ? " active" : ""}" aria-selected=${tab === "icon" ? "true" : "false"} @click=${() => params.onTab("icon")}>
                  <ha-icon icon="mdi:shape-outline"></ha-icon><span>${S.appearanceIcon}</span>
                </button>
                <button role="tab" class="appearance-tab${tab === "color" ? " active" : ""}" aria-selected=${tab === "color" ? "true" : "false"} @click=${() => params.onTab("color")}>
                  <span class="appearance-dot" style=${`--entity-swatch: ${entityColorHex(colorId)}`}></span><span>${S.entityColorName(colorId >= 1 ? colorId : 1)}</span>
                </button>
              </div>`
            : nothing}
          <div class="appearance-pane">
            ${tab === "icon"
              ? renderEntityIconPicker({
                  hubVersion: version,
                  icon,
                  color: entityColor(version, colorId),
                  servicesLabel: S.appearanceServices,
                  onPick: (picked) => params.onPick({ ...draft, icon: picked }),
                })
              : renderEntityColorPicker({
                  colorId,
                  colorName: (id) => S.entityColorName(id),
                  onPick: (picked) => params.onPick({ ...draft, colorId: picked }),
                })}
          </div>
        `
      : html`<button class="appearance-reveal" aria-expanded="false" @click=${params.onToggle}>
          <ha-icon icon=${colors ? "mdi:palette-outline" : "mdi:shape-outline"}></ha-icon><span>${changeLabel}</span><ha-icon icon="mdi:chevron-down"></ha-icon>
        </button>`}
  `;
}

/** The entity's tile beside a detail view's title (the editors, a Wifi
 *  Device): it opens the edit dialog with the picker showing. A narrow card
 *  hides it; the pencil opens the same dialog. */
export function renderDetailEntityTile(
  tile: EntityTile,
  params: { label: string; disabled?: boolean; onClick: () => void },
): TemplateResult {
  return html`<button class="detail-entity-tile" title=${params.label} aria-label=${params.label} ?disabled=${params.disabled ?? false} @click=${params.onClick}>
    ${renderEntityTile(tile)}
  </button>`;
}

/** Pair with entityTileStyles and entityPickerStyles. */
export const appearanceEditorStyles = css`
  .detail-entity-tile {
    flex: 0 0 auto;
    display: inline-flex;
    border: 0;
    padding: 0;
    background: none;
    border-radius: 11px;
    cursor: pointer;
    --entity-tile-size: 36px;
  }
  .detail-entity-tile:disabled { cursor: default; }
  .detail-entity-tile:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .detail-entity-tile:hover:not(:disabled) .entity-tile { box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-color) 35%, transparent); }
  @container sofabaton-card (max-width: 420px) {
    .detail-entity-tile { display: none; }
  }
  .appearance-ident { display: flex; align-items: center; gap: 14px; }
  .appearance-name { flex: 1; min-width: 0; }
  .appearance-name > ha-input, .appearance-name > ha-textfield { display: block; width: 100%; }
  .appearance-trigger {
    position: relative;
    flex: 0 0 auto;
    display: inline-flex;
    border: 0;
    padding: 0;
    background: none;
    border-radius: 15px;
    cursor: pointer;
    --entity-tile-size: 50px;
  }
  .appearance-trigger:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .appearance-trigger-edit {
    position: absolute;
    inset-inline-end: -6px;
    bottom: -6px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--ha-card-background, var(--card-background-color));
    color: var(--secondary-text-color);
    box-shadow: 0 0 0 1px var(--divider-color);
    --mdc-icon-size: 13px;
  }
  .appearance-reveal {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 0;
    background: none;
    padding: 4px 2px;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    color: color-mix(in srgb, var(--primary-color) 70%, var(--primary-text-color));
    cursor: pointer;
    --mdc-icon-size: 18px;
  }
  .appearance-tabs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 3px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--primary-text-color) 7%, transparent);
  }
  .appearance-tab {
    height: 32px;
    border: 0;
    border-radius: 999px;
    background: none;
    font: inherit;
    font-size: 13px;
    color: var(--secondary-text-color);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    cursor: pointer;
    --mdc-icon-size: 16px;
  }
  .appearance-tab.active {
    background: var(--ha-card-background, var(--card-background-color));
    color: var(--primary-text-color);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  }
  .appearance-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--entity-swatch); }
  .appearance-pane { max-height: 232px; overflow-y: auto; padding: 5px; margin: -5px; }
`;
