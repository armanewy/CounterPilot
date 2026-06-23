import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { buildMerchantReport, runReportJob } from "./counterpilot-report.mjs";

const NOW = new Date("2026-06-23T20:00:00Z");

async function withDataDir(fn) {
  const dataDir = await fs.mkdtemp(
    path.join(os.tmpdir(), "counterpilot-report-"),
  );
  try {
    await writeMarginConfig(dataDir);
    await fn(dataDir);
  } finally {
    await fs.rm(dataDir, { force: true, recursive: true });
  }
}

async function writeMarginConfig(dataDir, overrides = {}) {
  const config = {
    schema_version: "counterpilot.margin_config.v1",
    maturity_window_days: 0,
    default_product_cost_minor: 42000,
    default_shipping_cost_minor: 3500,
    default_platform_fee_minor: 0,
    default_return_loss_minor: 0,
    currency: "USD",
    ...overrides,
  };
  await fs.writeFile(
    path.join(dataDir, "margin_config.json"),
    `${JSON.stringify(config, null, 2)}\n`,
    "utf8",
  );
  return config;
}

async function writeJsonl(dataDir, name, rows) {
  const filePath = path.join(dataDir, name);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(
    filePath,
    rows.map((row) => JSON.stringify(row)).join("\n") + "\n",
    "utf8",
  );
}

async function writeOfferEvents(dataDir, rows) {
  await writeJsonl(dataDir, "offers.jsonl", rows);
}

async function runReport(dataDir) {
  const result = await runReportJob({ dataDir, now: NOW });
  const markdown = await fs.readFile(result.output_path, "utf8");
  return {
    result,
    markdown,
    report: await buildMerchantReport({ dataDir, now: NOW }),
  };
}

function baseFlow(transactionId = "cp_offer_report_001", overrides = {}) {
  const productTitle = overrides.productTitle ?? "The Complete Snowboard";
  const offerAmountMinor = overrides.offerAmountMinor ?? 61000;
  const acceptedAmountMinor = overrides.acceptedAmountMinor ?? 61000;
  const paidTotalMinor = overrides.paidTotalMinor ?? 61000;
  return [
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "offer_submitted",
      event_type: "offer_submitted",
      actor_type: "buyer",
      occurred_at: "2026-06-23T18:00:00Z",
      received_at: "2026-06-23T18:00:00Z",
      source: "counterpilot_theme_block",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      product_title: productTitle,
      product_reference_hash: "sha256:productreport001",
      variant_reference_hash: "sha256:variantreport001",
      offer_amount_minor: offerAmountMinor,
      currency: "USD",
      quantity: 1,
      buyer_contact_reference: "email_hash:abcdef1234567890",
    },
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "merchant_accepted",
      event_type: "merchant_accepted",
      actor_type: "merchant",
      occurred_at: "2026-06-23T18:01:00Z",
      source: "counterpilot_merchant_inbox",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      buyer_response_token_hash: "sha256:tokenhash",
      buyer_response_expires_at: "2026-06-30T18:01:00Z",
    },
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "buyer_accepted",
      event_type: "buyer_accepted",
      actor_type: "buyer",
      occurred_at: "2026-06-23T18:02:00Z",
      source: "counterpilot_buyer_response",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      accepted_amount_minor: acceptedAmountMinor,
      currency: "USD",
      accepted_from_event_type: "merchant_accepted",
    },
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "checkout_created",
      event_type: "checkout_created",
      actor_type: "system",
      occurred_at: "2026-06-23T18:03:00Z",
      source: "counterpilot_checkout",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      accepted_amount_minor: acceptedAmountMinor,
      negotiated_revenue_minor: acceptedAmountMinor,
      currency: "USD",
      draft_order_reference_hash: "sha256:draft",
      checkout_reference_hash: "sha256:checkout",
    },
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "order_created",
      event_type: "order_created",
      actor_type: "shopify",
      occurred_at: "2026-06-23T18:04:00Z",
      source: "shopify_orders_paid_webhook",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      order_reference_hash: "sha256:order",
      order_name_reference_hash: "sha256:ordername",
      order_total_minor: paidTotalMinor,
      shipping_total_minor: 0,
      tax_total_minor: 0,
      discount_total_minor: 0,
      currency: "USD",
      production_evidence: false,
    },
    {
      schema_version: "counterpilot.offer_event.v1",
      transaction_id: transactionId,
      lifecycle_state: "paid",
      event_type: "paid",
      actor_type: "shopify",
      occurred_at: "2026-06-23T18:05:00Z",
      paid_at: "2026-06-23T18:05:00Z",
      source: "shopify_orders_paid_webhook",
      store_id: "counterpilot-dev.myshopify.com",
      store_reference_hash: "sha256:store",
      order_reference_hash: "sha256:order",
      paid_total_minor: paidTotalMinor,
      currency: "USD",
      production_evidence: false,
    },
  ];
}

