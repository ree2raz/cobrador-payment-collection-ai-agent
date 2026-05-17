#!/usr/bin/env python3
"""
Eval runner — three tiers:
  Tier 1: Unit tests (run via pytest)
  Tier 2: Scripted scenarios (run via pytest)
  Tier 3: Persona simulation with LLM-as-judge

Usage:
  uv run python -m eval.run_eval --tier 3
  uv run python -m eval.run_eval --tier all
  uv run python -m eval.run_eval --tier 3 --personas cooperative rambling terse
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
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set.")
        sys.exit(1)

    if args.personas and args.tier not in ("3", "all"):
        print("Warning: --personas only applies to Tier 3.")

    setup_phoenix()

    if args.tier in ("1", "2", "all"):
        passed = run_tier1_and_2()
        if not passed and args.tier != "all":
            sys.exit(1)

    if args.tier in ("3", "all"):
        run_tier3(persona_filter=args.personas)


if __name__ == "__main__":
    main()
