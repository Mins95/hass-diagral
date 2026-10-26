/** Offline tests: no Home Assistant, browser, network or alarm command. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

class ElementStub {
  constructor(tag = "element") {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.events = [];
  }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.events.push([name, listener]); }
  dispatchEvent(event) { this.dispatched = event; return true; }
  attachShadow() { this.shadowRoot = new ElementStub("shadow"); return this.shadowRoot; }
}

const timers = new Map();
const timerDelays = new Map();
let nextTimer = 1;
const registry = new Map();
globalThis.HTMLElement = ElementStub;
globalThis.document = { createElement: (name) => new ElementStub(name) };
globalThis.customElements = {
  get: (name) => registry.get(name),
  define: (name, constructor) => registry.set(name, constructor),
};
globalThis.setTimeout = (callback, delay) => { const id = nextTimer++; timerDelays.set(id, delay); timers.set(id, () => { timers.delete(id); timerDelays.delete(id); callback(); }); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); timerDelays.delete(id); };
globalThis.fetch = () => { throw new Error("The card must not use the network"); };
globalThis.WebSocket = class { constructor() { throw new Error("The card must not open a socket"); } };

const sourcePath = fileURLToPath(new URL("./diagral-overview-card.js", import.meta.url));
const source = await readFile(sourcePath, "utf8");
const { buildOverview, formatDuration, DiagralOverviewCard } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);
const now = Date.parse("2026-10-26T12:00:00Z");
const config = {
  alarm_entity: "alarm_control_panel.fake_central",
  anomalies_entity: "sensor.fake_anomalies",
  arming_end_entity: "sensor.fake_arming_ends_at",
};
function hassFor(alarmState = "arming", end = "2026-10-26T12:01:30Z", attributes = {}) {
  return {
    states: {
      [config.alarm_entity]: { state: alarmState, attributes: { arming_timing_known: true, arming_source: "command", arming_duration: 90 } },
      [config.arming_end_entity]: { state: end, attributes: {} },
      [config.anomalies_entity]: { state: "0", attributes: { fault_count: 0, affected_device_count: 0, faults: [], last_success: "2026-10-26T12:00:00Z", updated_at: "2026-10-26T12:00:00Z", data_fresh: true, ...attributes } },
    },
    callService() { throw new Error("The card must not send alarm commands"); },
  };
}
function textTree(element) {
  return [element.textContent || "", ...element.children.map(textTree)].join(" ");
}
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test("known timing counts down from the published deadline", () => {
  const model = buildOverview(config, hassFor(), now + 18_000);
  assert.equal(model.arming.remaining, 72);
  assert.equal(model.arming.kind, "countdown");
  assert.match(model.arming.text, /1 min 12 s/);
  assert.match(model.arming.text, /estim|environ/);
});
test("a missed beginning does not invent a new ninety-second deadline", () => {
  const hass = hassFor();
  hass.states[config.alarm_entity].attributes.arming_timing_known = false;
  const model = buildOverview(config, hass, now);
  assert.equal(model.arming.kind, "unknown");
  assert.equal(model.arming.remaining, null);
  assert.match(model.arming.text, /inconnu/);
});
test("an unavailable deadline remains unknown even with stale panel attributes", () => {
  const hass = hassFor("arming", "unavailable");
  hass.states[config.alarm_entity].attributes.arming_ends_at = "2026-10-26T12:01:30Z";
  assert.equal(buildOverview(config, hass, now).arming.kind, "unknown");
});
test("an ISO date without a timezone is refused", () => {
  assert.equal(buildOverview(config, hassFor("arming", "2026-10-26T12:01:30"), now).arming.kind, "unknown");
});
test("zero seconds waits for central confirmation and never marks armed", () => {
  const model = buildOverview(config, hassFor(), now + 91_000);
  assert.equal(model.alarm.state, "arming");
  assert.equal(model.arming.kind, "awaiting_confirmation");
  assert.match(model.arming.text, /confirmation/);
});
test("disarm ignores a stale deadline", () => {
  assert.equal(buildOverview(config, hassFor("disarmed"), now).arming.kind, "inactive");
});
test("an unavailable alarm never shows an active countdown", () => {
  assert.equal(buildOverview(config, hassFor("unavailable"), now).arming.kind, "inactive");
});
test("all faults on one device are retained", () => {
  const faults = [{ device_name: "Fake detector", group_name: "Fake group", faults: [{ code: "power", name: "Alimentation" }, { code: "tamper", name: "Autoprotection" }] }];
  const model = buildOverview(config, hassFor("disarmed", "unknown", { faults, fault_count: 2, affected_device_count: 1 }), now);
  assert.equal(model.anomalies.groups.length, 1);
  assert.deepEqual(model.anomalies.groups[0].faults, ["Alimentation", "Autoprotection"]);
  assert.equal(model.anomalies.count, 2);
  assert.equal(model.anomalies.affected, 1);
});
test("unknown anomaly information never becomes all clear", () => {
  const hass = hassFor();
  hass.states[config.anomalies_entity].state = "unavailable";
  const model = buildOverview(config, hass, now);
  assert.equal(model.anomalies.available, false);
  assert.equal(model.anomalies.count, 0);
  assert.doesNotMatch(model.anomalies.text, /aucun défaut/i);
});
test("a snapshot older than ten minutes does not claim the installation is clear", () => {
  const model = buildOverview(config, hassFor("disarmed"), now + 601_000);
  assert.equal(model.anomalies.freshness, "stale");
  assert.equal(model.anomalies.count, 0);
  assert.match(model.anomalies.text, /données anciennes/i);
  assert.doesNotMatch(model.anomalies.text, /aucun défaut/i);
});
test("the stale threshold can be configured", () => {
  const model = buildOverview({ ...config, stale_after_seconds: 60 }, hassFor("disarmed"), now + 61_000);
  assert.equal(model.anomalies.freshness, "stale");
});
test("explicitly stale data retains every last known fault", () => {
  const faults = [{ device_name: "Fake detector", faults: [{ code: "power", name: "Alimentation" }, { code: "tamper", name: "Autoprotection" }] }];
  const model = buildOverview(config, hassFor("disarmed", "unknown", { faults, fault_count: 2, affected_device_count: 1, data_fresh: false }), now);
  assert.equal(model.anomalies.freshness, "stale");
  assert.equal(model.anomalies.count, 2);
  assert.deepEqual(model.anomalies.groups[0].faults, ["Alimentation", "Autoprotection"]);
  assert.match(model.anomalies.text, /dernier relevé/i);
});
test("missing and future timestamps never turn zero faults into an all-clear", () => {
  for (const value of [undefined, "invalid", "2026-10-26T13:00:00Z"]) {
    const model = buildOverview(config, hassFor("disarmed", "unknown", { last_success: value, updated_at: value }), now);
    assert.equal(model.anomalies.freshness, "unknown");
    assert.doesNotMatch(model.anomalies.text, /aucun défaut/i);
  }
});
test("malformed fault records do not throw or hide known fault count", () => {
  const model = buildOverview(config, hassFor("disarmed", "unknown", { faults: [null, {}, { faults: "bad" }], fault_count: 2, affected_device_count: 1 }), now);
  assert.equal(model.anomalies.count, 2);
  assert.match(model.anomalies.text, /2 défauts/);
  assert.equal(model.anomalies.groups.length, 0);
});
test("equipment names remain text, including HTML-like input", () => {
  const card = new DiagralOverviewCard();
  card.setConfig(config);
  card.hass = hassFor("disarmed", "unknown", { faults: [{ device_name: "<img src=x onerror=alert(1)>", faults: [{ name: "<script>bad()</script>" }] }], fault_count: 1, affected_device_count: 1 });
  const rendered = textTree(card.shadowRoot);
  assert.match(rendered, /<img src=x onerror=alert\(1\)>/);
  assert.match(rendered, /<script>bad\(\)<\/script>/);
  assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML/);
});
test("only one timer runs and it stops when the card is detached", () => {
  const originalNow = Date.now;
  Date.now = () => now;
  try {
    const card = new DiagralOverviewCard();
    card.setConfig(config);
    card.connectedCallback();
    card.hass = hassFor();
    card.hass = hassFor();
    assert.equal(timers.size, 1);
    card.disconnectedCallback();
    assert.equal(timers.size, 0);
    card.connectedCallback();
    assert.equal(timers.size, 1);
    card.hass = hassFor("disarmed");
    assert.equal(timers.size, 1);
    assert.ok([...timerDelays.values()][0] > 1000);
    card.disconnectedCallback();
  } finally { Date.now = originalNow; }
});
test("the timer updates while HA provides no new states and stops at the deadline", () => {
  const originalNow = Date.now;
  let clock = now;
  Date.now = () => clock;
  try {
    const card = new DiagralOverviewCard();
    card.setConfig(config);
    card.connectedCallback();
    card.hass = hassFor();
    clock += 18_000;
    [...timers.values()][0]();
    assert.match(textTree(card.shadowRoot), /1 min 12 s/);
    clock += 90_000;
    [...timers.values()][0]();
    assert.match(textTree(card.shadowRoot), /confirmation/);
    assert.equal(timers.size, 1);
    assert.ok([...timerDelays.values()][0] > 1000);
    card.disconnectedCallback();
  } finally { Date.now = originalNow; }
});
test("freshness expires in the frontend without a new HA state update", () => {
  const originalNow = Date.now;
  let clock = now;
  Date.now = () => clock;
  try {
    const card = new DiagralOverviewCard();
    card.setConfig(config);
    card.connectedCallback();
    card.hass = hassFor("disarmed");
    assert.equal(timers.size, 1);
    assert.equal([...timerDelays.values()][0], 600_001);
    assert.match(textTree(card.shadowRoot), /Aucun défaut/);
    clock += 600_001;
    [...timers.values()][0]();
    assert.match(textTree(card.shadowRoot), /Données anciennes/);
    assert.doesNotMatch(textTree(card.shadowRoot), /Aucun défaut/);
    assert.equal(timers.size, 0);
    card.disconnectedCallback();
  } finally { Date.now = originalNow; }
});
test("communication messages retain complete error states", () => {
  const hass = hassFor("disarmed");
  hass.states["sensor.fake_communication"] = { state: "Diagral.API.AuthenticationError", attributes: { friendly_name: "Communication" } };
  const model = buildOverview({ ...config, communication_entities: ["sensor.fake_communication"] }, hass, now);
  assert.equal(model.communication[0].stateText, "Diagral.API.AuthenticationError");
});
test("inventory presence never claims the equipment is healthy", () => {
  const hass = hassFor("disarmed");
  hass.states["sensor.fake_inventory"] = { state: "configured", attributes: { friendly_name: "Camera" } };
  const model = buildOverview({ ...config, equipment_entities: ["sensor.fake_inventory"] }, hass, now);
  assert.equal(model.equipment[0].stateText, "Déclaré");
});
test("problem and connectivity sensors use their actual semantics", () => {
  const hass = hassFor("disarmed");
  hass.states["binary_sensor.fake_problem"] = { state: "on", attributes: { device_class: "problem" } };
  hass.states["binary_sensor.fake_cloud"] = { state: "off", attributes: { device_class: "connectivity" } };
  const model = buildOverview({ ...config, equipment_entities: ["binary_sensor.fake_problem"], communication_entities: ["binary_sensor.fake_cloud"] }, hass, now);
  assert.equal(model.equipment[0].stateText, "Défaut détecté");
  assert.equal(model.communication[0].stateText, "Déconnecté");
});
test("row clicks open native more-info without a backend command", () => {
  const card = new DiagralOverviewCard();
  card.setConfig({ ...config, group_entities: ["sensor.fake_group"] });
  const hass = hassFor("disarmed");
  hass.states["sensor.fake_group"] = { state: "disarmed", attributes: {} };
  card.hass = hass;
  function findButton(element) {
    return element.tagName === "button" ? element : element.children.map(findButton).find(Boolean);
  }
  const button = findButton(card.shadowRoot);
  button.events.find(([event]) => event === "click")[1]();
  assert.equal(card.dispatched.type, "hass-more-info");
  assert.equal(card.dispatched.detail.entityId, "sensor.fake_group");
});
test("configuration rejects URLs in entity identifiers", () => {
  const card = new DiagralOverviewCard();
  assert.throws(() => card.setConfig({ ...config, alarm_entity: "https://example.com" }), /alarm_entity/);
});
test("duration formatting is bounded and readable", () => {
  assert.equal(formatDuration(90), "1 min 30 s");
  assert.equal(formatDuration(0), "0 s");
  assert.equal(formatDuration(12), "12 s");
});

let passed = 0;
for (const [name, run] of tests) {
  try { await run(); passed++; console.log(`PASS ${name}`); }
  catch (error) { console.error(`FAIL ${name}\n${error.stack}`); process.exitCode = 1; }
}
console.log(`${passed}/${tests.length} offline card tests passed`);