function refundEvent({
  transactionId = "cp_offer_report_001",
  amountMinor,
  cumulativeMinor = amountMinor,
  occurredAt = "2026-06-23T18:06:00Z",
}) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: transactionId,
    lifecycle_state:
      cumulativeMinor >= 61000 ? "refunded" : "partially_refunded",
    event_type: "refund_recorded",
    actor_type: "shopify",
    occurred_at: occurredAt,
    processed_at: occurredAt,
    source: "shopify_refunds_create_webhook",
    store_id: "counterpilot-dev.myshopify.com",
    store_reference_hash: "sha256:store",
    order_reference_hash: "sha256:order",
    refund_reference_hash: `sha256:refund${cumulativeMinor}`,
    refund_total_minor: amountMinor,
    cumulative_refund_total_minor: cumulativeMinor,
    refund_amount_source: "transactions",
    currency: "USD",
    production_evidence: false,
  };
}

function returnEvent({
  transactionId = "cp_offer_report_001",
  exposure,
  status,
  lifecycleState = "paid",
  occurredAt = "2026-06-23T18:07:00Z",
}) {
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: transactionId,
    lifecycle_state: lifecycleState,
    event_type: "return_status_recorded",
    actor_type: "shopify",
    occurred_at: occurredAt,
    source: "shopify_returns_webhook",
    store_id: "counterpilot-dev.myshopify.com",
    store_reference_hash: "sha256:store",
    order_reference_hash: "sha256:order",
    return_reference_hash: `sha256:return${status}`,
    return_status: status,
    return_exposure_state: exposure,
    total_return_line_items: 1,
    production_evidence: false,
  };
}

function matureEvent({
  transactionId = "cp_offer_report_001",
  refundTotalMinor = 0,
  marginMinor = 15500,
  returnExposure = "none",
  inputHash = "sha256:maturityhash001",
  occurredAt = "2026-06-23T19:00:00Z",
}) {
  const paidTotalMinor = 61000;
  return {
    schema_version: "counterpilot.offer_event.v1",
    transaction_id: transactionId,
    lifecycle_state: "mature",
    payment_lifecycle_state:
      refundTotalMinor >= paidTotalMinor
        ? "refunded"
        : refundTotalMinor > 0
          ? "partially_refunded"
          : "paid",
    event_type: "mature",
    actor_type: "system",
    source: "counterpilot_maturity_job",
    occurred_at: occurredAt,
    matured_at: occurredAt,
    store_id: "counterpilot-dev.myshopify.com",
    store_reference_hash: "sha256:store",
    maturity_window_days: 0,
    paid_total_minor: paidTotalMinor,
    refund_total_minor: refundTotalMinor,
    net_revenue_minor: paidTotalMinor - refundTotalMinor,
    product_cost_minor: 42000,
    shipping_cost_minor: 3500,
    platform_fee_minor: 0,
    return_loss_minor: 0,
    mature_margin_minor: marginMinor,
    currency: "USD",
    return_exposure_state: returnExposure,
    margin_config_source: "counterpilot.margin_config.v1",
    maturity_input_hash: inputHash,
    production_evidence: false,
  };
}

