"""
agent.py - Guardrail-gated Gemini conversational agent.

Imports check_input() from guardrail.py unchanged and wraps it with a Gemini
LLM call.  Every invocation (blocked or allowed) appends to a shared
trace_log list that run_chat.py can read and print.

Uses the current google-genai SDK (google.genai), not the deprecated
google.generativeai package.
"""

import asyncio
import os
import time
import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import google.genai as genai
from dotenv import load_dotenv

from guardrail import GuardrailResult, check_input

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

GEMINI_MODEL         = "gemini-3.6-flash"       # primary
FALLBACK_MODEL       = "gemini-3.6-flash-lite"  # used if primary fails after retries

# Retry policy: retry on 503 up to MAX_RETRIES times before falling back.
# Delay doubles each attempt: BACKOFF_BASE_S, 2x, 4x, ...
MAX_RETRIES:    int   = 3
BACKOFF_BASE_S: float = 1.0

_PLACEHOLDER = "your_key_here"


def _load_gemini_key() -> str:
    """
    Read GEMINI_API_KEY from the environment.  Raises a clear ValueError if
    the key is missing or still set to the placeholder value.
    """
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key == _PLACEHOLDER:
        raise ValueError(
            "GEMINI_API_KEY is not configured.\n"
            "  1. Open my_project/.env\n"
            "  2. Replace 'your_key_here' with your real key from "
            "https://aistudio.google.com/apikey\n"
            "  3. Re-run the script."
        )
    return key


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _http_status(exc: Exception) -> int:
    """Return the HTTP status code from a google.genai APIError, or 0."""
    # google.genai.errors.APIError (and its subclasses ClientError / ServerError)
    # all carry a plain int .code attribute set in __init__.
    return int(exc.code) if hasattr(exc, "code") else 0


async def _call_chat(client: genai.Client, model: str, prompt: str) -> str:
    """Create an AsyncChat session and send one message. Returns response text."""
    chat = client.aio.chats.create(model=model)
    resp = await chat.send_message(prompt)
    return resp.text


# ---------------------------------------------------------------------------
# Shared trace log (imported directly by run_chat.py)
# ---------------------------------------------------------------------------

trace_log: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Agent result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Full outcome of a single run_agent() call."""

    # --- guardrail fields (always populated) ---
    blocked: bool
    guardrail_score: float
    guardrail_category: Optional[str]
    guardrail_latency_ms: float
    matched_pattern: Optional[str]

    # --- response (always populated) ---
    response: str           # refusal message OR LLM answer

    # --- LLM fields (None when blocked) ---
    llm_latency_ms: Optional[float]


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------

async def run_agent(user_prompt: str) -> AgentResult:
    """
    1. Run the semantic guardrail (check_input).
    2. If blocked  -> return a refusal; do NOT call the LLM.
    3. If allowed  -> call Gemini and return its response.
    4. Append one record to trace_log regardless of outcome.
    """
    # -- Step 1: guardrail --
    gr: GuardrailResult = await check_input(user_prompt)

    llm_latency_ms: Optional[float] = None
    response: str

    if gr.blocked:
        # -- Step 2: blocked path --
        response = (
            f"Request blocked by guardrail -- "
            f"matched category: {gr.category}, "
            f"score: {gr.score:.4f}"
        )
    else:
        # -- Step 3: allowed path — try primary with retry, fall back if needed --
        api_key = _load_gemini_key()   # raises ValueError if not configured
        client = genai.Client(api_key=api_key)

        model_used = GEMINI_MODEL
        retries = 0
        t0 = time.monotonic()

        # Try the primary model, retrying on transient 503s.
        primary_succeeded = False
        last_primary_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await _call_chat(client, GEMINI_MODEL, user_prompt)
                model_used = GEMINI_MODEL
                retries = attempt
                primary_succeeded = True
                break
            except Exception as exc:
                code = _http_status(exc)
                if code == 503 and attempt < MAX_RETRIES:
                    delay = BACKOFF_BASE_S * (2 ** attempt)
                    print(
                        f"[agent] Primary model 503 (attempt {attempt + 1}/{MAX_RETRIES + 1}); "
                        f"retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    retries = attempt + 1
                    continue
                # 404, 429, or exhausted 503 retries -> try fallback
                last_primary_exc = exc
                break

        if not primary_succeeded:
            print(
                f"[agent] Primary model failed after {retries} retr{'y' if retries == 1 else 'ies'} "
                f"({last_primary_exc}); trying fallback {FALLBACK_MODEL!r}..."
            )
            try:
                response = await _call_chat(client, FALLBACK_MODEL, user_prompt)
                model_used = FALLBACK_MODEL
            except Exception as fallback_exc:
                # Make the failure impossible to miss before re-raising.
                print(
                    f"[agent] Fallback model {FALLBACK_MODEL!r} also failed: "
                    f"{type(fallback_exc).__name__}: {fallback_exc}"
                )
                raise

        llm_latency_ms = (time.monotonic() - t0) * 1000

    # -- Step 4: trace --
    trace_log.append({
        "timestamp":       datetime.datetime.now().isoformat(timespec="milliseconds"),
        "prompt":          user_prompt,
        "blocked":         gr.blocked,
        "guardrail_score": round(gr.score, 4),
        "category":        gr.category or "-",
        "guardrail_ms":    round(gr.latency_ms, 1),
        "llm_ms":          round(llm_latency_ms, 1) if llm_latency_ms is not None else "-",
        "model_used":      model_used if not gr.blocked else "-",
        "retries":         retries    if not gr.blocked else "-",
    })

    return AgentResult(
        blocked=gr.blocked,
        guardrail_score=gr.score,
        guardrail_category=gr.category,
        guardrail_latency_ms=gr.latency_ms,
        matched_pattern=gr.matched_pattern,
        response=response,
        llm_latency_ms=llm_latency_ms,
    )
