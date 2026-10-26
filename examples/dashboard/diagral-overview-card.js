/** Local read-only Diagral overview. No dependencies, network calls or commands. */
const UNKNOWN_STATES = new Set(["unknown", "unavailable", "none", ""]);
const ENTITY_ID = /^[a-z0-9_]+\.[a-z0-9_]+$/;
const DEFAULT_STALE_AFTER_SECONDS = 600;
const MAX_FUTURE_SKEW_MS = 60_000;
const ALARM_LABELS = {
  disarmed: "Désarmée", arming: "Armement en cours", armed_away: "Armée — absence",
  armed_home: "Armée — présence", armed_night: "Armée — nuit",
  armed_custom_bypass: "Armée — partiel", triggered: "Alarme déclenchée",
  pending: "Temporisation d’entrée", unavailable: "Centrale indisponible", unknown: "État inconnu",
};
const STATE_LABELS = {
  on: "Oui", off: "Non", unknown: "Inconnu", unavailable: "Indisponible",
  armed: "Armé", disarmed: "Désarmé", arming: "Armement en cours",
  ok: "Aucun défaut", healthy: "Aucun défaut", fault: "Défaut", problem: "Défaut",
  inhibited: "Neutralisé", connected: "Connecté", disconnected: "Déconnecté",
  registered: "Enregistré", authentication_failed: "Authentification refusée",
  clock_skew: "Horloge système décalée", cloud_unavailable: "Service Diagral inaccessible",
  network_error: "Erreur réseau", timeout: "Délai de réponse dépassé",
  rate_limited: "Trop de requêtes", waiting: "En attente", configured: "Déclaré",
};

function validEntity(value) {
  return typeof value === "string" && ENTITY_ID.test(value);
}

function knownState(entity) {
  return entity && typeof entity.state === "string" && !UNKNOWN_STATES.has(entity.state);
}

