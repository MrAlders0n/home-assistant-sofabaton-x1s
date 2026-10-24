/**
 * How Wifi Command presses reach Home Assistant, in the control panel
 * (docs/internal/wifi-webhook-transport-plan.md, wave 3): the path line and
 * the TCP flow to allow, the delivery panel, Test again / Move to direct
 * delivery in the Wifi tab, the create dialog's MQTT check, and the hub's
 * broker form.
 */
import test from "node:test";
import assert from "node:assert/strict";
import "../../custom_components/sofabaton_x1s/www/src/tabs/wifi-commands-tab";
import "../../custom_components/sofabaton_x1s/www/src/control-panel-translations";
import { setToolsCardLanguage, TOOLS_CARD_STRINGS } from "../../custom_components/sofabaton_x1s/www/src/strings";
import {
  deliveryAllowText,
  deliveryDocUrl,
  deliveryFailed,
  deliveryPathText,
  deployedTransport,
  formatLatency,
  targetHostPort,
  targetPort,
} from "../../custom_components/sofabaton_x1s/www/src/shared/utils/wifi-delivery";
import { renderWifiDeliveryPanel } from "../../custom_components/sofabaton_x1s/www/src/components/wifi-delivery-panel";
import { HubBrokerFormController } from "../../custom_components/sofabaton_x1s/www/src/components/hub-broker-form";
import type {
  HubMqttLinkState,
  WifiDeliveryInfo,
} from "../../custom_components/sofabaton_x1s/www/src/shared/ha-context";

const WifiCommandsTabElement = customElements.get("sofabaton-wifi-commands-tab") as {
  new (): HTMLElement;
};

function templateText(template: unknown): string {
  if (typeof template === "string") return template;
  if (Array.isArray(template)) return template.map(templateText).join("");
  if (template && typeof template === "object") {
    const maybeTemplate = template as { strings?: unknown[]; values?: unknown[] };
    const strings = maybeTemplate.strings ?? [];
    const values = maybeTemplate.values ?? [];
    let text = "";
    for (let index = 0; index < strings.length; index += 1) {
      text += templateText(strings[index]);
      if (index < values.length) text += templateText(values[index]);
    }
    return text;
  }
  return "";
}

const WEBHOOK_INFO: WifiDeliveryInfo = {
  device_key: "lights",
  transport: "webhook",
  target: "https://192.168.2.50:8123",
  target_stale: false,
  hub_ip: "192.168.2.123",
  delivery: { status: "delivered", transport: "webhook" },
  webhook_available: true,
  move_available: false,
};

const LINK: HubMqttLinkState = {
  available: true,
  ha_connected: true,
  ha_broker: { host: "core-mosquitto", port: 1883, tls: false },
  hub_connected: false,
  suggested_host: "192.168.2.50",
  suggested_port: 1883,
  ha_username: "ha",
  ha_login_available: true,
};

