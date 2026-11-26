#!/usr/bin/env python3
"""
CLI Runner for SilentLoss Independent Evidence Reconciliation.
Usage:
    python scripts/reconcile_evidence.py --evidence-file tests/fixtures/sample_evidence.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.reconciliation.collector import EvidenceReconciliationCollector
from src.reconciliation.models import SourceCompleteness
from src.benchmark.evidence import load_publication_evidence


def main():
    parser = argparse.ArgumentParser(description="SilentLoss Independent Evidence Reconciliation")
    parser.add_argument("--evidence-file", required=True, help="Path to input evidence JSON file")
    parser.add_argument("--output-file", default=None, help="Path to output reconciliation summary JSON")
    parser.add_argument("--plan")
    parser.add_argument("--trace")
    parser.add_argument("--closure")
    args = parser.parse_args()

    if not os.path.exists(args.evidence_file):
        print(f"Error: Evidence file not found: {args.evidence_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.evidence_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    sc_raw = data.get("source_completeness", {})
    # Backwards-compatible check for unobserved_queue_evidence fixture flag
    dlq_ok = sc_raw.get("dlq_evidence_complete", False)
    pub_ok = False
    publication_evidence = {}
    if any([args.plan, args.trace, args.closure]):
        if not all([args.plan, args.trace, args.closure]):
            parser.error("--plan, --trace and --closure must be supplied together")
        run_id, publication_evidence = load_publication_evidence(args.plan, args.trace, args.closure)
        if run_id != sc_raw.get("run_scope"):
            parser.error("Closed trace and acquired evidence run scopes differ")
        pub_ok = True

    sc = SourceCompleteness(
        orders_table_complete=sc_raw.get("orders_table_complete", False),
        fulfillment_table_complete=sc_raw.get("fulfillment_table_complete", False),
        dlq_evidence_complete=dlq_ok,
        rejection_audit_complete=sc_raw.get("rejection_audit_complete", False),
        publication_logs_complete=pub_ok,
        pagination_complete=sc_raw.get("pagination_complete", False),
        run_scope=sc_raw.get("run_scope", "FIXTURE"),
        observation_timestamp_utc=sc_raw.get("observation_timestamp_utc", ""),
    )

    pending_raw = data.get("pending_records", {})

    collector = EvidenceReconciliationCollector()
    summary = collector.reconcile(
        committed_orders=data.get("committed_orders", {}),
        fulfillment_records=data.get("fulfillment_records", []),
        pending_records=pending_raw,
        dlq_records=set(data.get("dlq_order_ids", [])),
        rejection_records=set(data.get("rejection_order_ids", [])),
        publication_outcomes=data.get("publication_outcomes", {}),
        publication_evidence=publication_evidence,
        duplicate_delivery_attempts=data.get("duplicate_delivery_attempts", 0),
        recovery_time_sec=data.get("recovery_time_sec", 0.0),
        source_completeness=sc,
        corrupted_record_ids=set(data.get("corrupted_record_ids", [])),
    )

    result_dict = summary.to_dict()

    print("\n" + "=" * 64)
    print("SILENTLOSS EVIDENCE RECONCILIATION SUMMARY")
    print("=" * 64)
    print(f"Total Committed Orders (C):            {summary.total_committed}")
    print(f"Fulfilled Orders (F):                  {summary.total_fulfilled}")
    print(f"Durably Pending Orders (P):            {summary.total_pending}")
    print(f"Explicitly Failed Orders (E):          {summary.total_explicitly_failed}")
    print(f"Confirmed Silent Loss (U):             {summary.confirmed_silent_loss}")
    print(f"Indeterminate Orders (I):              {summary.indeterminate_orders}")
    print("-" * 64)
    print(f"Silent Loss Rate (U/C):                {summary.silent_loss_rate_percent}%")
    print(f"Fulfillment Completion Rate (F/C):     {summary.fulfillment_completion_rate}%")
    print(f"Duplicate Completed Effects (D):       {summary.duplicate_completed_effects}")
    print(f"Pending Past Deadline:                 {summary.pending_past_deadline}")
    print(f"Unresolved Obligations:                {summary.unresolved_obligations}")
    print(f"Evidence Completeness:                 {summary.evidence_completeness_percent}%")
    print(f"Clean Success Verified:                {summary.clean_success_verified}")
    print(f"Mathematical Invariant Holds (C==Sum): {summary.invariant_holds}")
    print("=" * 64 + "\n")

    if args.output_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)
        with open(args.output_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2)
        print(f"Reconciliation summary saved to: {args.output_file}")


if __name__ == "__main__":
    main()