function assertRequiredSections(markdown) {
  for (const section of [
    "## Summary",
    "## Offer Funnel",
    "## Mature Margin Summary",
    "## Refund And Return Impact",
    "## Margin Leakage",
    "## Product/SKU Breakdown",
    "## Offer-To-Asking Breakdown",
    "## Safe Transaction Ledger",
    "## Assumptions Used",
    "## Data-Quality / Reconciliation Notes",
    "## Language Boundaries",
  ]) {
    assert.match(markdown, new RegExp(section.replace("/", "\\/")));
  }
}

function assertNoRawReportLeak(markdown) {
  assert.doesNotMatch(markdown, /buyer@example\.com/);
  assert.doesNotMatch(markdown, /gid:\/\/shopify\//);
  assert.doesNotMatch(markdown, /checkout\.counterpilot\.test/);
  assert.doesNotMatch(markdown, /raw-status-token/);
  assert.doesNotMatch(markdown, /123 Union/);
  assert.doesNotMatch(markdown, /555-0100/);
  assert.doesNotMatch(markdown, /987654321/);
  assert.doesNotMatch(markdown, /246813579/);
  assert.doesNotMatch(markdown, /secret-return-tracking/);
  assert.doesNotMatch(markdown, /refund note/i);
  assert.doesNotMatch(markdown, /Decline note/i);
}

test("happy path paid to mature report includes required merchant sections", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [...baseFlow(), matureEvent({})]);

    const { result, markdown, report } = await runReport(dataDir);

    assert.equal(result.checked_transactions, 1);
    assert.equal(result.current_mature_transactions, 1);
    assert.equal(result.production_evidence, false);
    assert.equal(result.privacy_scan_passed, true);
    assert.equal(report.mature_margin.mature_margin, "$155.00 USD");
    assertRequiredSections(markdown);
    assert.match(markdown, /Current mature margin: \$155\.00 USD/);
    assert.match(markdown, /Production evidence: false/);
    assert.match(markdown, /does not estimate conversion lift, profit lift/);
    assert.match(markdown, /Counterpilot is not a recommendation model/);
    assertNoRawReportLeak(markdown);
  });
});

test("partial refund reduces net revenue and mature margin in report", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      refundEvent({ amountMinor: 1500 }),
      matureEvent({
        refundTotalMinor: 1500,
        marginMinor: 14000,
        inputHash: "sha256:maturityhashpartial",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.refund_total, "$15.00 USD");
    assert.equal(report.mature_margin.net_revenue, "$595.00 USD");
    assert.equal(report.mature_margin.mature_margin, "$140.00 USD");
    assert.match(markdown, /\$140\.00 USD/);
    assertNoRawReportLeak(markdown);
  });
});

test("full refund appears clearly with zero net revenue", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      refundEvent({ amountMinor: 61000 }),
      matureEvent({
        refundTotalMinor: 61000,
        marginMinor: -45500,
        inputHash: "sha256:maturityhashfullrefund",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.net_revenue, "$0.00 USD");
    assert.equal(report.mature_margin.mature_margin, "-$455.00 USD");
    assert.match(markdown, /\| refund_total \| \$610\.00 USD \|/);
  });
});

test("open return after mature blocks current maturity display", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      matureEvent({}),
      returnEvent({
        exposure: "open",
        status: "reopened",
        lifecycleState: "mature",
        occurredAt: "2026-06-23T19:10:00Z",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.current_mature_transactions, 0);
    assert.equal(report.data_quality.stale_mature_event_count, 1);
    assert.match(markdown, /maturity_blocked_open_return/);
    assert.doesNotMatch(markdown, /Current mature margin: \$155\.00 USD/);
  });
});

test("closed return allows mature display", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      returnEvent({ exposure: "closed", status: "closed" }),
      matureEvent({
        returnExposure: "closed",
        inputHash: "sha256:maturityhashclosedreturn",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.current_mature_transactions, 1);
    assert.equal(report.refund_return_impact.current_closed_return_exposure, 1);
    assert.match(
      markdown,
      /\| cp_offer_report_001 .* current_mature .* closed .* \$155\.00 USD \|/,
    );
  });
});

