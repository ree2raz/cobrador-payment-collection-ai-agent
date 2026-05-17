#!/usr/bin/env python3
"""Interactive CLI for the payment collection agent."""
import os
import sys

from agent import Agent


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    print("=" * 60)
    print("  Cobrador — Payment Collection Agent")
    print("  Type 'quit' or 'exit' to end the session.")
    print("=" * 60)

    agent = Agent()
    # Kick off the conversation
    response = agent.next("hello")
    print(f"\nAgent: {response['message']}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Session ended]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Agent: Thank you. Goodbye!")
            break

        response = agent.next(user_input)
        print(f"\nAgent: {response['message']}\n")

        # Stop if agent has reached a terminal state
        from core.state_machine import TERMINAL_STATES
        if agent._conv.state in TERMINAL_STATES:
            break


if __name__ == "__main__":
    main()
