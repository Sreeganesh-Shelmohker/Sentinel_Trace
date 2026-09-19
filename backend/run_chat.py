"""
run_chat.py - Interactive terminal loop: guardrail + Gemini.

Usage:
    python run_chat.py

Type any prompt and press Enter.
Type 'exit' (or Ctrl-C) to quit and print the session trace log.
"""

import asyncio
import sys
from typing import Any, Dict, List

from agent import AgentResult, run_agent, trace_log
from guardrail import setup_index

# ---------------------------------------------------------------------------
# Trace table printer (ASCII-only, safe on any Windows codepage)
# ---------------------------------------------------------------------------

def _print_trace(log: List[Dict[str, Any]]) -> None:
    if not log:
        print("  (no entries)")
        return

    headers = [
        "timestamp", "prompt", "blocked",
        "guardrail_score", "category", "guardrail_ms", "llm_ms",
    ]
    col_widths = {h: len(h) for h in headers}
    for row in log:
        for h in headers:
            val = str(row[h])
            if h == "prompt" and len(val) > 40:
                val = val[:37] + "..."
            col_widths[h] = max(col_widths[h], len(val))

    sep = "+" + "+".join("-" * (col_widths[h] + 2) for h in headers) + "+"
    hdr = "|" + "|".join(f" {h:<{col_widths[h]}} " for h in headers) + "|"
    print(sep)
    print(hdr)
    print(sep)
    for row in log:
        cells = []
        for h in headers:
            val = str(row[h])
            if h == "prompt" and len(val) > 40:
                val = val[:37] + "..."
            cells.append(f" {val:<{col_widths[h]}} ")
        print("|" + "|".join(cells) + "|")
    print(sep)


# ---------------------------------------------------------------------------
# Per-turn printer
# ---------------------------------------------------------------------------

def _print_turn(result: AgentResult) -> None:
    verdict = "[BLOCKED]" if result.blocked else "[ALLOWED]"
    print(f"  Guardrail : {verdict}  "
          f"(score={result.guardrail_score:.4f}, "
          f"guardrail={result.guardrail_latency_ms:.1f} ms)")
    if result.blocked:
        print(f"  Category  : {result.guardrail_category}")
        print(f"  Matched   : {result.matched_pattern!r}")
    if result.llm_latency_ms is not None:
        print(f"  LLM time  : {result.llm_latency_ms:.0f} ms")
    print()
    print(result.response)
    print()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  Guardrail + Gemini Chat")
    print("  Type 'exit' to quit and see the session trace log.")
    print("=" * 60)
    print()

    # Warm up the Moss index once at startup.
    try:
        await setup_index()
    except EnvironmentError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("-" * 60)
    print()

    while True:
        try:
            user_input = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "exit"

        if not user_input:
            continue

        if user_input.lower() == "exit":
            break

        print()
        try:
            result = await run_agent(user_input)
            _print_turn(result)
        except ValueError as exc:
            # Friendly message for missing/placeholder GEMINI_API_KEY
            print(f"[CONFIG ERROR] {exc}")
            print()
        except Exception as exc:
            print(f"[ERROR] Unexpected problem: {exc}")
            print()

    # Session summary
    print()
    print("=" * 60)
    print("  Session Trace Log")
    print("=" * 60)
    print()
    _print_trace(trace_log)
    print()
    print("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
