import fs from "node:fs/promises";
import path from "node:path";

import { loadMarginConfig, resolveDataDir } from "./counterpilot-maturity.mjs";
import { buildOfferSnapshots } from "./counterpilot-server.mjs";

export const REPORT_SCHEMA_VERSION = "counterpilot.merchant_report.v1";
export const DEFAULT_REPORT_FILENAME = "counterpilot_merchant_report.md";

const SENSITIVE_PATTERNS = [
  /gid:\/\/shopify\/[A-Za-z]+\/[A-Za-z0-9_-]+/g,
  /https?:\/\/\S+/g,
  /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,
  /(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)/g,
  /\b\d{1,6}\s+[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|place|pl)\b/gi,
  /\b(?:access|refresh|token|secret)[-_A-Za-z0-9]{8,}\b/gi,
];

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

function redactText(value) {
  let text = String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
  for (const pattern of SENSITIVE_PATTERNS) {
    text = text.replace(pattern, "[redacted]");
  }
  return text;
}

function markdownCell(value) {
  const text = redactText(value);
  return text.replaceAll("|", "\\|") || "n/a";
}

function shortHash(value) {
  if (typeof value !== "string" || !value) {
    return "none";
  }
  return value.replace(/^sha256:/, "").slice(0, 12);
}

function money(amountMinor, currency) {
  if (!Number.isSafeInteger(amountMinor)) {
    return "n/a";
  }
  const sign = amountMinor < 0 ? "-" : "";
  const absolute = Math.abs(amountMinor);
  return `${sign}$${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, "0")} ${currency}`;
}

function sum(rows, field) {
  return rows.reduce((total, row) => total + (row[field] ?? 0), 0);
}

function byStatus(refs) {
  const counts = {};
  for (const ref of refs) {
    const status =
      typeof ref.status === "string" ? redactText(ref.status) : "unknown";
    counts[status] = (counts[status] ?? 0) + 1;
  }
  return counts;
}

function currentMaturityStatus(snapshot) {
  if (snapshot.lifecycle_state === "mature") {
    return "current_mature";
  }
  if (snapshot.return_exposure_state === "open") {
    return "maturity_blocked_open_return";
  }
  if (snapshot.paid_at) {
    return "not_currently_mature";
  }
  return "not_paid";
}

function selectedAmountMinor(snapshot) {
  return (
    snapshot.accepted_amount_minor ??
    snapshot.counter_amount_minor ??
    snapshot.offer_amount_minor ??
    null
  );
}

function productLabel(snapshot) {
  const title = redactText(snapshot.product_title);
  const hash = shortHash(snapshot.product_reference_hash);
  return title ? `${title} (${hash})` : `Product ${hash}`;
}

function eventCounts(events, type) {
  return events.filter((event) => event.event_type === type).length;
}

function staleMatureEventCount(events, snapshots) {
  let stale = 0;
  for (const event of events) {
    if (event.event_type !== "mature") {
      continue;
    }
    const snapshot = snapshots.get(event.transaction_id);
    if (
      !snapshot ||
      snapshot.lifecycle_state !== "mature" ||
      snapshot.maturity_input_hash !== event.maturity_input_hash
    ) {
      stale += 1;
    }
  }
  return stale;
}

function productionEvidenceSummary(snapshots) {
  const known = snapshots.filter(
    (snapshot) => typeof snapshot.production_evidence === "boolean",
  );
  const trueCount = known.filter(
    (snapshot) => snapshot.production_evidence,
  ).length;
  const falseCount = known.filter(
    (snapshot) => !snapshot.production_evidence,
  ).length;
  return {
    any: trueCount > 0,
    all: known.length > 0 && trueCount === known.length,
    true_count: trueCount,
    false_count: falseCount,
    unknown_count: snapshots.length - known.length,
  };
}

