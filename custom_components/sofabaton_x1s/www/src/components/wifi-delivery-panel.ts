// "How presses reach Home Assistant" for one deployed Wifi Device (or the
// Wifi Events device): the path the hub uses, built from what was deployed,
// with Test again next to it. When the last test failed it names the TCP
// flow to allow and links the networking guide
// (docs/internal/wifi-webhook-transport-plan.md §4.4).

import { css, html, nothing } from "lit";
import type { WifiDelivery, WifiDeliveryInfo } from "../shared/ha-context";
import {
  deliveryAllowText,
  deliveryDocUrl,
  deliveryFailed,
  deliveryPathText,
  deployedTransport,
  formatLatency,
} from "../shared/utils/wifi-delivery";
import { TOOLS_CARD_STRINGS } from "../strings";

export type WifiDeliveryBusy = "test" | null;

export const wifiDeliveryStyles = css`
  .delivery-panel {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 14px;
    border: 1px solid var(--divider-color);
    border-radius: var(--tools-radius-sm, 10px);
    background: var(--ha-card-background, var(--card-background-color));
  }
  .delivery-panel.attention {
    border-color: color-mix(in srgb, var(--warning-color, #f59e0b) 45%, var(--divider-color));
  }
  .delivery-head { display: flex; align-items: center; gap: 8px; }
  .delivery-head ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); flex: 0 0 auto; }
  .delivery-title { flex: 1; min-width: 0; font-size: 13px; font-weight: 700; color: var(--primary-text-color); }
  .delivery-path {
    font-size: 12px;
    line-height: 1.45;
    color: var(--secondary-text-color);
    overflow-wrap: anywhere;
  }
  .delivery-alert {
    display: flex;
    gap: 8px;
    padding: 8px 10px;
    border-radius: var(--tools-radius-sm, 10px);
    background: color-mix(in srgb, var(--warning-color, #f59e0b) 12%, transparent);
    color: var(--primary-text-color);
    font-size: 12px;
    line-height: 1.45;
  }
  .delivery-alert ha-icon { --mdc-icon-size: 18px; color: var(--warning-color, #b58a00); flex: 0 0 auto; }
  .delivery-alert-copy { display: flex; flex-direction: column; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
  .delivery-alert-title { font-weight: 700; }
  .delivery-alert a { color: var(--primary-color); }
  .delivery-ok { font-size: 12px; color: var(--success-color, #43a047); }
  .delivery-error { font-size: 12px; color: var(--error-color, #db4437); }
  .delivery-btn {
    border: 1px solid var(--divider-color);
    border-radius: var(--tools-radius-sm, 10px);
    padding: 6px 10px;
    background: transparent;
    color: var(--primary-text-color);
    font: inherit;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
  }
  .delivery-btn:hover:not(:disabled) { border-color: color-mix(in srgb, var(--primary-color) 55%, var(--divider-color)); }
  .delivery-btn:disabled { opacity: 0.45; cursor: default; }
`;

export function renderWifiDeliveryPanel(params: {
  info: WifiDeliveryInfo | null;
  busy: WifiDeliveryBusy;
  /** Another hub operation runs: nothing here may start. */
  locked: boolean;
  /** The result of a Test again pressed here, shown once when it passed. */
  lastTest: WifiDelivery | null;
  error: string;
  onTest: () => void;
}) {
  const info = params.info;
  if (!info || !deployedTransport(info.transport)) return nothing;
  const S = TOOLS_CARD_STRINGS.wifiDelivery;
  const failed = deliveryFailed(info.delivery);
  const disabled = params.locked || params.busy !== null;
  const lastPassed = !failed && params.lastTest?.status === "delivered";
  return html`
    <section class="delivery-panel${failed ? " attention" : ""}">
      <div class="delivery-head">
        <ha-icon icon="mdi:transit-connection-variant"></ha-icon>
        <span class="delivery-title">${S.title}</span>
        <button class="delivery-btn delivery-test-btn" ?disabled=${disabled} @click=${params.onTest}>
          ${params.busy === "test" ? S.testing : S.testAgain}
        </button>
      </div>
      <div class="delivery-path">${deliveryPathText(info)}</div>
      ${failed
        ? html`
            <div class="delivery-alert" role="alert">
              <ha-icon icon="mdi:alert-outline"></ha-icon>
              <div class="delivery-alert-copy">
                <span class="delivery-alert-title">${info.delivery?.status === "not_sent" ? S.notSent : S.notDelivered}</span>
                ${info.delivery?.status === "not_sent" ? nothing : html`<span>${deliveryAllowText(info)}</span>`}
                <a href=${deliveryDocUrl(info.transport)} target="_blank" rel="noreferrer">${S.networkingGuide}</a>
              </div>
            </div>
          `
        : nothing}
      ${lastPassed ? html`<div class="delivery-ok">${S.delivered(formatLatency(params.lastTest?.latency_s))}</div>` : nothing}
      ${params.error ? html`<div class="delivery-error" role="alert">${params.error}</div>` : nothing}
    </section>
  `;
}
