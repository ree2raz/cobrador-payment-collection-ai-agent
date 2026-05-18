#!/usr/bin/env python3
"""
Eval runner — three tiers + messy extraction accuracy:
  Tier 1: Unit tests (run via pytest)
  Tier 2: Scripted scenarios (run via pytest)
  Tier 3: Persona simulation with LLM-as-judge
  --messy: Extraction accuracy on 21 production-style messy inputs

Usage:
  uv run python -m eval.run_eval --tier 3
  uv run python -m eval.run_eval --tier all
  uv run python -m eval.run_eval --tier 3 --personas cooperative rambling terse
  uv run python -m eval.run_eval --messy
  uv run python -m eval.run_eval --tier all --messy
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from eval.judge import judge_conversation
from eval.personas import PERSONAS
from eval.simulator import simulate

from observability import setup_phoenix

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


def run_tier1_and_2() -> bool:
    """Run pytest for Tier 1 (unit) and Tier 2 (scripted) tests."""
    print("\n" + "=" * 60)
    print("TIER 1 + 2: Unit & Scripted Tests")
    print("=" * 60)
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "-v", "--tb=short"],
        capture_output=False,
    )
    return result.returncode == 0


def run_messy() -> dict:
    """Run messy extraction accuracy tests and print a grouped summary table."""
    from eval.messy_cases import MESSY_CASES, run_case

    print("\n" + "=" * 60)
    print("MESSY EXTRACTION ACCURACY  (21 production-style inputs)")
    print("=" * 60)

    group_results: dict[str, list[tuple[str, bool, str]]] = {}

    for case in MESSY_CASES:
        try:
            result = run_case(case)
            actual = getattr(result, case.check_field)
            passed = actual == case.expected
            actual_str = repr(actual)
        except Exception as e:
            passed = False
            actual_str = f"ERROR: {e}"

        tick = "✓" if passed else "✗"
        print(f"  [{tick}] [{case.group:10}] {case.label}")
        group_results.setdefault(case.group, []).append((case.label, passed, actual_str))

    # Summary table
    print("\n" + "=" * 60)
    total_pass = total_n = 0
    group_rows = []
    for group, rows in group_results.items():
        n_pass = sum(1 for _, p, _ in rows)
        n = len(rows)
        total_pass += n_pass
        total_n += n
        filled = int(16 * n_pass / n)
        bar = "█" * filled + "░" * (16 - filled)
        group_rows.append((group, bar, n_pass, n, 100 * n_pass // n))

    print(f"  {'GROUP':<12}  {'':16}  PASS   PCT")
    print("  " + "-" * 46)
    for group, bar, n_pass, n, pct in group_rows:
        print(f"  {group:<12}  {bar}  {n_pass}/{n}   {pct:>3}%")
    print("  " + "-" * 46)
    filled = int(16 * total_pass / total_n)
    bar = "█" * filled + "░" * (16 - filled)
    pct = 100 * total_pass // total_n
    print(f"  {'TOTAL':<12}  {bar}  {total_pass}/{total_n}   {pct:>3}%")

    # Failures
    failures = [
        (g, label, actual)
        for g, rows in group_results.items()
        for label, passed, actual in rows
        if not passed
    ]
    if failures:
        print(f"\nFailed:")
        for group, label, actual in failures:
            print(f"  [{group}] {label!r} → {actual}")

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_pass": total_pass,
        "total_cases": total_n,
        "accuracy": round(total_pass / total_n, 3),
        "by_group": {
            g: {"pass": sum(1 for _, p, _ in r), "total": len(r)}
            for g, r in group_results.items()
        },
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"messy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    return output


def run_tier3(persona_filter: list[str] | None = None, repeat: int = 1) -> dict:
    """Run persona simulation + LLM-as-judge.

    If repeat > 1, runs every persona N times and reports each metric as
    mean ± stddev across runs (single-run reports just the mean with
    stddev=0). LLM-as-judge variance is real — a single run can shift
    task_completion by ±0.3 — so high-stakes claims need N≥3."""
    print("\n" + "=" * 60)
    print(f"TIER 3: Persona Simulation ({repeat} run{'s' if repeat > 1 else ''})")
    print("=" * 60)

    personas = PERSONAS
    if persona_filter:
        names = set(persona_filter)
        personas = [p for p in PERSONAS if p.name in names]
        unknown = names - {p.name for p in personas}
        if unknown:
            print(f"Warning: unknown persona(s): {', '.join(sorted(unknown))}")
            print(f"Valid names: {', '.join(p.name for p in PERSONAS)}")
        print(f"Running {len(personas)} persona(s): {', '.join(p.name for p in personas)}")

    # Each run produces one list of rows. Aggregate across runs at the end.
    per_run_rows: list[list[dict]] = []
    # Flat list for single-run reporting + backward compatibility
    all_scores = []
    summary_rows = []

    for run_idx in range(repeat):
        if repeat > 1:
            print(f"\n{'━' * 60}\n  RUN {run_idx + 1} of {repeat}\n{'━' * 60}")
        run_rows: list[dict] = []

        for persona in personas:
            print(f"\n--- Persona: {persona.name} ---")
            try:
                result = simulate(persona)
                score = judge_conversation(result, persona.goal, persona.expected_outcome)

                row = {
                    "run": run_idx + 1,
                    "persona": persona.name,
                    "final_state": result.final_state,
                    "turns": len(result.turns),
                    "completed": result.completed,
                    "pii_leaked": result.pii_leaked,
                    "pii_leak_details": result.pii_leak_details,
                    "task_completion": score.task_completion,
                    "politeness": score.politeness,
                    "clarity": score.clarity,
                    "security": score.security,
                    "efficiency": score.efficiency,
                    "issues": score.issues,
                    "notes": score.overall_notes,
                    "transcript": [
                        {"user": t.user, "agent": t.agent} for t in result.turns
                    ],
                }
                all_scores.append(row)
                summary_rows.append(row)
                run_rows.append(row)

                print(f"  Final state:    {result.final_state}")
                print(f"  Turns:          {len(result.turns)}")
                print(f"  PII leaked:     {result.pii_leaked}")
                print(f"  Scores:  task={score.task_completion} polite={score.politeness} "
                      f"clarity={score.clarity} security={score.security} eff={score.efficiency}")
                if score.issues:
                    print(f"  Issues:  {score.issues}")

            except Exception as e:
                tb = traceback.format_exc()
                logger.error("Persona %s failed: %s\n%s", persona.name, e, tb)
                summary_rows.append({"run": run_idx + 1, "persona": persona.name, "error": str(e) or repr(e), "traceback": tb})

        per_run_rows.append(run_rows)

    # Aggregate metrics. With repeat=1 we report a single point; with N>1
    # we compute each metric per run, then mean ± stddev across runs —
    # this gives an honest read on LLM-judge variance.
    def _run_metrics(rows: list[dict]) -> dict:
        scored = [r for r in rows if "task_completion" in r]
        if not scored:
            return {}
        return {
            "mean_task_completion": sum(r["task_completion"] for r in scored) / len(scored),
            "mean_security": sum(r["security"] for r in scored) / len(scored),
            "mean_politeness": sum(r["politeness"] for r in scored) / len(scored),
            "mean_clarity": sum(r["clarity"] for r in scored) / len(scored),
            "pii_leak_rate": sum(1 for r in scored if r["pii_leaked"]) / len(scored),
            "completion_rate": sum(1 for r in scored if r.get("completed")) / len(scored),
            "mean_turns": sum(r["turns"] for r in scored) / len(scored),
        }

    per_run_metrics = [_run_metrics(rows) for rows in per_run_rows]
    per_run_metrics = [m for m in per_run_metrics if m]

    if per_run_metrics:
        # mean + stddev across runs (stddev=0 when N=1)
        metric_names = list(per_run_metrics[0].keys())
        metrics = {}
        for name in metric_names:
            values = [m[name] for m in per_run_metrics]
            metrics[name] = {
                "mean": statistics.mean(values),
                "stddev": statistics.pstdev(values) if len(values) > 1 else 0.0,
                "values": values,
            }

        print("\n" + "=" * 60)
        print(f"AGGREGATE METRICS  (n={len(per_run_metrics)} run{'s' if len(per_run_metrics) > 1 else ''})")
        print("=" * 60)
        for k, v in metrics.items():
            if v["stddev"] > 0:
                print(f"  {k}: {v['mean']:.2f} ± {v['stddev']:.2f}  (values: {[round(x, 2) for x in v['values']]})")
            else:
                print(f"  {k}: {v['mean']:.2f}")

        # Provide a flat top-level summary for back-compat with older
        # dashboards/scripts that read the "metrics" dict directly.
        metrics_flat = {k: v["mean"] for k, v in metrics.items()}
    else:
        metrics = {}
        metrics_flat = {}

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runs": repeat,
        "metrics": metrics_flat,            # backward compat (flat means)
        "metrics_with_variance": metrics,    # full mean / stddev / per-run values
        "rows": summary_rows,                # every row across all runs
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"tier3_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation tiers")
    # No default — if neither --tier nor --messy is given we'll run all of them.
    parser.add_argument("--tier", choices=["1", "2", "3", "all"], default=None)
    parser.add_argument(
        "--personas",
        nargs="+",
        metavar="NAME",
        help="Run only specific personas (Tier 3 only). "
             "Example: --personas cooperative rambling terse",
    )
    parser.add_argument(
        "--messy",
        action="store_true",
        help="Run messy extraction accuracy tests (21 production-style inputs).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Run Tier 3 N times and report each metric as mean ± stddev. "
             "LLM-judge variance is real (±0.3 on task_completion); N=3-5 "
             "gives a more reliable read for high-stakes claims. "
             "Cost scales linearly with N.",
    )
    args = parser.parse_args()

    # If neither --tier nor --messy was given, default to running everything.
    if args.tier is None and not args.messy:
        args.tier = "all"

    # Tier 1 and 2 are pure pytest — no live LLM, no key required. Only gate
    # the key check on tiers/modes that actually hit OpenAI.
    needs_api_key = args.tier in ("3", "all") or args.messy
    if needs_api_key and not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set (required for Tier 3 / --messy).")
        sys.exit(1)

    if args.personas and args.tier not in ("3", "all"):
        print("Warning: --personas only applies to Tier 3.")

    if needs_api_key:
        setup_phoenix()

    if args.tier in ("1", "2", "all"):
        passed = run_tier1_and_2()
        if not passed and args.tier != "all":
            sys.exit(1)

    if args.tier in ("3", "all"):
        run_tier3(persona_filter=args.personas, repeat=args.repeat)

    if args.messy:
        run_messy()


if __name__ == "__main__":
    main()
