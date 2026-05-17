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

PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"


def setup_phoenix() -> bool:
    """Launch Phoenix, register OTEL tracer provider, auto-instrument OpenAI.
    Returns True if activated."""
    if not os.getenv("PHOENIX"):
        return False

    try:
        import phoenix as px
        from phoenix.otel import register

        # Start the Phoenix UI server
        px.launch_app()

        # Register the OTEL tracer provider and auto-instrument OpenAI SDK.
        # auto_instrument=True picks up openinference-instrumentation-openai
        # automatically — no manual OpenAIInstrumentor() call needed.
        register(
            project_name="cobrador-payment-agent",
            endpoint=PHOENIX_ENDPOINT,
            auto_instrument=True,
        )

        print(f"Phoenix running at http://localhost:6006")
        return True
    except ImportError as e:
        print(f"Phoenix not installed — run: uv sync --group dev. Missing: {e}")
        return False