function integer(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function timestamp(value) {
  if (typeof value !== "string" || !/T.*(?:Z|[+-]\d\d:\d\d)$/i.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatDuration(seconds) {
  const value = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(value / 60);
  return minutes ? `${minutes} min ${value % 60} s` : `${value} s`;
}

function armingModel(config, states, alarm, now) {
  const attributes = alarm?.attributes || {};
  const duration = integer(attributes.arming_duration);
  if (alarm?.state !== "arming") return { kind: "inactive", remaining: null, text: "", duration };
  const timingKnown = attributes.arming_timing_known === true;
  const deadline = config.arming_end_entity
    ? timestamp(states[config.arming_end_entity]?.state)
    : timestamp(attributes.arming_ends_at);
  if (!timingKnown || deadline === null) {
    return {
      kind: "unknown", remaining: null, duration,
      text: "Temps restant inconnu : le début de la temporisation n’a pas été observé.",
      detail: duration === null ? "" : `Durée de sortie configurée : ${formatDuration(duration)}.`,
    };
  }
  const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
  if (remaining === 0) {
    return {
      kind: "awaiting_confirmation", remaining, duration,
      text: "Délai estimé écoulé — en attente de confirmation de la centrale.",
      detail: "Le panneau reste en armement tant que Diagral ne confirme pas l’état final.",
    };
  }
  return {
    kind: "countdown", remaining, duration, deadline,
    text: `Temps de sortie estimé : environ ${formatDuration(remaining)}.`,
    detail: "La centrale confirme l’état final. Aucun appel cloud supplémentaire chaque seconde.",
    progress: duration ? Math.max(0, Math.min(1, remaining / duration)) : null,
  };
}

function faultGroups(attributes) {
  if (!Array.isArray(attributes.faults)) return [];
  return attributes.faults.flatMap((item) => {
    if (!item || typeof item !== "object" || !Array.isArray(item.faults)) return [];
    const faults = item.faults.flatMap((fault) => {
      if (!fault || typeof fault !== "object") return [];
      if (typeof fault.name === "string" && fault.name.trim()) return [fault.name];
      return typeof fault.code === "string" || typeof fault.code === "number" ? [`Défaut ${fault.code}`] : [];
    });
    if (!faults.length) return [];
    return [{
      name: typeof item.device_name === "string" && item.device_name.trim() ? item.device_name : "Équipement non identifié",
      group: typeof item.group_name === "string" ? item.group_name : null,
      faults,
    }];
  });
}

function anomaliesModel(entity, now, staleAfterSeconds) {
  const attributes = entity?.attributes || {};
  const available = Boolean(knownState(entity));
  const groups = faultGroups(attributes);
  const count = integer(attributes.fault_count) ?? (groups.length ? groups.reduce((sum, group) => sum + group.faults.length, 0) : entity?.state === "0" ? 0 : null);
  const affected = integer(attributes.affected_device_count) ?? (groups.length ? groups.length : count === 0 ? 0 : null);
  const updatedAt = timestamp(attributes.last_success) ?? timestamp(attributes.updated_at);
  const timestampKnown = updatedAt !== null && updatedAt <= now + MAX_FUTURE_SKEW_MS;
  const freshness = attributes.data_fresh === false || !available
    ? "stale" : !timestampKnown ? "unknown" : now - updatedAt > staleAfterSeconds * 1000 ? "stale" : "fresh";
  const expiresAt = freshness === "fresh" ? updatedAt + staleAfterSeconds * 1000 + 1 : null;
  const detail = count === null ? "détail des défauts à confirmer" : `${count} défaut${count === 1 ? "" : "s"}${affected === null ? "" : ` sur ${affected} équipement${affected === 1 ? "" : "s"}`}`;
  let text;
  if (freshness === "stale") text = `Données anciennes — dernier relevé : ${detail}. L’état actuel n’est pas confirmé.`;
  else if (freshness === "unknown") text = `Fraîcheur inconnue — dernier relevé : ${detail}. L’état actuel n’est pas confirmé.`;
  else if (count === 0) text = "Aucun défaut signalé lors de la dernière lecture.";
  else if (count !== null) text = `${detail}.`;
  else text = "Anomalie signalée — le détail n’est pas disponible.";
  return { available, count, affected, groups, text, updatedAt, freshness, expiresAt, tone: freshness !== "fresh" || count === null ? "unknown" : count === 0 ? "clear" : "fault" };
}

function references(config, key, states, stale = false) {
  return (config[key] || []).map((reference) => {
    const entityId = typeof reference === "string" ? reference : reference.entity;
    const entity = states[entityId];
    const state = entity?.state ?? "unavailable";
    const name = (typeof reference === "object" && reference.name) || entity?.attributes?.friendly_name || entityId;
    let stateText = STATE_LABELS[state] || state;
    if (entity?.attributes?.device_class === "problem" && (state === "on" || state === "off")) stateText = state === "on" ? "Défaut détecté" : "Aucun défaut";
    if (entity?.attributes?.device_class === "connectivity" && (state === "on" || state === "off")) stateText = state === "on" ? "Connecté" : "Déconnecté";
    if (stale && knownState(entity)) stateText = `Dernier relevé : ${stateText === "Aucun défaut" ? "0 défaut" : stateText}`;
    return { entityId, name, state, stateText, available: knownState(entity) };
  });
}

export function buildOverview(config, hass, now = Date.now()) {
  const states = hass?.states || {};
  const alarm = states[config.alarm_entity];
  const state = alarm?.state ?? "unavailable";
  const staleAfterSeconds = typeof config.stale_after_seconds === "number" && Number.isFinite(config.stale_after_seconds) && config.stale_after_seconds > 0 ? config.stale_after_seconds : DEFAULT_STALE_AFTER_SECONDS;
  const anomalies = anomaliesModel(states[config.anomalies_entity], now, staleAfterSeconds);
  return {
    title: config.title || "Diagral",
    alarm: { state, label: ALARM_LABELS[state] || "État de la centrale inconnu", available: knownState(alarm) },
    arming: armingModel(config, states, alarm, now),
    anomalies,
    groups: references(config, "group_entities", states, anomalies.freshness !== "fresh"),
    equipment: references(config, "equipment_entities", states, anomalies.freshness !== "fresh"),
    communication: references(config, "communication_entities", states, anomalies.freshness !== "fresh"),
  };
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

const CSS = `
  :host { display: block; }
  ha-card { overflow: hidden; color: var(--primary-text-color); }
  .body { padding: 22px; }
  h2 { margin: 0 0 18px; font-size: 1.3rem; font-weight: 600; }
  h3 { margin: 22px 0 12px; font-size: 1rem; }
  .status { display: flex; align-items: center; gap: 10px; font-size: 1.2rem; font-weight: 600; }
  .status ha-icon { color: var(--primary-color); }
  .status[data-state="arming"] ha-icon { color: var(--warning-color, #c78600); }
  .status[data-state="triggered"] ha-icon { color: var(--error-color, #db4437); }
  .status[data-state="unavailable"] ha-icon, .status[data-state="unknown"] ha-icon { color: var(--disabled-text-color); }
  .countdown { margin-top: 12px; font-variant-numeric: tabular-nums; }
  .countdown strong { display: block; line-height: 1.5; }
  .detail, .timestamp, .empty { color: var(--secondary-text-color); font-size: .9rem; line-height: 1.5; }
  progress { width: 100%; height: 9px; margin: 10px 0 4px; accent-color: var(--primary-color); }
  .summary { padding: 12px 14px; border-radius: 10px; background: var(--secondary-background-color); line-height: 1.5; }
  .summary[data-tone="fault"] { border-left: 4px solid var(--error-color, #db4437); }
  .summary[data-tone="unknown"] { border-left: 4px solid var(--warning-color, #c78600); }
  .fault-list { margin: 12px 0 0; padding: 0; list-style: none; }
  .fault-device { margin-bottom: 12px; border-bottom: 1px solid var(--divider-color); padding-bottom: 10px; }
  .fault-device strong { display: block; overflow-wrap: anywhere; }
  .fault-device ul { margin: 6px 0 0; padding-left: 22px; }
  .fault-device li { line-height: 1.6; overflow-wrap: anywhere; }
  .entities { display: grid; gap: 0; }
  .entity { width: 100%; border: 0; border-bottom: 1px solid var(--divider-color); background: transparent; color: inherit; text-align: left; padding: 12px 0; display: flex; justify-content: space-between; gap: 15px; font: inherit; cursor: pointer; }
  .entity:hover { background: var(--secondary-background-color); }
  .entity:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .entity-name { overflow-wrap: anywhere; }
  .entity-state { color: var(--secondary-text-color); text-align: right; overflow-wrap: anywhere; }
  @media (max-width: 450px) { .body { padding: 16px; } .entity { flex-direction: column; gap: 4px; } .entity-state { text-align: left; } }
`;

export class DiagralOverviewCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._attached = false;
    this._timer = null;
    this._config = null;
    this._hass = null;
  }

  setConfig(config) {
    for (const key of ["alarm_entity", "anomalies_entity"]) {
      if (!validEntity(config?.[key])) throw new Error(`Diagral : ${key} doit être un entity_id valide.`);
    }
    if (config.arming_end_entity && !validEntity(config.arming_end_entity)) throw new Error("Diagral : arming_end_entity doit être un entity_id valide.");
    if (config.stale_after_seconds !== undefined && (typeof config.stale_after_seconds !== "number" || !Number.isFinite(config.stale_after_seconds) || config.stale_after_seconds <= 0)) throw new Error("Diagral : stale_after_seconds doit être un nombre positif.");
    for (const key of ["group_entities", "equipment_entities", "communication_entities"]) {
      if (config[key] !== undefined && !Array.isArray(config[key])) throw new Error(`Diagral : ${key} doit être une liste.`);
      if ((config[key] || []).some((item) => !validEntity(typeof item === "string" ? item : item?.entity))) throw new Error(`Diagral : ${key} contient un entity_id invalide.`);
    }
    this._config = { ...config };
    this._render();
  }

  set hass(value) {
    this._hass = value;
    this._render();
  }

  connectedCallback() {
    this._attached = true;
    this._render();
  }

  disconnectedCallback() {
    this._attached = false;
    this._clearTimer();
  }

  getCardSize() { return 8; }

  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  _clearTimer() {
    if (this._timer !== null) clearTimeout(this._timer);
    this._timer = null;
  }

  _scheduleNextTick(model) {
    this._clearTimer();
    if (!this._attached) return;
    const freshnessDelay = model.anomalies.expiresAt === null ? Infinity : Math.max(1, model.anomalies.expiresAt - Date.now());
    const delay = Math.min(model.arming.kind === "countdown" ? 1000 : Infinity, freshnessDelay);
    if (!Number.isFinite(delay)) return;
    this._timer = setTimeout(() => {
      this._timer = null;
      const current = buildOverview(this._config, this._hass);
      if (current.anomalies.freshness !== this._displayedFreshness) this._render();
      else this._renderCountdown();
    }, Math.min(delay, 2_147_483_647));
  }

  _renderCountdown() {
    if (!this._config || !this._countdown) return;
    const model = buildOverview(this._config, this._hass);
    const { arming } = model;
    this._countdownText.textContent = arming.text;
    this._countdownDetail.textContent = arming.detail || "";
    this._countdown.hidden = arming.kind === "inactive";
    this._progress.hidden = arming.kind !== "countdown" || arming.progress === null;
    if (!this._progress.hidden) this._progress.value = arming.progress;
    this._scheduleNextTick(model);
  }

  _entitySection(parent, title, entities) {
    if (!entities.length) return;
    parent.append(node("h3", "", title));
    const rows = node("div", "entities");
    for (const entity of entities) {
      const button = node("button", "entity");
      button.type = "button";
      button.setAttribute("aria-label", `${entity.name} : ${entity.stateText}. Ouvrir les détails.`);
      button.append(node("span", "entity-name", entity.name), node("span", "entity-state", entity.stateText));
      button.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entity.entityId }, bubbles: true, composed: true })));
      rows.append(button);
    }
    parent.append(rows);
  }

  _render() {
    if (!this._config) return;
    const model = buildOverview(this._config, this._hass);
    this._displayedFreshness = model.anomalies.freshness;
    const style = node("style", "", CSS);
    const card = node("ha-card");
    const body = node("div", "body");
    body.append(node("h2", "", model.title));
    const status = node("div", "status");
    status.setAttribute("data-state", model.alarm.state);
    const icon = node("ha-icon");
    icon.setAttribute("icon", model.alarm.state === "triggered" ? "mdi:alarm-light" : model.alarm.state === "arming" ? "mdi:timer-sand" : "mdi:shield-home-outline");
    status.append(icon, node("span", "", model.alarm.label));
    body.append(status);
    this._countdown = node("div", "countdown");
    this._countdownText = node("strong");
    this._countdownDetail = node("div", "detail");
    this._progress = node("progress");
    this._progress.max = 1;
    this._progress.setAttribute("aria-label", "Temps de sortie restant estimé");
    this._countdown.append(this._countdownText, this._progress, this._countdownDetail);
    body.append(this._countdown);
    body.append(node("h3", "", "Anomalies"));
    const summary = node("div", "summary", model.anomalies.text);
    summary.setAttribute("data-tone", model.anomalies.tone);
    body.append(summary);
    if (model.anomalies.updatedAt !== null && model.anomalies.updatedAt !== undefined) {
      body.append(node("div", "timestamp", `Dernier relevé : ${new Date(model.anomalies.updatedAt).toLocaleString("fr-FR")}`));
    }
    if (model.anomalies.groups.length) {
      const groups = node("ul", "fault-list");
      for (const group of model.anomalies.groups) {
        const device = node("li", "fault-device");
        device.append(node("strong", "", group.name));
        if (group.group) device.append(node("div", "detail", group.group));
        const faults = node("ul");
        for (const fault of group.faults) faults.append(node("li", "", fault));
        device.append(faults);
        groups.append(device);
      }
      body.append(groups);
    } else if (model.anomalies.count !== null && model.anomalies.count > 0) body.append(node("div", "empty", "Le détail des équipements n’a pas été fourni."));
    this._entitySection(body, "Groupes", model.groups);
    this._entitySection(body, "Équipements", model.equipment);
    this._entitySection(body, "Communication", model.communication);
    card.append(body);
    this.shadowRoot.replaceChildren(style, card);
    this._renderCountdown();
  }
}

if (!customElements.get("diagral-overview-card")) customElements.define("diagral-overview-card", DiagralOverviewCard);
