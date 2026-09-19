"""
main.py - FastAPI backend: guardrail + context evaluator with SQLite trace store.

Run with:
    uvicorn main:app --reload

Endpoints:
    POST /guardrail/check    {"prompt": "..."}                     -> guardrail verdict
    POST /evaluate/context   {"response": "...", "context": "..."} -> groundedness
    GET  /trace/log                                                -> last 50 traces (newest first)
    GET  /trace/stats                                              -> summary stats from SQLite
    GET  /health                                                   -> {"status": "ok"}
"""

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from guardrail import GuardrailResult, setup_index, check_input
from evaluator import EvalResult, setup_eval_index, check_groundedness
from trace_store import init_db, add_trace, get_recent_traces, get_trace_stats


# ---------------------------------------------------------------------------
# Lifespan: warm up the Moss index once at startup and init SQLite
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and load Moss indexes before the first request arrives."""
    init_db()
    await setup_index()
    await setup_eval_index()
    print()
    print("=" * 60)
    print("  Guardrail + Evaluator + Trace API is ready.")
    print("  POST http://localhost:8000/guardrail/check")
    print("  POST http://localhost:8000/evaluate/context")
    print("  GET  http://localhost:8000/trace/log")
    print("  GET  http://localhost:8000/trace/stats")
    print("  GET  http://localhost:8000/health")
    print("  Docs http://localhost:8000/docs")
    print("=" * 60)
    print()
    yield
    # (nothing to clean up on shutdown)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Guardrail & Evaluation API",
    description="Semantic prompt-injection/jailbreak detector and context evaluator powered by Moss with SQLite tracing.",
    version="0.2.0",
    lifespan=lifespan,
)

# Allow all origins so a frontend on any port can call this freely.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CheckRequest(BaseModel):
    prompt: str


class CheckResponse(BaseModel):
    blocked: bool
    matched_pattern: Optional[str]
    score: float
    category: Optional[str]
    latency_ms: float


class EvalRequest(BaseModel):
    response: str
    context: str


class EvalResponse(BaseModel):
    groundedness_score: float
    verdict: str        # "grounded" | "ungrounded"
    latency_ms: float


class TraceRecord(BaseModel):
    id: int
    timestamp: str
    event_type: str
    input_summary: str
    blocked_or_verdict: str
    score: float
    latency_ms: float
    mode: str = "realtime"


class TraceStatsResponse(BaseModel):
    total_checks: int
    block_rate: float
    guardrail_average_latency_ms: float
    eval_average_latency_ms: float
    average_groundedness: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe - returns 200 once the server is running."""
    return {"status": "ok"}


@app.post("/guardrail/check", response_model=CheckResponse, tags=["guardrail"])
async def guardrail_check(body: CheckRequest):
    """
    Run the semantic guardrail against the supplied prompt.

    Returns the full verdict: whether the prompt is blocked, the closest
    matching pattern, its similarity score, its attack category, and the
    time taken by the Moss query.
    Logs result to SQLite trace.db.
    """
    result: GuardrailResult = await check_input(body.prompt)

    # Persist trace row to SQLite trace.db (mode="realtime")
    add_trace(
        event_type="guardrail_check",
        input_summary=body.prompt,
        blocked_or_verdict=result.blocked,
        score=result.score,
        latency_ms=result.latency_ms,
        mode="realtime",
    )

    return CheckResponse(
        blocked=result.blocked,
        matched_pattern=result.matched_pattern,
        score=result.score,
        category=result.category,
        latency_ms=result.latency_ms,
    )


@app.post("/evaluate/context", response_model=EvalResponse, tags=["evaluate"])
async def evaluate_context(body: EvalRequest):
    """
    Check whether a model response is grounded in the supplied context.

    Embeds both the response and the context via Moss and returns their
    semantic similarity score, a "grounded" / "ungrounded" verdict, and
    the query latency.
    Logs result to SQLite trace.db with mode="background".
    """
    result: EvalResult = await check_groundedness(body.response, body.context)

    # Persist trace row to SQLite trace.db (mode="background")
    summary = f"resp: {body.response[:50]}... | ctx: {body.context[:50]}..."
    add_trace(
        event_type="context_eval",
        input_summary=summary,
        blocked_or_verdict=result.verdict,
        score=result.groundedness_score,
        latency_ms=result.latency_ms,
        mode="background",
    )

    return EvalResponse(
        groundedness_score=result.groundedness_score,
        verdict=result.verdict,
        latency_ms=result.latency_ms,
    )


@app.get("/trace/log", response_model=List[TraceRecord], tags=["traces"])
async def trace_log():
    """Return the last 50 trace records from SQLite trace.db, newest first."""
    return get_recent_traces(limit=50)


@app.get("/trace/stats", response_model=TraceStatsResponse, tags=["traces"])
async def trace_stats():
    """
    Return summary statistics computed from SQLite trace.db:
    - total_checks: total check events recorded
    - block_rate: percentage of guardrail checks that were blocked (0.0 to 100.0)
    - average_latency: average latency in ms across all checks
    - average_groundedness: average score across all context_eval checks
    """
    return get_trace_stats()