function productBreakdown(snapshots, currency) {
  const groups = new Map();
  for (const snapshot of snapshots) {
    const label = productLabel(snapshot);
    const group = groups.get(label) ?? {
      product: label,
      offers: 0,
      paid: 0,
      current_mature: 0,
      mature_margin_minor: 0,
      refund_total_minor: 0,
    };
    group.offers += 1;
    if (snapshot.paid_at) {
      group.paid += 1;
      group.refund_total_minor += snapshot.cumulative_refund_total_minor ?? 0;
    }
    if (snapshot.lifecycle_state === "mature") {
      group.current_mature += 1;
      group.mature_margin_minor += snapshot.mature_margin_minor ?? 0;
    }
    groups.set(label, group);
  }
  return [...groups.values()]
    .sort(
      (left, right) =>
        right.offers - left.offers || left.product.localeCompare(right.product),
    )
    .map((row) => ({
      ...row,
      mature_margin: money(row.mature_margin_minor, currency),
      refunds: money(row.refund_total_minor, currency),
    }));
}

function transactionLedger(snapshots, currency) {
  return snapshots
    .slice()
    .sort(
      (left, right) =>
        String(left.submitted_at ?? "").localeCompare(
          String(right.submitted_at ?? ""),
        ) || left.transaction_id.localeCompare(right.transaction_id),
    )
    .map((snapshot) => {
      const status = currentMaturityStatus(snapshot);
      const selectedMinor = selectedAmountMinor(snapshot);
      return {
        transaction_id: snapshot.transaction_id,
        product: productLabel(snapshot),
        lifecycle_state: snapshot.lifecycle_state,
        maturity_status: status,
        offer: money(
          snapshot.offer_amount_minor,
          snapshot.currency ?? currency,
        ),
        selected_amount:
          selectedMinor === null
            ? "n/a"
            : money(selectedMinor, snapshot.currency ?? currency),
        paid_total: money(
          snapshot.paid_total_minor,
          snapshot.paid_currency ?? currency,
        ),
        refund_total: money(
          snapshot.cumulative_refund_total_minor ?? 0,
          snapshot.refund_currency ?? snapshot.paid_currency ?? currency,
        ),
        return_exposure_state: snapshot.return_exposure_state ?? "none",
        mature_margin:
          snapshot.lifecycle_state === "mature"
            ? money(
                snapshot.mature_margin_minor,
                snapshot.mature_currency ?? currency,
              )
            : "not current",
        production_evidence: String(Boolean(snapshot.production_evidence)),
      };
    });
}

function offerToAskingBreakdown(snapshots) {
  const rows = snapshots.filter(
    (snapshot) =>
      Number.isSafeInteger(snapshot.offer_amount_minor) &&
      Number.isSafeInteger(snapshot.asking_price_minor) &&
      snapshot.asking_price_minor > 0,
  );
  if (rows.length === 0) {
    return {
      available: false,
      note: "Offer-to-asking ratios are unavailable because the current storefront event schema does not persist asking_price_minor.",
      buckets: [],
    };
  }
  const buckets = [
    { label: "<70%", min: -Infinity, max: 0.7, count: 0 },
    { label: "70-85%", min: 0.7, max: 0.85, count: 0 },
    { label: "85-100%", min: 0.85, max: 1.0, count: 0 },
    { label: ">=100%", min: 1.0, max: Infinity, count: 0 },
  ];
  for (const snapshot of rows) {
    const ratio = snapshot.offer_amount_minor / snapshot.asking_price_minor;
    const bucket = buckets.find(
      (item) => ratio >= item.min && ratio < item.max,
    );
    if (bucket) {
      bucket.count += 1;
    }
  }
  return { available: true, note: null, buckets };
}