test("late refund after mature uses corrected maturity when present", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      matureEvent({ inputHash: "sha256:maturityhashold" }),
      refundEvent({
        amountMinor: 1500,
        occurredAt: "2026-06-23T19:10:00Z",
      }),
      matureEvent({
        refundTotalMinor: 1500,
        marginMinor: 14000,
        inputHash: "sha256:maturityhashcorrected",
        occurredAt: "2026-06-23T19:20:00Z",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.mature_margin, "$140.00 USD");
    assert.equal(report.data_quality.stale_mature_event_count, 1);
    assert.match(markdown, /Current mature margin: \$140\.00 USD/);
    assert.doesNotMatch(markdown, /Current mature margin: \$155\.00 USD/);
  });
});

test("late refund after mature without corrected maturity hides stale margin", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      matureEvent({ inputHash: "sha256:maturityhashstale" }),
      refundEvent({
        amountMinor: 1500,
        occurredAt: "2026-06-23T19:10:00Z",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.mature_margin.current_mature_transactions, 0);
    assert.equal(report.mature_margin.mature_margin, "$0.00 USD");
    assert.match(markdown, /not_currently_mature/);
    assert.doesNotMatch(markdown, /mature_margin \| \$155\.00 USD/);
  });
});

test("report includes margin config assumptions and production evidence false", async () => {
  await withDataDir(async (dataDir) => {
    await writeMarginConfig(dataDir, {
      default_product_cost_minor: 40000,
      default_shipping_cost_minor: 2500,
      default_platform_fee_minor: 500,
      default_return_loss_minor: 100,
    });
    await writeOfferEvents(dataDir, [
      ...baseFlow(),
      matureEvent({
        marginMinor: 17900,
        inputHash: "sha256:maturityhashcustomconfig",
      }),
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.assumptions.product_cost, "$400.00 USD");
    assert.equal(report.assumptions.shipping_cost, "$25.00 USD");
    assert.equal(report.assumptions.platform_fee, "$5.00 USD");
    assert.equal(report.assumptions.return_loss, "$1.00 USD");
    assert.equal(report.production_evidence.any, false);
    assert.match(markdown, /default_product_cost \| \$400\.00 USD/);
  });
});

test("report uses only safe labels and operational refs only for counts", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [
      ...baseFlow("cp_offer_report_001", {
        productTitle:
          "Snowboard buyer@example.com gid://shopify/Product/123 https://checkout.counterpilot.test/raw",
      }),
      matureEvent({}),
    ]);
    await writeJsonl(dataDir, "refund_refs.jsonl", [
      {
        schema_version: "counterpilot.refund_ref.v1",
        transaction_id: "cp_offer_report_001",
        status: "needs_reconciliation",
        refund_id: 987654321,
        refund_admin_graphql_api_id: "gid://shopify/Refund/987654321",
        raw_note: "refund note with buyer@example.com",
      },
    ]);
    await writeJsonl(dataDir, "return_refs.jsonl", [
      {
        schema_version: "counterpilot.return_ref.v1",
        transaction_id: "cp_offer_report_001",
        status: "held_before_paid",
        return_id: 246813579,
        return_admin_graphql_api_id: "gid://shopify/Return/246813579",
        tracking_url: "https://tracking.example/secret-return-tracking",
        note: "Decline note 555-0100",
      },
    ]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.data_quality.reconciliation_hold_count, 2);
    assert.match(markdown, /Refund ref statuses: needs_reconciliation: 1/);
    assert.match(markdown, /Return ref statuses: held_before_paid: 1/);
    assertNoRawReportLeak(markdown);
  });
});

test("report is explicitly non-causal and not a recommendation model", async () => {
  await withDataDir(async (dataDir) => {
    await writeOfferEvents(dataDir, [...baseFlow(), matureEvent({})]);

    const { markdown, report } = await runReport(dataDir);

    assert.equal(report.language_boundaries.causal_claim, false);
    assert.equal(report.language_boundaries.recommendation_model, false);
    assert.match(markdown, /Non-causal report: true/);
    assert.match(markdown, /Recommendation model: false/);
    assert.doesNotMatch(markdown, /conversion lift[^.]*was/i);
    assert.doesNotMatch(markdown, /recommendation: counter/i);
  });
});
