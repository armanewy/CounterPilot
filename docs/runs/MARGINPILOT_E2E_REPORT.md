# MarginPilot E2E Report

- Transaction ID: `mp_txn_29edeaebbcb04a67`
- Mature state: `mature`
- Duplicate order webhook idempotent: `True`
- Mature contribution margin minor: `16166`
- Model recommendations present: `False`
- PII redaction checks: `{'no_email': True, 'no_shopify_gid': True, 'no_checkout_url': True, 'no_buyer_message': True}`

```json
{
  "events": {
    "buyer_accept_state": "buyer_accepted",
    "checkout_state": "checkout_created",
    "duplicate_order_created": {
      "delivery_id": "delivery_order_created",
      "delivery_replay": true,
      "result": {
        "current_state": "order_created",
        "idempotent_replay": true,
        "imported": false,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "order_created",
        "errors": [],
        "event_count": 5,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [],
        "pending_event_ids": [],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "order_created"
    },
    "mature_state": "mature",
    "merchant_counter_state": "merchant_countered",
    "offer": [
      "mp_txn_29edeaebbcb04a67_offer_submitted"
    ],
    "order_created": {
      "delivery_id": "delivery_order_created",
      "delivery_replay": false,
      "result": {
        "current_state": "order_created",
        "idempotent_replay": false,
        "imported": true,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "order_created",
        "errors": [],
        "event_count": 5,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [],
        "pending_event_ids": [],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "order_created"
    },
    "out_of_order_return_close": {
      "delivery_id": "delivery_return_close",
      "delivery_replay": false,
      "result": {
        "current_state": "partially_refunded",
        "idempotent_replay": false,
        "imported": true,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [
          "shopify_delivery_return_close"
        ],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created",
          "shopify_delivery_order_paid",
          "shopify_delivery_refund_partial"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "partially_refunded",
        "errors": [],
        "event_count": 8,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [
          {
            "event": {
              "currency": "USD",
              "economics": {},
              "event_hash": "6dd84e97d1f18b4e0d5e475d19445e1a58dd2f8750be5f1803b91c14d0937ba6",
              "event_id": "shopify_delivery_return_close",
              "idempotency_key": "delivery_return_close",
              "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
              "occurred_at": "2026-06-22T10:19:00+00:00",
              "received_at": "2026-06-22T10:19:00+00:00",
              "schema_version": "marginpilot.transaction_event.v1",
              "source": "shopify_webhook",
              "transaction_id": "mp_txn_29edeaebbcb04a67",
              "transition_to": "return_closed"
            },
            "reason": "missing_predecessor",
            "state": "partially_refunded"
          }
        ],
        "pending_event_ids": [
          "shopify_delivery_return_close"
        ],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "return_closed"
    },
    "paid": {
      "delivery_id": "delivery_order_paid",
      "delivery_replay": false,
      "result": {
        "current_state": "paid",
        "idempotent_replay": false,
        "imported": true,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created",
          "shopify_delivery_order_paid"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "paid",
        "errors": [],
        "event_count": 6,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [],
        "pending_event_ids": [],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "paid"
    },
    "partial_refund": {
      "delivery_id": "delivery_refund_partial",
      "delivery_replay": false,
      "result": {
        "current_state": "partially_refunded",
        "idempotent_replay": false,
        "imported": true,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created",
          "shopify_delivery_order_paid",
          "shopify_delivery_refund_partial"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "partially_refunded",
        "errors": [],
        "event_count": 7,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [],
        "pending_event_ids": [],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "partially_refunded"
    },
    "return_open": {
      "delivery_id": "delivery_return_open",
      "delivery_replay": false,
      "result": {
        "current_state": "return_closed",
        "idempotent_replay": false,
        "imported": true,
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending_event_ids": [],
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "state": {
        "applied_event_ids": [
          "mp_txn_29edeaebbcb04a67_offer_submitted",
          "mp_txn_29edeaebbcb04a67_merchant_countered",
          "mp_txn_29edeaebbcb04a67_buyer_accepted",
          "mp_txn_29edeaebbcb04a67_checkout_created",
          "shopify_delivery_order_created",
          "shopify_delivery_order_paid",
          "shopify_delivery_refund_partial",
          "shopify_delivery_return_open",
          "shopify_delivery_return_close"
        ],
        "available_actions": [
          {
            "action": "create_checkout"
          },
          {
            "action": "cancel"
          }
        ],
        "currency": "USD",
        "current_state": "return_closed",
        "errors": [],
        "event_count": 9,
        "executed_actions": [
          {
            "action": "counter"
          },
          {
            "action": "create_checkout"
          }
        ],
        "mature_outcome": null,
        "merchant_decisions": [
          {
            "action": "counter",
            "amount_minor": 76000,
            "merchant_floor_minor": 69000
          },
          {
            "action": "create_checkout"
          }
        ],
        "merchant_namespace": "merchant_demo_refurb:store_demo_shopify",
        "pending": [],
        "pending_event_ids": [],
        "recommendations": [
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          },
          {
            "recommendation_id": null,
            "system_mode": "manual_only"
          }
        ],
        "schema_version": "marginpilot.transaction_snapshot.v1",
        "transaction_id": "mp_txn_29edeaebbcb04a67"
      },
      "transition": "return_opened"
    }
  },
  "financial_components": {
    "cost_basis_minor": 52000,
    "final_sale_price_minor": 76000,
    "mature_contribution_margin_minor": 16166,
    "partial_refund_minor": 1000,
    "reconciled_fees_minor": 2234,
    "reconciled_fulfillment_cost_minor": 4600,
    "reconciliation_formula": "final_sale_price - cost_basis - fees - fulfillment - partial_refund",
    "reconciliation_verified": true
  },
  "idempotency_behavior": {
    "duplicate_delivery_replay": true,
    "duplicate_order_created_replay": true,
    "duplicate_order_event_count": 5
  },
  "merchant_inbox": {
    "first_offer_state": "offer_submitted",
    "offer_count_after_submit": 1
  },
  "model_recommendations_present": false,
  "out_of_order_behavior": {
    "pending_after_reconciliation": [],
    "return_close_pending_before_open": true,
    "state_after_reconciliation": "return_closed"
  },
  "pii_redaction": {
    "no_buyer_message": true,
    "no_checkout_url": true,
    "no_email": true,
    "no_shopify_gid": true
  },
  "research_projection": {
    "consent_lineage": [
      {
        "active": true,
        "consent_policy_version": "marginpilot-ml-consent-v1",
        "granted_at": "2026-06-22T09:50:00+00:00",
        "granted_purposes": [
          "merchant_specific_model_training",
          "merchant_specific_policy_evaluation",
          "merchant_specific_shadow_recommendations"
        ],
        "merchant_id": "merchant_demo_refurb",
        "policy_hash": "39478728aa5879f7117f33b413eeb476b218f8697f865faf56c87332f651688b",
        "prohibited_purposes": [
          "cross_merchant_training"
        ],
        "purpose": "merchant_specific_model_training",
        "record_hash": "d9ae344fc423e5c1629c0e998ae522d1e0457377804276fda9349e428226dcb4",
        "record_id": "marginpilot_consent_b7cb9862192667f8d3472cfe",
        "revoked_at": null,
        "store_id": "store_demo_shopify"
      }
    ],
    "dataset_lineage": {
      "dataset_id": "b6de9535993b1337bb5e1612e5c6f7cc3e2de2a7c0f95bf058807d3dc6addb09",
      "event_count": 1,
      "event_hashes": [
        "68fe778e8aa693374dfeaa4e6e823e9d42f86b66dd1895bcfc50469f7e4d3d08"
      ],
      "merchant_store_pairs": [
        {
          "merchant_id": "merchant_demo_refurb",
          "store_id": "store_demo_shopify"
        }
      ],
      "research_record_type": "marginpilot_research_event"
    },
    "model_features": [
      {
        "asking_price_minor": 90000,
        "buyer_offer_amount_minor": 72000,
        "category": "refurbished technology",
        "counter_amount_minor": 76000,
        "financial_cost_basis_minor": 52000,
        "financial_mature_contribution_margin_minor": 16166,
        "financial_reconciled_fees_minor": 2234,
        "financial_reconciled_fulfillment_costs_minor": 4600,
        "financial_refund_total_minor": 1000,
        "surface": "product_page_offer"
      }
    ],
    "purpose": "merchant_specific_model_training",
    "rows": [
      {
        "decision": {
          "amount_minor": 76000,
          "selected_action": "counter_at_amount"
        },
        "event_hash": "68fe778e8aa693374dfeaa4e6e823e9d42f86b66dd1895bcfc50469f7e4d3d08",
        "features": {
          "asking_price_minor": 90000,
          "buyer_offer_amount_minor": 72000,
          "category": "refurbished technology",
          "counter_amount_minor": 76000,
          "financial_cost_basis_minor": 52000,
          "financial_mature_contribution_margin_minor": 16166,
          "financial_reconciled_fees_minor": 2234,
          "financial_reconciled_fulfillment_costs_minor": 4600,
          "financial_refund_total_minor": 1000,
          "surface": "product_page_offer"
        },
        "merchant_id": "merchant_demo_refurb",
        "outcome": {
          "buyer_paid": true,
          "return_window_matured": true,
          "returned": false
        },
        "pseudonymous_buyer_id": "mp_buyer_43fd0b3e0dddc0f9a911165c17f06e61",
        "pseudonymous_session_id": "mp_session_1f579838cdada1312fc88141c10e2350",
        "store_id": "store_demo_shopify"
      }
    ],
    "schema_version": "marginpilot_research_export.v1"
  },
  "schema_version": "marginpilot_shopify_e2e_report.v1",
  "shopify_resource_linkage": {
    "checkout_link_available_to_delivery_flow": true,
    "checkout_link_reported_value": "operational_store_only",
    "fake_draft_order_count": 1,
    "stored_in": "operational_store"
  },
  "state_transition_log": [
    {
      "event_id": "mp_txn_29edeaebbcb04a67_offer_submitted",
      "occurred_at": "2026-06-22T10:00:00+00:00",
      "received_at": "2026-06-22T10:00:00+00:00",
      "source": "shopify_theme_app_extension",
      "transition_to": "offer_submitted"
    },
    {
      "event_id": "mp_txn_29edeaebbcb04a67_merchant_countered",
      "occurred_at": "2026-06-22T10:05:00+00:00",
      "received_at": "2026-06-22T10:05:00+00:00",
      "source": "embedded_admin",
      "transition_to": "merchant_countered"
    },
    {
      "event_id": "mp_txn_29edeaebbcb04a67_buyer_accepted",
      "occurred_at": "2026-06-22T10:10:00+00:00",
      "received_at": "2026-06-22T10:10:00+00:00",
      "source": "buyer_offer_surface",
      "transition_to": "buyer_accepted"
    },
    {
      "event_id": "mp_txn_29edeaebbcb04a67_checkout_created",
      "occurred_at": "2026-06-22T10:11:00+00:00",
      "received_at": "2026-06-22T10:11:00+00:00",
      "source": "shopify_admin_graphql",
      "transition_to": "checkout_created"
    },
    {
      "event_id": "shopify_delivery_order_created",
      "occurred_at": "2026-06-22T10:12:00+00:00",
      "received_at": "2026-06-22T10:12:01+00:00",
      "source": "shopify_webhook",
      "transition_to": "order_created"
    },
    {
      "event_id": "shopify_delivery_order_paid",
      "occurred_at": "2026-06-22T10:14:00+00:00",
      "received_at": "2026-06-22T10:14:00+00:00",
      "source": "shopify_webhook",
      "transition_to": "paid"
    },
    {
      "event_id": "shopify_delivery_refund_partial",
      "occurred_at": "2026-06-22T10:16:00+00:00",
      "received_at": "2026-06-22T10:16:00+00:00",
      "source": "shopify_webhook",
      "transition_to": "partially_refunded"
    },
    {
      "event_id": "shopify_delivery_return_open",
      "occurred_at": "2026-06-22T10:18:00+00:00",
      "received_at": "2026-06-22T10:18:00+00:00",
      "source": "shopify_webhook",
      "transition_to": "return_opened"
    },
    {
      "event_id": "shopify_delivery_return_close",
      "occurred_at": "2026-06-22T10:19:00+00:00",
      "received_at": "2026-06-22T10:19:00+00:00",
      "source": "shopify_webhook",
      "transition_to": "return_closed"
    },
    {
      "event_id": "mp_txn_29edeaebbcb04a67_mature",
      "occurred_at": "2026-07-22T10:20:00+00:00",
      "received_at": "2026-07-22T10:20:00+00:00",
      "source": "marginpilot_return_window",
      "transition_to": "mature"
    }
  ],
  "surface": {
    "merchant_id": "merchant_demo_refurb",
    "network_calls": 0,
    "schema_version": "marginpilot.shopify_adapter.v1",
    "store_id": "store_demo_shopify",
    "surface": "theme_app_extension_product_block",
    "visibility": {
      "enabled": true,
      "product_gid_reference": "operational_store"
    }
  },
  "transaction_id": "mp_txn_29edeaebbcb04a67"
}
```