function reportModel({
  events,
  refundRefs,
  returnRefs,
  marginConfig,
  generatedAt,
}) {
  const snapshotMap = buildOfferSnapshots(events);
  const snapshots = [...snapshotMap.values()];
  const mature = snapshots.filter(
    (snapshot) => snapshot.lifecycle_state === "mature",
  );
  const paid = snapshots.filter((snapshot) => snapshot.paid_at);
  const refunded = snapshots.filter(
    (snapshot) => (snapshot.cumulative_refund_total_minor ?? 0) > 0,
  );
  const openReturn = snapshots.filter(
    (snapshot) => snapshot.return_exposure_state === "open",
  );
  const closedReturn = snapshots.filter(
    (snapshot) => snapshot.return_exposure_state === "closed",
  );
  const currency = marginConfig.currency;
  const refundStatuses = byStatus(refundRefs);
  const returnStatuses = byStatus(returnRefs);

  return {
    schema_version: REPORT_SCHEMA_VERSION,
    generated_at: generatedAt.toISOString(),
    currency,
    production_evidence: productionEvidenceSummary(snapshots),
    assumptions: {
      schema_version: marginConfig.schema_version,
      maturity_window_days: marginConfig.maturity_window_days,
      product_cost: money(marginConfig.default_product_cost_minor, currency),
      shipping_cost: money(marginConfig.default_shipping_cost_minor, currency),
      platform_fee: money(marginConfig.default_platform_fee_minor, currency),
      return_loss: money(marginConfig.default_return_loss_minor, currency),
      currency,
    },
    funnel: {
      offers_submitted: snapshots.length,
      merchant_accepted: eventCounts(events, "merchant_accepted"),
      merchant_countered: eventCounts(events, "merchant_countered"),
      merchant_declined: eventCounts(events, "merchant_declined"),
      buyer_accepted: eventCounts(events, "buyer_accepted"),
      checkout_created: eventCounts(events, "checkout_created"),
      order_created: eventCounts(events, "order_created"),
      paid: paid.length,
      current_mature: mature.length,
      open_return_blocked: openReturn.length,
    },
    mature_margin: {
      current_mature_transactions: mature.length,
      paid_total: money(sum(mature, "paid_total_minor"), currency),
      refund_total: money(sum(mature, "refund_total_minor"), currency),
      net_revenue: money(sum(mature, "net_revenue_minor"), currency),
      product_cost: money(sum(mature, "product_cost_minor"), currency),
      shipping_cost: money(
        sum(mature, "maturity_shipping_cost_minor"),
        currency,
      ),
      platform_fee: money(sum(mature, "platform_fee_minor"), currency),
      return_loss: money(sum(mature, "return_loss_minor"), currency),
      mature_margin: money(sum(mature, "mature_margin_minor"), currency),
    },
    refund_return_impact: {
      refunded_transactions: refunded.length,
      current_open_return_exposure: openReturn.length,
      current_closed_return_exposure: closedReturn.length,
      refund_total_across_paid: money(
        sum(paid, "cumulative_refund_total_minor"),
        currency,
      ),
      latest_refund_total_across_paid: money(
        sum(paid, "latest_refund_total_minor"),
        currency,
      ),
    },
    margin_leakage: {
      refunds: money(sum(mature, "refund_total_minor"), currency),
      product_cost: money(sum(mature, "product_cost_minor"), currency),
      shipping_cost: money(
        sum(mature, "maturity_shipping_cost_minor"),
        currency,
      ),
      platform_fee: money(sum(mature, "platform_fee_minor"), currency),
      return_loss: money(sum(mature, "return_loss_minor"), currency),
    },
    product_breakdown: productBreakdown(snapshots, currency),
    offer_to_asking: offerToAskingBreakdown(snapshots),
    transaction_ledger: transactionLedger(snapshots, currency),
    data_quality: {
      stale_mature_event_count: staleMatureEventCount(events, snapshotMap),
      refund_ref_count: refundRefs.length,
      return_ref_count: returnRefs.length,
      refund_ref_statuses: refundStatuses,
      return_ref_statuses: returnStatuses,
      reconciliation_hold_count:
        (refundStatuses.needs_reconciliation ?? 0) +
        (refundStatuses.held_before_paid ?? 0) +
        (returnStatuses.needs_reconciliation ?? 0) +
        (returnStatuses.held_before_paid ?? 0),
    },
    language_boundaries: {
      causal_claim: false,
      recommendation_model: false,
      manual_decisions: true,
    },
  };
}

function table(headers, rows) {
  const header = `| ${headers.map(markdownCell).join(" | ")} |`;
  const divider = `| ${headers.map(() => "---").join(" | ")} |`;
  const body = rows.map((row) => `| ${row.map(markdownCell).join(" | ")} |`);
  return [header, divider, ...body].join("\n");
}

function statusLineMap(map) {
  const entries = Object.entries(map);
  if (entries.length === 0) {
    return "none";
  }
  return entries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ");
}

