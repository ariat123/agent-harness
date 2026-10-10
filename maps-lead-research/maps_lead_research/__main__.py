"""One supported CLI. All network execution is explicit."""

import argparse
import json
import sys
from pathlib import Path

from .common import lock, read_json
from .config import load, queries
from .geography import plan
from .outputs import export
from .pipeline import discover, enrich, http_fetch, initialize, rebuild
from .providers import ACCOUNT_STATE, Firecrawl, RapidAPI, credentials
from .registry import Registry, import_history
from .input_enrichment import (
    initialize_input_successor,
    input_plan,
    input_status,
    load_input_campaign,
    run_input_enrichment,
)
from .input_finalization import finalize_input_results


def parser():
    p = argparse.ArgumentParser(
        description="Generic Google Maps company discovery and public contact research"
    )
    s = p.add_subparsers(dest="command", required=True)
    for cmd in ["validate", "plan", "discover", "enrich", "run"]:
        sub = s.add_parser(cmd)
        sub.add_argument("campaign", type=Path)
        if cmd in ["discover", "enrich", "run"]:
            sub.add_argument("--allow-paid", action="store_true")
            sub.add_argument("--allow-http", action="store_true")
            sub.add_argument("--backlog", action="store_true")
    s.add_parser("keys")
    sub = s.add_parser("balance")
    sub.add_argument("--live", action="store_true")
    for cmd in ["status", "export", "resume"]:
        sub = s.add_parser(cmd)
        sub.add_argument("run_dir", type=Path)
        if cmd == "resume":
            sub.add_argument(
                "--stage", choices=["discover", "enrich", "run"], default="run"
            )
            sub.add_argument("--allow-paid", action="store_true")
            sub.add_argument("--allow-http", action="store_true")
            sub.add_argument("--backlog", action="store_true")
    sub = s.add_parser("rebuild")
    sub.add_argument("run_dir", type=Path)
    sub.add_argument("--output", type=Path, required=True)
    sub = s.add_parser("import-history")
    sub.add_argument("paths", nargs="+", type=Path)
    sub.add_argument(
        "--registry", type=Path, default=Path("data/research-registry.sqlite")
    )
    sub.add_argument(
        "--report", type=Path, default=Path("docs/historical-import-report.json")
    )
    sub = s.add_parser("batch")
    sub.add_argument("campaigns", nargs="+", type=Path)
    sub.add_argument("--allow-paid", action="store_true")
    sub.add_argument("--allow-http", action="store_true")
    sub = s.add_parser("plan-inputs")
    sub.add_argument("campaign", type=Path)
    sub = s.add_parser("enrich-inputs")
    sub.add_argument("campaign", type=Path)
    sub.add_argument("--allow-paid", action="store_true")
    sub.add_argument("--allow-http", action="store_true")
    sub.add_argument("--search-workers", type=int, default=1)
    sub.add_argument("--search-delay", type=float, default=1.0)
    sub.add_argument("--http-workers", type=int, default=1)
    sub.add_argument("--search-only", action="store_true")
    sub = s.add_parser("resume-inputs")
    sub.add_argument("run_dir", type=Path)
    sub.add_argument("--allow-paid", action="store_true")
    sub.add_argument("--allow-http", action="store_true")
    sub.add_argument("--search-workers", type=int, default=1)
    sub.add_argument("--search-delay", type=float, default=1.0)
    sub.add_argument("--http-workers", type=int, default=1)
    sub.add_argument("--search-only", action="store_true")
    sub = s.add_parser("continue-input-run")
    sub.add_argument("source_run", type=Path)
    sub.add_argument("--output-directory", type=Path, required=True)
    sub.add_argument("--max-firecrawl-credit-delta", type=int, required=True)
    sub = s.add_parser("status-inputs")
    sub.add_argument("run_dir", type=Path)
    sub = s.add_parser("finalize-input-results")
    sub.add_argument("run_dir", type=Path)
    sub.add_argument("--perplexity-results", type=Path, required=True)
    sub.add_argument("--supergrok-results", type=Path, required=True)
    sub.add_argument("--output-directory", type=Path, required=True)
    return p


