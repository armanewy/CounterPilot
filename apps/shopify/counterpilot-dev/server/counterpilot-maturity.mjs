import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { buildOfferSnapshots } from "./counterpilot-server.mjs";

export const DEFAULT_DATA_DIR = path.join(process.cwd(), ".counterpilot-data");
export const MARGIN_CONFIG_SCHEMA_VERSION = "counterpilot.margin_config.v1";
export const MATURE_EVENT_SOURCE = "counterpilot_maturity_job";

const MONEY_FIELDS = [
  "default_product_cost_minor",
  "default_shipping_cost_minor",
  "default_platform_fee_minor",
  "default_return_loss_minor",
];

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256Reference(value) {
  return `sha256:${crypto.createHash("sha256").update(value).digest("hex")}`;
}

function normalizeCurrency(value, fieldName = "currency") {
  if (typeof value !== "string" || !/^[A-Z]{3}$/.test(value.trim())) {
    throw new Error(`${fieldName} must be a three-letter uppercase currency`);
  }
  return value.trim();
}

function nonnegativeInteger(value, fieldName) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${fieldName} must be a non-negative integer`);
  }
  return value;
}

function normalizeMarginConfig(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("margin config must be a JSON object");
  }
  if (payload.schema_version !== MARGIN_CONFIG_SCHEMA_VERSION) {
    throw new Error(
      `margin config schema_version must be ${MARGIN_CONFIG_SCHEMA_VERSION}`,
    );
  }
  const config = {
    schema_version: MARGIN_CONFIG_SCHEMA_VERSION,
    maturity_window_days: nonnegativeInteger(
      payload.maturity_window_days,
      "maturity_window_days",
    ),
    currency: normalizeCurrency(payload.currency),
  };
  for (const field of MONEY_FIELDS) {
    config[field] = nonnegativeInteger(payload[field], field);
  }
  return config;
}

export async function loadMarginConfig(configPath) {
  let raw;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(
        `margin config is required at ${configPath}; create ${MARGIN_CONFIG_SCHEMA_VERSION} before running maturity`,
      );
    }
    throw error;
  }
  try {
    return normalizeMarginConfig(JSON.parse(raw));
  } catch (error) {
    throw new Error(`invalid margin config: ${error.message}`);
  }
}

export function resolveDataDir(options = {}) {
  return (
    options.dataDir ??
    process.env.COUNTERPILOT_SERVER_DATA_DIR ??
    process.env.COUNTERPILOT_DATA_DIR ??
    DEFAULT_DATA_DIR
  );
}

async function readJsonl(filePath) {
  try {
    const text = await fs.readFile(filePath, "utf8");
    return text
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

async function appendJsonl(filePath, record) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.appendFile(filePath, `${JSON.stringify(record)}\n`, "utf8");
}

function ageEligible(paidAt, now, maturityWindowDays) {
  const paidTime = new Date(paidAt).getTime();
  if (!Number.isFinite(paidTime)) {
    return false;
  }
  const elapsedMs = now.getTime() - paidTime;
  return elapsedMs >= maturityWindowDays * 24 * 60 * 60 * 1000;
}

function latestStatusByReference(refs, transactionId, hashField) {
  const latest = new Map();
  for (const ref of refs) {
    if (ref.transaction_id !== transactionId) {
      continue;
    }
    const referenceHash = ref[hashField];
    if (!referenceHash) {
      continue;
    }
    latest.set(referenceHash, ref.status);
  }
  return latest;
}

function unresolvedOperationalHolds({ refundRefs, returnRefs, transactionId }) {
  const reasons = [];
  for (const [referenceHash, status] of latestStatusByReference(
    refundRefs,
    transactionId,
    "refund_reference_hash",
  )) {
    if (status === "needs_reconciliation" || status === "held_before_paid") {
      reasons.push(`refund:${referenceHash}:${status}`);
    }
  }
  for (const [referenceHash, status] of latestStatusByReference(
    returnRefs,
    transactionId,
    "return_reference_hash",
  )) {
    if (status === "needs_reconciliation" || status === "held_before_paid") {
      reasons.push(`return:${referenceHash}:${status}`);
    }
  }
  return reasons;
}

function derivePaymentLifecycleState(snapshot) {
  if (snapshot.payment_lifecycle_state) {
    return snapshot.payment_lifecycle_state;
  }
  const refundTotal = snapshot.cumulative_refund_total_minor ?? 0;
  if (refundTotal > 0 && refundTotal >= snapshot.paid_total_minor) {
    return "refunded";
  }
  if (refundTotal > 0) {
    return "partially_refunded";
  }
  return "paid";
}

function maturityInput({ snapshot, config }) {
  return {
    transaction_id: snapshot.transaction_id,
    paid_total_minor: snapshot.paid_total_minor,
    cumulative_refund_total_minor: snapshot.cumulative_refund_total_minor ?? 0,
    return_exposure_state: snapshot.return_exposure_state ?? "none",
    product_cost_minor: config.default_product_cost_minor,
    shipping_cost_minor: config.default_shipping_cost_minor,
    platform_fee_minor: config.default_platform_fee_minor,
    return_loss_minor: config.default_return_loss_minor,
    currency: config.currency,
    maturity_window_days: config.maturity_window_days,
  };
}

function existingMatureHashes(events, transactionId) {
  return new Set(
    events
      .filter(
        (event) =>
          event.transaction_id === transactionId &&
          event.event_type === "mature" &&
          typeof event.maturity_input_hash === "string",
      )
      .map((event) => event.maturity_input_hash),
  );
}

function createMatureEvent({ snapshot, config, now, inputHash }) {
  const refundTotal = snapshot.cumulative_refund_total_minor ?? 0;
  const netRevenueMinor = snapshot.paid_total_minor - refundTotal;
  const matureMarginMinor =
    netRevenueMinor -
    config.default_product_cost_minor -
    config.default_shipping_cost_minor -
    config.default_platform_fee_minor -
    config.default_return_loss_minor;
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: snapshot.transaction_id,
    lifecycle_state: "mature",
    payment_lifecycle_state: derivePaymentLifecycleState(snapshot),
    event_type: "mature",
    actor_type: "system",
    source: MATURE_EVENT_SOURCE,
    occurred_at: now.toISOString(),
    matured_at: now.toISOString(),
    store_id: snapshot.store_id,
    store_reference_hash: sha256Reference(snapshot.store_id),
    maturity_window_days: config.maturity_window_days,
    paid_total_minor: snapshot.paid_total_minor,
    refund_total_minor: refundTotal,
    net_revenue_minor: netRevenueMinor,
    product_cost_minor: config.default_product_cost_minor,
    shipping_cost_minor: config.default_shipping_cost_minor,
    platform_fee_minor: config.default_platform_fee_minor,
    return_loss_minor: config.default_return_loss_minor,
    mature_margin_minor: matureMarginMinor,
    currency: config.currency,
    return_exposure_state: snapshot.return_exposure_state ?? "none",
    margin_config_source: MARGIN_CONFIG_SCHEMA_VERSION,
    maturity_input_hash: inputHash,
    production_evidence: Boolean(snapshot.production_evidence),
  };
}

function eligibilityForSnapshot({
  snapshot,
  config,
  now,
  events,
  refundRefs,
  returnRefs,
}) {
  if (!snapshot.paid_at || !Number.isSafeInteger(snapshot.paid_total_minor)) {
    return { eligible: false, reason: "not_paid" };
  }
  if (snapshot.return_exposure_state === "open") {
    return { eligible: false, reason: "return_exposure_open" };
  }
  if (!ageEligible(snapshot.paid_at, now, config.maturity_window_days)) {
    return { eligible: false, reason: "maturity_window_open" };
  }
  if (snapshot.paid_currency !== config.currency) {
    return { eligible: false, reason: "currency_mismatch" };
  }
  if (
    snapshot.refund_currency &&
    snapshot.cumulative_refund_total_minor > 0 &&
    snapshot.refund_currency !== config.currency
  ) {
    return { eligible: false, reason: "currency_mismatch" };
  }
  const holds = unresolvedOperationalHolds({
    refundRefs,
    returnRefs,
    transactionId: snapshot.transaction_id,
  });
  if (holds.length > 0) {
    return {
      eligible: false,
      reason: "reconciliation_hold",
      holds,
    };
  }
  const input = maturityInput({ snapshot, config });
  const inputHash = sha256Reference(canonicalJson(input));
  if (existingMatureHashes(events, snapshot.transaction_id).has(inputHash)) {
    return {
      eligible: false,
      reason: "duplicate_maturity_input",
      maturity_input_hash: inputHash,
    };
  }
  return { eligible: true, input, inputHash };
}

export function planMaturity({
  events,
  refundRefs = [],
  returnRefs = [],
  config,
  now = new Date(),
}) {
  const snapshots = buildOfferSnapshots(events);
  const planned = [];
  const skipped = [];
  for (const snapshot of snapshots.values()) {
    const eligibility = eligibilityForSnapshot({
      snapshot,
      config,
      now,
      events,
      refundRefs,
      returnRefs,
    });
    if (!eligibility.eligible) {
      skipped.push({
        transaction_id: snapshot.transaction_id,
        reason: eligibility.reason,
        maturity_input_hash: eligibility.maturity_input_hash,
      });
      continue;
    }
    planned.push(
      createMatureEvent({
        snapshot,
        config,
        now,
        inputHash: eligibility.inputHash,
      }),
    );
  }
  return { planned, skipped };
}

export async function runMaturityJob(options = {}) {
  const dataDir = resolveDataDir(options);
  const configPath =
    options.configPath ?? path.join(dataDir, "margin_config.json");
  const now = options.now instanceof Date ? options.now : new Date();
  const eventsPath = path.join(dataDir, "offers.jsonl");
  const events = await readJsonl(eventsPath);
  const refundRefs = await readJsonl(path.join(dataDir, "refund_refs.jsonl"));
  const returnRefs = await readJsonl(path.join(dataDir, "return_refs.jsonl"));
  const config = await loadMarginConfig(configPath);
  const { planned, skipped } = planMaturity({
    events,
    refundRefs,
    returnRefs,
    config,
    now,
  });
  for (const event of planned) {
    await appendJsonl(eventsPath, event);
  }
  return {
    schema_version: "counterpilot.maturity_job_result.v1",
    data_dir: dataDir,
    config_path: configPath,
    checked_transactions: buildOfferSnapshots(events).size,
    appended: planned.length,
    mature_events: planned.map((event) => ({
      transaction_id: event.transaction_id,
      maturity_input_hash: event.maturity_input_hash,
      mature_margin_minor: event.mature_margin_minor,
      currency: event.currency,
    })),
    skipped,
  };
}