export function renderMerchantReportMarkdown(report) {
  const lines = [
    "# Counterpilot Merchant Report",
    "",
    `Generated at: ${report.generated_at}`,
    `Production evidence: ${report.production_evidence.any ? "true" : "false"}`,
    "",
    "This report summarizes Counterpilot-mediated negotiated orders and their observed lifecycle outcomes. It does not estimate conversion lift, profit lift, recovered revenue, or what would have happened without Counterpilot.",
    "",
    "Counterpilot is not a recommendation model. Merchant accept, counter, and decline decisions were manual.",
    "",
    "## Summary",
    "",
    `- Offers submitted: ${report.funnel.offers_submitted}`,
    `- Paid negotiated orders: ${report.funnel.paid}`,
    `- Current mature transactions: ${report.mature_margin.current_mature_transactions}`,
    `- Current mature margin: ${report.mature_margin.mature_margin}`,
    `- Current open return exposure blocking maturity: ${report.funnel.open_return_blocked}`,
    "",
    "## Offer Funnel",
    "",
    table(
      ["Step", "Count"],
      [
        ["offers_submitted", report.funnel.offers_submitted],
        ["merchant_accepted", report.funnel.merchant_accepted],
        ["merchant_countered", report.funnel.merchant_countered],
        ["merchant_declined", report.funnel.merchant_declined],
        ["buyer_accepted", report.funnel.buyer_accepted],
        ["checkout_created", report.funnel.checkout_created],
        ["order_created", report.funnel.order_created],
        ["paid", report.funnel.paid],
        ["current_mature", report.funnel.current_mature],
      ],
    ),
    "",
    "## Mature Margin Summary",
    "",
    table(
      ["Metric", "Value"],
      [
        ["paid_total", report.mature_margin.paid_total],
        ["refund_total", report.mature_margin.refund_total],
        ["net_revenue", report.mature_margin.net_revenue],
        ["product_cost", report.mature_margin.product_cost],
        ["shipping_cost", report.mature_margin.shipping_cost],
        ["platform_fee", report.mature_margin.platform_fee],
        ["return_loss", report.mature_margin.return_loss],
        ["mature_margin", report.mature_margin.mature_margin],
      ],
    ),
    "",
    "## Refund And Return Impact",
    "",
    table(
      ["Metric", "Value"],
      [
        [
          "refunded_transactions",
          report.refund_return_impact.refunded_transactions,
        ],
        [
          "refund_total_across_paid",
          report.refund_return_impact.refund_total_across_paid,
        ],
        [
          "latest_refund_total_across_paid",
          report.refund_return_impact.latest_refund_total_across_paid,
        ],
        [
          "current_open_return_exposure",
          report.refund_return_impact.current_open_return_exposure,
        ],
        [
          "current_closed_return_exposure",
          report.refund_return_impact.current_closed_return_exposure,
        ],
      ],
    ),
    "",
    "## Margin Leakage",
    "",
    table(
      ["Leakage component", "Amount"],
      [
        ["refunds", report.margin_leakage.refunds],
        ["product_cost", report.margin_leakage.product_cost],
        ["shipping_cost", report.margin_leakage.shipping_cost],
        ["platform_fee", report.margin_leakage.platform_fee],
        ["return_loss", report.margin_leakage.return_loss],
      ],
    ),
    "",
    "## Product/SKU Breakdown",
    "",
    report.product_breakdown.length
      ? table(
          [
            "Product",
            "Offers",
            "Paid",
            "Current mature",
            "Refunds",
            "Mature margin",
          ],
          report.product_breakdown.map((row) => [
            row.product,
            row.offers,
            row.paid,
            row.current_mature,
            row.refunds,
            row.mature_margin,
          ]),
        )
      : "No product rows yet.",
    "",
    "## Offer-To-Asking Breakdown",
    "",
  ];

  if (report.offer_to_asking.available) {
    lines.push(
      table(
        ["Offer-to-asking bucket", "Count"],
        report.offer_to_asking.buckets.map((row) => [row.label, row.count]),
      ),
    );
  } else {
    lines.push(report.offer_to_asking.note);
  }

  lines.push(
    "",
    "## Safe Transaction Ledger",
    "",
    report.transaction_ledger.length
      ? table(
          [
            "Transaction",
            "Product",
            "Lifecycle",
            "Maturity status",
            "Offer",
            "Selected amount",
            "Paid total",
            "Refund total",
            "Return exposure",
            "Mature margin",
            "Production evidence",
          ],
          report.transaction_ledger.map((row) => [
            row.transaction_id,
            row.product,
            row.lifecycle_state,
            row.maturity_status,
            row.offer,
            row.selected_amount,
            row.paid_total,
            row.refund_total,
            row.return_exposure_state,
            row.mature_margin,
            row.production_evidence,
          ]),
        )
      : "No transactions yet.",
    "",
    "## Assumptions Used",
    "",
    table(
      ["Assumption", "Value"],
      [
        ["schema_version", report.assumptions.schema_version],
        ["maturity_window_days", report.assumptions.maturity_window_days],
        ["default_product_cost", report.assumptions.product_cost],
        ["default_shipping_cost", report.assumptions.shipping_cost],
        ["default_platform_fee", report.assumptions.platform_fee],
        ["default_return_loss", report.assumptions.return_loss],
        ["currency", report.assumptions.currency],
      ],
    ),
    "",
    "## Data-Quality / Reconciliation Notes",
    "",
    `- Stale mature events excluded from current margin view: ${report.data_quality.stale_mature_event_count}`,
    `- Operational refund refs counted: ${report.data_quality.refund_ref_count}`,
    `- Operational return refs counted: ${report.data_quality.return_ref_count}`,
    `- Refund ref statuses: ${statusLineMap(report.data_quality.refund_ref_statuses)}`,
    `- Return ref statuses: ${statusLineMap(report.data_quality.return_ref_statuses)}`,
    `- Reconciliation holds: ${report.data_quality.reconciliation_hold_count}`,
    "",
    "## Language Boundaries",
    "",
    "- Non-causal report: true",
    "- Recommendation model: false",
    "- Merchant decisions: manual",
    "",
  );

  return `${lines.join("\n")}\n`;
}