def execute(c, stage, allow_paid=False, allow_http=False, backlog=False):
    # Validate all prerequisites before creating run state.
    if stage in ["discover", "run"] and not allow_paid:
        raise ValueError("Maps discovery requires explicit --allow-paid")
    if c.get("suppression_path") and not Path(c["suppression_path"]).is_file():
        raise ValueError("Suppression file does not exist")
    with lock(str(Path(c["historical_registry_path"]).resolve()) + ".lock"):
        run = initialize(c)
        with lock(run / ".run.lock"):
            reg = Registry(c["historical_registry_path"])
            try:
                if stage in ["discover", "run"]:
                    discover(run, reg, RapidAPI(allow_paid))
                if stage in ["enrich", "run"]:
                    client = (
                        Firecrawl(True)
                        if allow_paid
                        and c["max_firecrawl_credit_delta"]
                        and c["max_firecrawl_pages_per_domain"]
                        else None
                    )
                    enrich(
                        run,
                        reg,
                        client,
                        http_fetch if allow_http else None,
                        backlog=backlog,
                    )
                return {"run_dir": str(run), **export(run, reg)}
            finally:
                reg.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ["validate", "plan"]:
            c = load(args.campaign)
            value = {"valid": True, "campaign": c}
            if args.command == "plan":
                g = plan(c)
                value.update(
                    geography_plan=g,
                    queries=queries(c),
                    planned_max_page_requests=len(g["targets"])
                    * len(queries(c))
                    * c["max_pages_per_search"],
                    hard_maps_request_ceiling=c["max_maps_requests"],
                    firecrawl_credit_delta=c["max_firecrawl_credit_delta"],
                    network_requests=0,
                )
        elif args.command == "plan-inputs":
            value = input_plan(load_input_campaign(args.campaign))
        elif args.command in ["enrich-inputs", "resume-inputs"]:
            campaign = (
                args.campaign
                if args.command == "enrich-inputs"
                else args.run_dir.resolve() / "campaign.json"
            )
            c = load_input_campaign(campaign)
            run = Path(c["output_directory"])
            if args.command == "resume-inputs" and run.resolve() != args.run_dir.resolve():
                raise ValueError("Run directory differs from its immutable configuration")
            with (
                lock(str(Path(c["historical_registry_path"]).resolve()) + ".lock"),
                lock(run / ".run.lock"),
            ):
                value = run_input_enrichment(
                    c,
                    allow_paid=args.allow_paid,
                    allow_http=args.allow_http and not args.search_only,
                    search_workers=args.search_workers,
                    search_delay=args.search_delay,
                    http_workers=args.http_workers,
                    allow_scrape=not args.search_only,
                )
        elif args.command == "status-inputs":
            value = input_status(args.run_dir)
        elif args.command == "continue-input-run":
            value = initialize_input_successor(
                args.source_run,
                args.output_directory,
                args.max_firecrawl_credit_delta,
            )
        elif args.command == "finalize-input-results":
            value = finalize_input_results(
                args.run_dir,
                args.perplexity_results,
                args.supergrok_results,
                args.output_directory,
            )
        elif args.command == "keys":
            value = credentials()
        elif args.command == "balance":
            if not args.live:
                raise ValueError(
                    "Balance requires --live; this reads account usage without scraping"
                )
            with lock(str(ACCOUNT_STATE) + ".lock"):
                value = {
                    "provider": "firecrawl",
                    "remaining_credits": Firecrawl(True).balance(),
                    "rapidapi_balance": "Use RapidAPI subscription dashboard; no reliable remaining-request endpoint configured",
                }
        elif args.command == "import-history":
            with lock(str(args.registry.resolve()) + ".lock"):
                reg = Registry(args.registry)
                try:
                    value = import_history(reg, args.paths, args.report)
                finally:
                    reg.close()
            value = {k: v for k, v in value.items() if k != "artifacts"}
        elif args.command == "batch":
            configs = [load(p) for p in args.campaigns]
            if (
                len(
                    {
                        str(Path(c["historical_registry_path"]).resolve())
                        for c in configs
                    }
                )
                != 1
            ):
                raise ValueError("Batch campaigns must share one registry")
            if len(
                {str(Path(c["output_directory"]).resolve()) for c in configs}
            ) != len(configs):
                raise ValueError("Batch campaigns require distinct output directories")
            value = []
            for c in configs:
                value.append(execute(c, "discover", args.allow_paid))
            for c in configs:
                value.append(execute(c, "enrich", args.allow_paid, args.allow_http))
        elif args.command == "rebuild":
            value = rebuild(args.run_dir, args.output)
        elif args.command in ["status", "export", "resume"]:
            run = args.run_dir.resolve()
            c = load(run / "campaign.json")
            if args.command == "status":
                value = {
                    "manifest": read_json(run / "manifest.json"),
                    "validation": read_json(run / "validation_report.json"),
                }
            elif args.command == "resume":
                if Path(c["output_directory"]).resolve() != run:
                    raise ValueError(
                        "Run directory differs from its immutable configuration; use offline rebuild for moved runs"
                    )
                value = execute(
                    c, args.stage, args.allow_paid, args.allow_http, args.backlog
                )
            else:
                with (
                    lock(str(Path(c["historical_registry_path"]).resolve()) + ".lock"),
                    lock(run / ".run.lock"),
                ):
                    reg = Registry(c["historical_registry_path"])
                    try:
                        value = export(run, reg)
                    finally:
                        reg.close()
        else:
            value = execute(
                load(args.campaign),
                args.command,
                args.allow_paid,
                args.allow_http,
                args.backlog,
            )
        print(json.dumps(value, indent=2))
        return 0 if not isinstance(value, dict) or value.get("valid", True) else 1
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        # Provider exceptions contain only status codes. Never echo arbitrary exception bodies.
        from .common import redact

        print(json.dumps({"error": redact(str(exc))}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
