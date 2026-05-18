"""
Local LLM observability via Arize Phoenix.

## Persistent setup (recommended)
Run Phoenix as a standalone server once in its own terminal:

    uv run python -m phoenix.server.main serve

It persists traces to ~/.phoenix/ and stays up between eval runs.
Browse traces at http://localhost:6006 any time.

Then in your eval/CLI terminal:

    PHOENIX=1 uv run python -m eval.run_eval --tier 3
    PHOENIX=1 uv run python cli.py

## One-shot setup (traces lost when process exits)
Just set PHOENIX=1 — observability.py will launch an in-process server
automatically. Traces are gone when the process finishes.

To remove entirely: delete this file and the two lines in cli.py /
eval/run_eval.py that call setup_phoenix().
"""
import os

PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"


def _phoenix_already_running() -> bool:
    """Return True if a Phoenix server is already reachable at localhost:6006."""
    try:
        import httpx
        r = httpx.get("http://localhost:6006/healthz", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


def setup_phoenix() -> bool:
    """Wire up OTEL tracing to Phoenix. Returns True if activated."""
    if not os.getenv("PHOENIX"):
        return False

    try:
        from phoenix.otel import register

        already_up = _phoenix_already_running()

        if not already_up:
            # Start an in-process server (traces lost on process exit).
            # For persistence, run `uv run python -m phoenix.server.main serve`
            # in a separate terminal before running this script.
            import phoenix as px
            px.launch_app()
            print("Phoenix started (in-process) at http://localhost:6006")
            print("  Tip: run `uv run python -m phoenix.server.main serve` in a")
            print("  separate terminal for traces that persist between runs.")
        else:
            print("Phoenix server already running — connecting to http://localhost:6006")

        register(
            project_name="cobrador-payment-agent",
            endpoint=PHOENIX_ENDPOINT,
            auto_instrument=True,
        )
        return True

    except ImportError as e:
        print(f"Phoenix not installed — run: uv sync --group dev. Missing: {e}")
        return False