function scanForbiddenReportContent(text) {
  const failures = [];
  const checks = [
    ["raw Shopify GID", /gid:\/\/shopify\//],
    ["checkout or order URL", /https?:\/\/\S+/],
    ["email address", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i],
    [
      "phone number",
      /(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)/,
    ],
    ["raw token", /\b(?:access|refresh|token|secret)[-_A-Za-z0-9]{8,}\b/i],
  ];
  for (const [name, pattern] of checks) {
    if (pattern.test(text)) {
      failures.push(name);
    }
  }
  return failures;
}

export async function buildMerchantReport(options = {}) {
  const dataDir = resolveDataDir(options);
  const configPath =
    options.configPath ?? path.join(dataDir, "margin_config.json");
  const generatedAt = options.now instanceof Date ? options.now : new Date();
  const events = await readJsonl(path.join(dataDir, "offers.jsonl"));
  const refundRefs = await readJsonl(path.join(dataDir, "refund_refs.jsonl"));
  const returnRefs = await readJsonl(path.join(dataDir, "return_refs.jsonl"));
  const marginConfig = await loadMarginConfig(configPath);
  return reportModel({
    events,
    refundRefs,
    returnRefs,
    marginConfig,
    generatedAt,
  });
}

export async function runReportJob(options = {}) {
  const dataDir = resolveDataDir(options);
  const report = await buildMerchantReport({ ...options, dataDir });
  const outputPath =
    options.outputPath ??
    path.join(dataDir, "reports", DEFAULT_REPORT_FILENAME);
  const markdown = renderMerchantReportMarkdown(report);
  const privacyFailures = scanForbiddenReportContent(markdown);
  if (privacyFailures.length > 0) {
    throw new Error(
      `report privacy scan failed: ${privacyFailures.join(", ")}`,
    );
  }
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, markdown, "utf8");
  return {
    schema_version: "counterpilot.report_job_result.v1",
    output_path: outputPath,
    checked_transactions: report.funnel.offers_submitted,
    current_mature_transactions:
      report.mature_margin.current_mature_transactions,
    production_evidence: report.production_evidence.any,
    privacy_scan_passed: true,
  };
}
