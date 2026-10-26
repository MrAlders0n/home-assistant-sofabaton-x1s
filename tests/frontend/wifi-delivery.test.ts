/**
 * How Wifi Command presses reach Home Assistant, in the control panel
 * (docs/internal/wifi-webhook-transport-plan.md, wave 3): the path line and
 * the TCP flow to allow, the delivery panel, Test again in the Wifi tab, the
 * create dialog's MQTT check, and the hub's broker form.
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

const LISTENER_INFO: WifiDeliveryInfo = {
  device_key: "lights",
  transport: "http",
  target: "http://192.168.2.50:8060",
  hub_ip: "192.168.2.123",
  delivery: { status: "delivered", transport: "http" },
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
  assert.equal(deployedTransport("http"), "http");
  // A device the 0.7.2 test releases deployed as Direct has no path any more.
  assert.equal(deployedTransport("webhook"), null);
  assert.equal(deployedTransport("bogus"), null);
  assert.equal(targetHostPort("https://192.168.2.50:8123"), "192.168.2.50:8123");
  assert.equal(targetHostPort("core-mosquitto:1883"), "core-mosquitto:1883");
  assert.equal(targetPort("http://192.168.2.50:8060"), "8060");

  assert.match(deliveryPathText(LISTENER_INFO), /listener at http:\/\/192\.168\.2\.50:8060/);
  assert.match(deliveryAllowText(LISTENER_INFO), /port 8060 on the Home Assistant host; with Docker, publish port 8060/);
  assert.equal(deliveryPathText({ transport: "webhook", target: "https://192.168.2.50:8123" }), "");
  const mqtt = { transport: "mqtt", target: "core-mosquitto:1883", hub_ip: "192.168.2.123" };
  assert.match(deliveryPathText(mqtt), /listens on core-mosquitto:1883/);
  assert.match(deliveryAllowText(mqtt), /same broker Home Assistant uses \(core-mosquitto:1883\)/);
  assert.match(deliveryDocUrl("mqtt"), /networking\.md#optional--mqtt-delivery-x2$/);
  assert.match(deliveryDocUrl("http"), /networking\.md#optional--wifi-commands-and-wifi-events$/);

  assert.equal(deliveryFailed({ status: "not_delivered" }), true);
  assert.equal(deliveryFailed({ status: "not_sent" }), true);
  assert.equal(deliveryFailed({ status: "delivered" }), false);
  assert.equal(deliveryFailed(null), false);
  assert.equal(formatLatency(1.234), "1.2");
});

test("the delivery panel stays quiet on success and guides on failure", () => {
  setToolsCardLanguage("en");
  const S = TOOLS_CARD_STRINGS.wifiDelivery;
  const base = { busy: null, locked: false, lastTest: null, error: "", onTest() {} };
  // Not deployed: no panel.
  assert.equal(templateText(renderWifiDeliveryPanel({ ...base, info: { ...LISTENER_INFO, transport: null } })), "");
  const ok = templateText(renderWifiDeliveryPanel({ ...base, info: LISTENER_INFO }));
  assert.match(ok, new RegExp(S.title));
  assert.match(ok, /Test again/);
  assert.doesNotMatch(ok, new RegExp(S.notDelivered));

  const failed = templateText(renderWifiDeliveryPanel({
    ...base,
    info: { ...LISTENER_INFO, delivery: { status: "not_delivered", transport: "http" } },
  }));
  assert.match(failed, new RegExp(S.notDelivered));
  assert.match(failed, /Allow TCP from the hub \(192\.168\.2\.123\) to port 8060/);
  assert.match(failed, /Networking guide/);

  const passed = templateText(renderWifiDeliveryPanel({ ...base, info: LISTENER_INFO, lastTest: { status: "delivered", latency_s: 0.42 } }));
  assert.match(passed, /Test command received \(0\.4 s\)/);
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
    return LISTENER_INFO;
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
  assert.equal(tab._deliveryInfo.target, "http://192.168.2.50:8060");
});

test("Test again presses through the backend and shows the verdict", async () => {
  const types: string[] = [];
  const tab = makeTab(async (msg) => {
    types.push(String(msg.type));
    if (msg.type === "sofabaton_x1s/command_config/test_delivery") return { status: "delivered", latency_s: 1.5 };
    if (msg.type === "sofabaton_x1s/command_devices/list") return { devices: tab._wifiDevices };
    return LISTENER_INFO;
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
    if (msg.type === "sofabaton_x1s/command_sync/run") {
      return { status: "success", delivery: { status: "not_delivered", transport: "http" } };
    }
    if (msg.type === "sofabaton_x1s/command_devices/list") return { devices: tab._wifiDevices };
    return {};
  });
  tab.showCompletion = (notice: { tone: string; label: string }) => notices.push(notice);
  tab._wifiDevices = [{ device_key: "lights", device_name: "Lights", deployed_device_id: 9 }];
  tab._selectedDeviceKey = "lights";
  await tab._startCommandConfigSync();
  assert.deepEqual(notices, [{ tone: "error", label: TOOLS_CARD_STRINGS.wifiDelivery.syncedNotDeliveredDock }]);
});

test("new devices default to MQTT where it is offered; MQTT waits for the broker link", async () => {
  const plain = makeTab(async () => LINK);
  plain._openCreateDeviceModal();
  assert.equal(plain._newDeviceTransport, "http");
  assert.equal(plain._mqttChoiceBlocked(), false);

  const tab = makeTab(async () => LINK);
  tab._mqttAvailable = true;
  tab._openCreateDeviceModal();
  assert.equal(tab._newDeviceTransport, "mqtt");
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
  // Disconnected does not mean unconfigured: warn before replacing saved settings.
  assert.match(templateText(form.render()), /replaces any MQTT broker settings/);
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
  assert.match(templateText(form.render()), /replaces any MQTT broker settings/);
});

test("the broker form refuses a host the hub cannot store before sending", async () => {
  const sent: Array<Record<string, unknown>> = [];
  const form = makeForm(async (msg) => { sent.push(msg); return LINK; });
  form.state = { ...LINK, suggested_host: "" };
  form.openFor("entry-1");
  await form.submit();
  assert.equal(sent.length, 0);
  assert.match(templateText(form.render()), /Enter a valid IPv4 address for the broker/);
});

test("latency uses the selected locale and never turns missing data into zero", () => {
  for (const locale of ["de", "es", "fr", "nl"]) {
    setToolsCardLanguage(locale);
    assert.equal(formatLatency(0.84), "0,8", locale);
  }
  for (const locale of ["en", "en-GB", "zh-hans"]) {
    setToolsCardLanguage(locale);
    assert.equal(formatLatency(0.84), "0.8", locale);
  }
  assert.equal(formatLatency(null), "?");
  assert.equal(formatLatency(undefined), "?");
  assert.equal(formatLatency(Number.NaN), "?");
  assert.equal(formatLatency(0), "0.0");
  assert.equal(formatLatency(0.84, "bad_locale"), "0.8");
  setToolsCardLanguage("en");
});

test("an unsent test does not claim a delivery failure or prescribe firewall changes", () => {
  setToolsCardLanguage("en");
  const notices: any[] = [];
  const tab = makeTab(async () => ({}));
  tab.showCompletion = (notice: unknown) => notices.push(notice);
  tab._announceDeployResult({ status: "not_sent" }, "Synced", "entry-1");
  assert.equal(notices[0].label, "Synced, but delivery could not be tested.");
  const text = templateText(renderWifiDeliveryPanel({
    info: { ...LISTENER_INFO, delivery: { status: "not_sent" } },
    busy: null, locked: false, lastTest: null, error: "", onTest() {},
  }));
  assert.match(text, /Could not send the test command/);
  assert.doesNotMatch(text, /did not reach|allow TCP/);
});

test("broker validation rejects invalid IPv4 octets and ports without sending", async () => {
  setToolsCardLanguage("en");
  let sent = false;
  const form = makeForm(async () => { sent = true; return {}; });
  form.state = LINK;
  for (const host of ["999.999.999.999", "192.168.001.1", "broker.local"]) {
    form.openFor("entry-1");
    (form as any)._host = host;
    await form.submit();
    assert.match(templateText(form.render()), /sb-broker-host-error/);
  }
  for (const port of [0, 65536, 1883.5, Number.NaN]) {
    form.openFor("entry-1");
    (form as any)._port = port;
    await form.submit();
    assert.match(templateText(form.render()), /sb-broker-port-error/);
    assert.match(templateText(form.render()), /1 to 65535/);
  }
  assert.equal(sent, false);
});

test("broker failures are localized from codes, with visible field errors and a safe fallback", async () => {
  for (const locale of ["de", "es", "fr", "nl", "zh-hans"]) {
    setToolsCardLanguage(locale);
    const S = TOOLS_CARD_STRINGS.wifiDelivery;
    const cases: Array<[string, string, string | null]> = [
      ["broker_invalid_host", S.brokerHostRequired, "host"],
      ["broker_invalid_port", S.brokerPortInvalid, "port"],
      ["broker_username_too_long", S.brokerUsernameTooLong, "username"],
      ["broker_password_too_long", S.brokerPasswordTooLong, "password"],
      ["broker_credentials_too_long", S.brokerCredentialsTooLong, null],
      ["unknown", S.brokerFailed, null],
      ["invalid_format", S.brokerFailed, null],
      ["hub_no_answer", S.brokerNoAnswer, null],
    ];
    for (const [code, expected, field] of cases) {
      const form = makeForm(async () => { throw { code, message: "Backend diagnostic must not appear" }; });
      form.state = LINK;
      form.openFor("entry-1");
      (form as any)._useHaLogin = false;
      await form.submit();
      const text = templateText(form.render());
      assert.ok(text.includes(expected), `${locale}: ${code}`);
      assert.doesNotMatch(text, /Backend diagnostic/);
      if (field) assert.ok(text.includes(`sb-broker-${field}-error`));
    }
    // Stored HA credentials have no editable field: their error must remain visible.
    const form = makeForm(async () => { throw { code: "broker_password_too_long" }; });
    form.state = LINK;
    form.openFor("entry-1");
    await form.submit();
    assert.ok(templateText(form.render()).includes(S.brokerPasswordTooLong));
  }
  setToolsCardLanguage("en");
});

test("the replacement warning is present for connected, disconnected and unknown hubs", () => {
  setToolsCardLanguage("en");
  for (const status of [true, false, null]) {
    const form = makeForm(async () => ({}));
    form.state = { ...LINK, hub_connected: status };
    form.openFor("entry-1");
    assert.ok(templateText(form.render()).includes(TOOLS_CARD_STRINGS.wifiDelivery.brokerReplaceWarning));
  }
});
