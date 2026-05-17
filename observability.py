"""
Local LLM observability via Arize Phoenix.

Activate by setting PHOENIX=1 in your environment:
    PHOENIX=1 uv run python cli.py
    PHOENIX=1 uv run python -m eval.run_eval

UI opens at http://localhost:6006

To remove entirely: delete this file and the two lines in cli.py / eval/run_eval.py
that call `from observability import setup_phoenix; setup_phoenix()`.
"""
import os


def setup_phoenix() -> bool:
    """Start Phoenix and instrument OpenAI. Returns True if activated."""
    if not os.getenv("PHOENIX"):
        return False

    try:
        import phoenix as px
        from openinference.instrumentation.openai import OpenAIInstrumentor

        px.launch_app()
        OpenAIInstrumentor().instrument()
        print("Phoenix running at http://localhost:6006")
        return True
    except ImportError:
        print("Phoenix not installed — run: uv sync (dev deps required)")
        return False
