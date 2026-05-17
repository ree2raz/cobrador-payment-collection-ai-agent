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


def run_tier3(persona_filter: list[str] | None = None) -> dict:
    """Run persona simulation + LLM-as-judge."""
    print("\n" + "=" * 60)
    print("TIER 3: Persona Simulation")
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

    all_scores = []
    summary_rows = []

    for persona in personas:
        print(f"\n--- Persona: {persona.name} ---")
        try:
            result = simulate(persona)
            score = judge_conversation(result, persona.goal, persona.expected_outcome)

            row = {
                "persona": persona.name,
                "final_state": result.final_state,
                "turns": len(result.turns),
                "completed": result.completed,
                "pii_leaked": result.pii_leaked,
                "task_completion": score.task_completion,
                "politeness": score.politeness,
                "clarity": score.clarity,
                "security": score.security,
                "efficiency": score.efficiency,
                "issues": score.issues,
                "notes": score.overall_notes,
            }
            all_scores.append(row)
            summary_rows.append(row)

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
            summary_rows.append({"persona": persona.name, "error": str(e) or repr(e), "traceback": tb})

    # Aggregate metrics
    scored = [r for r in all_scores if "task_completion" in r]
    if scored:
        metrics = {
            "mean_task_completion": sum(r["task_completion"] for r in scored) / len(scored),
            "mean_security": sum(r["security"] for r in scored) / len(scored),
            "mean_politeness": sum(r["politeness"] for r in scored) / len(scored),
            "mean_clarity": sum(r["clarity"] for r in scored) / len(scored),
            "pii_leak_rate": sum(1 for r in scored if r["pii_leaked"]) / len(scored),
            "completion_rate": sum(1 for r in scored if r.get("completed")) / len(scored),
            "mean_turns": sum(r["turns"] for r in scored) / len(scored),
        }

        print("\n" + "=" * 60)
        print("AGGREGATE METRICS")
        print("=" * 60)
        for k, v in metrics.items():
            print(f"  {k}: {v:.2f}")
    else:
        metrics = {}

    output = {"timestamp": datetime.now(timezone.utc).isoformat(), "metrics": metrics, "rows": summary_rows}

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"tier3_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation tiers")
    parser.add_argument("--tier", choices=["1", "2", "3", "all"], default="all")
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
    args = parser.parse_args()

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
        run_tier3(persona_filter=args.personas)

    if args.messy:
        run_messy()


if __name__ == "__main__":
    main()
