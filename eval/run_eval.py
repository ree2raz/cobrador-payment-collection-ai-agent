#!/usr/bin/env python3
"""
Eval runner — three tiers:
  Tier 1: Unit tests (run via pytest)
  Tier 2: Scripted scenarios (run via pytest)
  Tier 3: Persona simulation with LLM-as-judge

Usage:
  uv run python -m eval.run_eval --tier 3
  uv run python -m eval.run_eval --tier all
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
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


def run_tier3() -> dict:
    """Run persona simulation + LLM-as-judge."""
    print("\n" + "=" * 60)
    print("TIER 3: Persona Simulation")
    print("=" * 60)

    all_scores = []
    summary_rows = []

    for persona in PERSONAS:
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
            logger.error("Persona %s failed: %s", persona.name, e, exc_info=True)
            summary_rows.append({"persona": persona.name, "error": str(e)})

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

    output = {"timestamp": datetime.utcnow().isoformat(), "metrics": metrics, "rows": summary_rows}

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"tier3_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation tiers")
    parser.add_argument("--tier", choices=["1", "2", "3", "all"], default="all")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set.")
        sys.exit(1)

    setup_phoenix()

    if args.tier in ("1", "2", "all"):
        passed = run_tier1_and_2()
        if not passed and args.tier != "all":
            sys.exit(1)

    if args.tier in ("3", "all"):
        run_tier3()


if __name__ == "__main__":
    main()