test("the path line and the flow to allow name the actual addresses", () => {
  setToolsCardLanguage("en");
  assert.equal(deployedTransport("webhook"), "webhook");
  assert.equal(deployedTransport("bogus"), null);
  assert.equal(targetHostPort("https://192.168.2.50:8123"), "192.168.2.50:8123");
  assert.equal(targetHostPort("core-mosquitto:1883"), "core-mosquitto:1883");
  assert.equal(targetPort("http://192.168.2.50:8060"), "8060");

  assert.equal(deliveryPathText(WEBHOOK_INFO), "The hub calls Home Assistant at https://192.168.2.50:8123.");
  assert.equal(
    deliveryAllowText(WEBHOOK_INFO),
    "If the hub is on another network or VLAN, allow TCP from the hub (192.168.2.123) to 192.168.2.50:8123.",
  );
  const listener = { transport: "http", target: "http://192.168.2.50:8060", hub_ip: "192.168.2.123" };
  assert.match(deliveryPathText(listener), /listener at http:\/\/192\.168\.2\.50:8060/);
  assert.match(deliveryAllowText(listener), /port 8060 on the Home Assistant host; with Docker, publish port 8060/);
  const mqtt = { transport: "mqtt", target: "core-mosquitto:1883", hub_ip: "192.168.2.123" };
  assert.match(deliveryPathText(mqtt), /listens on core-mosquitto:1883/);
  assert.match(deliveryAllowText(mqtt), /same broker Home Assistant uses \(core-mosquitto:1883\)/);
  assert.match(deliveryDocUrl("mqtt"), /networking\.md#optional--mqtt-delivery-x2$/);
  assert.match(deliveryDocUrl("webhook"), /networking\.md#optional--wifi-commands-and-wifi-events$/);

  assert.equal(deliveryFailed({ status: "not_delivered" }), true);
  assert.equal(deliveryFailed({ status: "not_sent" }), true);
  assert.equal(deliveryFailed({ status: "delivered" }), false);
  assert.equal(deliveryFailed(null), false);
  assert.equal(formatLatency(1.234), "1.2");
});

test("the delivery panel stays quiet on success and guides on failure", () => {
  setToolsCardLanguage("en");
  const S = TOOLS_CARD_STRINGS.wifiDelivery;
  const base = { busy: null, locked: false, lastTest: null, error: "", onTest() {}, onMove() {}, onUpdate() {} };
  // Not deployed: no panel.
  assert.equal(templateText(renderWifiDeliveryPanel({ ...base, info: { ...WEBHOOK_INFO, transport: null } })), "");
  const ok = templateText(renderWifiDeliveryPanel({ ...base, info: WEBHOOK_INFO }));
  assert.match(ok, new RegExp(S.title));
  assert.match(ok, /Test again/);
  assert.doesNotMatch(ok, new RegExp(S.notDelivered));
  assert.doesNotMatch(ok, /Move to direct delivery/);

  const failed = templateText(renderWifiDeliveryPanel({
    ...base,
    info: { ...WEBHOOK_INFO, delivery: { status: "not_delivered", transport: "webhook" } },
  }));
  assert.match(failed, new RegExp(S.notDelivered));
  assert.match(failed, /allow TCP from the hub \(192\.168\.2\.123\) to 192\.168\.2\.50:8123/);
  assert.match(failed, /Networking guide/);

  const listener = templateText(renderWifiDeliveryPanel({
    ...base,
    info: { ...WEBHOOK_INFO, transport: "http", target: "http://192.168.2.50:8060", move_available: true, target_stale: true },
  }));
  assert.match(listener, /Move to direct delivery/);
  assert.match(listener, /Update the hub/);

  const passed = templateText(renderWifiDeliveryPanel({ ...base, info: WEBHOOK_INFO, lastTest: { status: "delivered", latency_s: 0.42 } }));
  assert.match(passed, /Presses arrive \(0\.4 s\)/);
});

function makeTab(callWS: (msg: Record<string, unknown>) => Promise<unknown>) {
  const element = new WifiCommandsTabElement() as any;
  element.hub = { entry_id: "entry-1", version: "X2" };
  element.hass = {
    callWS,
    states: { "remote.hub": { attributes: { entry_id: "entry-1" } } },
  };
  return element;
}

test("delivery info is read for deployed devices only", async () => {
  const calls: Array<Record<string, unknown>> = [];
  const tab = makeTab(async (msg) => {
    calls.push(msg);
    return WEBHOOK_INFO;
  });
  tab._wifiDevices = [
    { device_key: "lights", device_name: "Lights", deployed_device_id: 9 },
    { device_key: "fresh", device_name: "Fresh", deployed_device_id: null },
  ];
  tab._selectedDeviceKey = "fresh";
  await tab._loadDeliveryInfo("fresh");
  assert.equal(calls.length, 0);
  assert.equal(tab._deliveryInfo, null);

  tab._selectedDeviceKey = "lights";
  await tab._loadDeliveryInfo("lights");
  assert.deepEqual(calls.map((msg) => msg.type), ["sofabaton_x1s/command_config/delivery"]);
  assert.equal(tab._deliveryInfo.target, "https://192.168.2.50:8123");
});

test("Test again presses through the backend and shows the verdict", async () => {
  const types: string[] = [];
  const tab = makeTab(async (msg) => {
    types.push(String(msg.type));
    if (msg.type === "sofabaton_x1s/command_config/test_delivery") return { status: "delivered", latency_s: 1.5 };
    if (msg.type === "sofabaton_x1s/command_devices/list") return { devices: tab._wifiDevices };
    return WEBHOOK_INFO;
  });
  tab._wifiDevices = [{ device_key: "lights", device_name: "Lights", deployed_device_id: 9 }];
  tab._selectedDeviceKey = "lights";
  await tab._testDelivery("lights");
  assert.equal(types[0], "sofabaton_x1s/command_config/test_delivery");
  assert.equal(tab._deliveryLastTest.delivery.status, "delivered");
  assert.equal(tab._deliveryBusy, null);
});

test("a deploy whose test press did not arrive says so in the dock", async () => {
  setToolsCardLanguage("en");
  const notices: Array<{ tone: string; label: string }> = [];
  const tab = makeTab(async (msg) => {
    if (msg.type === "sofabaton_x1s/command_sync/move_to_direct") {
      return { status: "success", delivery: { status: "not_delivered", transport: "webhook" } };
    }
    if (msg.type === "sofabaton_x1s/command_devices/list") return { devices: tab._wifiDevices };
    return {};
  });
  tab.showCompletion = (notice: { tone: string; label: string }) => notices.push(notice);
  tab._wifiDevices = [{ device_key: "lights", device_name: "Lights", deployed_device_id: 9 }];
  tab._selectedDeviceKey = "lights";
  await tab._startCommandConfigSync("move");
  assert.deepEqual(notices, [{ tone: "error", label: TOOLS_CARD_STRINGS.wifiDelivery.syncedNotDeliveredDock }]);
});

test("new devices default to Direct; MQTT waits for the broker link", async () => {
  const tab = makeTab(async () => LINK);
  tab._mqttAvailable = true;
  tab._openCreateDeviceModal();
  assert.equal(tab._newDeviceTransport, "http");
  assert.equal(tab._mqttChoiceBlocked(), false);
  tab._pickNewDeviceTransport("mqtt");
  // The link is being read: Create waits.
  assert.equal(tab._mqttChoiceBlocked(), true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  // The hub has no broker: MQTT cannot be created until it is set up.
  assert.equal(tab._brokerForm.state.hub_connected, false);
  assert.equal(tab._mqttChoiceBlocked(), true);
  tab._brokerForm.state = { ...LINK, hub_connected: null };
  assert.equal(tab._mqttChoiceBlocked(), false);
});

function makeForm(callWS: (msg: Record<string, unknown>) => Promise<unknown>) {
  const host = { addController() {}, requestUpdate() {}, removeController() {}, updateComplete: Promise.resolve(true) };
  return new HubBrokerFormController(host as any, () => ({ callWS, states: {} }) as any);
}

test("the broker form prefills Home Assistant's broker and never sends its password", async () => {
  const sent: Array<Record<string, unknown>> = [];
  const form = makeForm(async (msg) => {
    sent.push(msg);
    if (msg.type === "sofabaton_x1s/hub_mqtt/status") return LINK;
    return { ...LINK, hub_connected: true, connected: true };
  });
  await form.load("entry-1");
  let done: boolean | null = null;
  form.openFor("entry-1", { onDone: (connected) => { done = connected; } });
  assert.equal(form.open, true);
  assert.match(templateText(form.render()), /192\.168\.2\.50/);
  // No broker yet: no replace warning.
  assert.doesNotMatch(templateText(form.render()), /replaces the hub's current broker/);
  await form.submit();
  const setup = sent.find((msg) => msg.type === "sofabaton_x1s/hub_mqtt/setup")!;
  assert.deepEqual(setup, {
    type: "sofabaton_x1s/hub_mqtt/setup",
    entry_id: "entry-1",
    host: "192.168.2.50",
    port: 1883,
    use_ha_login: true,
  });
  assert.equal(done, true);
  assert.equal(form.state?.hub_connected, true);

  // Change: the hub has a broker that gets replaced.
  form.openFor("entry-1");
  assert.match(templateText(form.render()), /replaces the hub's current broker/);
});

test("the broker form refuses a host the hub cannot store before sending", async () => {
  const sent: Array<Record<string, unknown>> = [];
  const form = makeForm(async (msg) => { sent.push(msg); return LINK; });
  form.state = { ...LINK, suggested_host: "" };
  form.openFor("entry-1");
  await form.submit();
  assert.equal(sent.length, 0);
  assert.match(templateText(form.render()), /Enter the broker's IPv4 address/);
});
