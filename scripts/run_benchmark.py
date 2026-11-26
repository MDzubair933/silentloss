#!/usr/bin/env python3
"""
CLI Runner for SilentLoss Benchmark Harness.
Usage:
    python scripts/run_benchmark.py --scenario B-01 --dry-run
"""

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.benchmark.harness import BenchmarkHarness
from src.benchmark.scenarios import DEFAULT_CONCURRENCY, MAX_SAFE_CONCURRENCY, STANDARD_SCENARIOS, validate_concurrency


def main():
    parser = argparse.ArgumentParser(description="SilentLoss Configurable Benchmark Driver")
    parser.add_argument(
        "--scenario",
        choices=list(STANDARD_SCENARIOS.keys()) + ["ALL"],
        default="B-01",
        help="Scenario ID to execute",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Client concurrency threads (default={DEFAULT_CONCURRENCY}, max={MAX_SAFE_CONCURRENCY})",
    )
    parser.add_argument("--seed", type=int, default=20261126, help="Deterministic dataset RNG seed")
    parser.add_argument("--pacing-sec", type=float, default=0.0, help="Optional inter-request pacing delay")
    parser.add_argument("--api-endpoint", default=None, help="Deployed IAM-authenticated API Gateway URL")
    parser.add_argument("--region", default="us-east-1", help="AWS Region for SigV4 signing")
    parser.add_argument("--profile", default=None, help="Optional AWS CLI profile name")
    parser.add_argument("--dry-run", action="store_true", help="Validate plan without issuing live HTTP requests")
    parser.add_argument("--output-file", default=None, help="Save execution summary to JSON")
    parser.add_argument("--jsonl-log", default=None, help="Incremental raw request JSONL persistence path")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--orders-table")
    parser.add_argument("--fulfillment-table")
    parser.add_argument("--rejection-table")
    parser.add_argument("--outbox-table")
    parser.add_argument("--queue-url")
    parser.add_argument("--dlq-url")
    parser.add_argument("--convergence-seconds", type=float, default=60)
    args = parser.parse_args()

    try:
        concurrency = validate_concurrency(args.concurrency)
    except ValueError as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        sys.exit(2)

    scenarios_to_run = list(STANDARD_SCENARIOS.keys()) if args.scenario == "ALL" else [args.scenario]
    observer, duplicate_sender = None, None
    if not args.dry_run:
        if not all([args.api_endpoint, args.jsonl_log, args.output_file, args.orders_table,
                    args.fulfillment_table, args.rejection_table, args.queue_url, args.dlq_url]):
            parser.error("Live execution requires endpoint, JSONL, summary output, three tables and both queue URLs")
        import boto3
        from botocore.config import Config
        from src.reconciliation.aws_acquisition import AwsEvidenceAcquirer
        from src.benchmark.transport import SqsDuplicateControl
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        acquirer = AwsEvidenceAcquirer(args.orders_table,args.fulfillment_table,args.rejection_table,
                                      args.queue_url,args.dlq_url,"","",session=session,
                                      connect_timeout=5.0,read_timeout=10.0,max_attempts=2,
                                      outbox_table=args.outbox_table)
        observer = acquirer.await_convergence
        if "B-04" in scenarios_to_run:
            duplicate_sender = SqsDuplicateControl(args.queue_url,session.client("sqs",config=Config(
                connect_timeout=5.0,read_timeout=10.0,retries={"total_max_attempts":2,"mode":"standard"})))
    harness = BenchmarkHarness(
        api_endpoint=args.api_endpoint if not args.dry_run else None,
        region=args.region,
        profile_name=args.profile,
        seed=args.seed,
        run_id=args.run_id,
        observer=observer,
        duplicate_sender=duplicate_sender,
        convergence_seconds=args.convergence_seconds,
    )

    print("\n" + "=" * 64)
    print("SILENTLOSS BENCHMARK HARNESS")
    print(f"Run ID:             {harness.run_id}")
    print(f"Seed:               {args.seed}")
    print(f"Scenarios Selected: {scenarios_to_run}")
    print(f"Concurrency Limit:  {concurrency} threads (max safe={MAX_SAFE_CONCURRENCY})")
    print(f"Transport Mode:     {harness.transport_mode}")
    print("=" * 64)

    if args.dry_run:
        for s_id in scenarios_to_run:
            plan = harness.build_deterministic_plan(STANDARD_SCENARIOS[s_id])
            fault_cnt = sum(1 for p in plan if p["scheduled_fault"] is not None)
            print(f"  [{s_id}] Planned steps={len(plan)} (scheduled_faults={fault_cnt})")
        print("Dry-run plan validated. Zero cloud or network requests executed.\n")
        sys.exit(0)

    if not args.api_endpoint:
        print("Error: --api-endpoint is required unless --dry-run is specified.", file=sys.stderr)
        sys.exit(1)

    combined_results = {}
    for s_id in scenarios_to_run:
        print(f"Executing Scenario {s_id} via SigV4 authenticated transport...")
        res = harness.run_scenario(
            s_id,
            max_concurrency=concurrency,
            pacing_sec=args.pacing_sec,
            incremental_jsonl_path=args.jsonl_log,
        )
        combined_results[s_id] = res

    if args.output_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)
        with open(args.output_file, "w", encoding="utf-8") as f:
            def encode(value):
                if is_dataclass(value):
                    return asdict(value)
                if isinstance(value, set):
                    return sorted(value)
                raise TypeError(type(value).__name__)
            json.dump(combined_results, f, indent=2, default=encode)
        print(f"Benchmark summary saved to: {args.output_file}")


if __name__ == "__main__":
    main()
