"""
evaluator.py - LLM response groundedness checker using the Moss SDK.

For each check_groundedness() call, creates an ephemeral Moss index named
'context-eval-{uuid}' containing the caller's context doc PLUS a small set
of fixed calibration docs from unrelated domains.

Why calibration docs?
---------------------
Moss scores are relative rankings.  With only one doc in an index, Moss
always returns score=1.0 (the single doc is always the best match).
Adding calibration docs from unrelated domains gives Moss a meaningful
ranking surface:
  - A response that closely matches the context -> context ranks #1, high score
  - A response that contradicts / invents facts -> context scores noticeably
    lower because it no longer dominates over unrelated calibration docs

The groundedness score is the raw similarity score assigned to the context
doc when the response is used as the search query.
"""

import os
import time
from dataclasses import dataclass
from uuid import uuid4

from dotenv import load_dotenv
from moss import DocumentInfo, MossClient, QueryOptions

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

EVAL_INDEX_PREFIX = "context-eval"

# Scores >= this threshold are labelled "grounded".
# Range [0, 1].  Raise to tighten, lower to loosen.
GROUNDEDNESS_THRESHOLD: float = 0.6

# Hybrid search balance (0 = keyword-only, 1 = semantic-only).
EVAL_ALPHA: float = 0.5

# Fixed calibration docs — completely unrelated to any expected context.
# These pad the index so Moss produces meaningful relative scores rather
# than always returning 1.0 for the single-doc case.
_CALIBRATION_DOCS = [
    DocumentInfo(
        id="cal-0",
        text="The stock market closed sharply higher yesterday as tech stocks led broad gains.",
    ),
    DocumentInfo(
        id="cal-1",
        text="Scientists discovered a new species of deep-sea beetle near hydrothermal vents.",
    ),
    DocumentInfo(
        id="cal-2",
        text="The recipe calls for two cups of flour, one egg, and a teaspoon of vanilla.",
    ),
    DocumentInfo(
        id="cal-3",
        text="The local football team secured the championship with a last-minute penalty goal.",
    ),
]

# Total docs per ephemeral index = 1 context + len(_CALIBRATION_DOCS)
_TOTAL_DOCS = 1 + len(_CALIBRATION_DOCS)

# Stable ID for the caller's context doc (used to find it in results by ID).
_CONTEXT_DOC_ID = "context"


# ---------------------------------------------------------------------------
# Moss client singleton
# ---------------------------------------------------------------------------

_eval_client: MossClient | None = None


def _get_eval_client() -> MossClient:
    global _eval_client
    if _eval_client is None:
        project_id = os.getenv("MOSS_PROJECT_ID")
        project_key = os.getenv("MOSS_PROJECT_KEY")
        if not project_id or not project_key:
            raise EnvironmentError(
                "MOSS_PROJECT_ID and MOSS_PROJECT_KEY must be set "
                "(check your .env file)."
            )
        _eval_client = MossClient(project_id, project_key)
    return _eval_client


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Outcome of a single check_groundedness() call."""

    groundedness_score: float   # similarity of response vs context in [0, 1]
    verdict: str                # "grounded" or "ungrounded"
    latency_ms: float           # wall-clock time of the full Moss round-trip


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

async def setup_eval_index() -> None:
    """
    Validate Moss credentials at server startup.

    The 'context-eval' index is ephemeral — a fresh one is created per
    check_groundedness() call and deleted immediately after.
    """
    _get_eval_client()  # raises EnvironmentError if credentials are missing
    print(
        f"[eval] Evaluator ready "
        f"(ephemeral '{EVAL_INDEX_PREFIX}-<uuid>' indexes, "
        f"{_TOTAL_DOCS} docs each).\n"
    )


# ---------------------------------------------------------------------------
# check_groundedness
# ---------------------------------------------------------------------------

async def check_groundedness(response: str, context: str) -> EvalResult:
    """
    Measure how well *response* is grounded in *context* using Moss
    semantic similarity.

    Strategy
    --------
    1. Build an ephemeral index 'context-eval-{uuid}' with:
         - the caller's context (id='context')
         - {len(_CALIBRATION_DOCS)} fixed calibration docs from unrelated domains
    2. Load the index locally.
    3. Query with the *response* text, returning top-{_TOTAL_DOCS} results.
    4. Look up the context doc's score by id='context'.
       - High score  -> response discusses content found in the context  -> grounded
       - Low score   -> response discusses content absent from context   -> ungrounded
    5. Delete the ephemeral index.

    Returns
    -------
    EvalResult with:
      groundedness_score : float in [0, 1]
      verdict            : "grounded" (>= GROUNDEDNESS_THRESHOLD) or "ungrounded"
      latency_ms         : wall-clock time of the full Moss round-trip
    """
    client = _get_eval_client()
    index_name = f"{EVAL_INDEX_PREFIX}-{uuid4().hex[:12]}"

    context_doc = DocumentInfo(id=_CONTEXT_DOC_ID, text=context)
    all_docs = [context_doc] + _CALIBRATION_DOCS

    t0 = time.monotonic()
    score = 0.0

    try:
        # 1. Build the ephemeral index.
        await client.create_index(index_name, all_docs)

        # 2. Load locally.
        await client.load_index(index_name)

        # 3. Query: how does the response rank against the context + calibration docs?
        results = await client.query(
            index_name,
            response,
            QueryOptions(top_k=_TOTAL_DOCS, alpha=EVAL_ALPHA),
        )

        # 4. Find the context doc's score by its stable ID.
        for doc in results.docs:
            if doc.id == _CONTEXT_DOC_ID:
                score = doc.score
                break

    finally:
        # 5. Clean up regardless of success or failure.
        try:
            await client.delete_index(index_name)
        except Exception:
            pass  # best-effort; stale ephemeral indexes don't affect correctness

    latency_ms = (time.monotonic() - t0) * 1000  # wall-clock; most honest E2E measure
    verdict = "grounded" if score >= GROUNDEDNESS_THRESHOLD else "ungrounded"

    return EvalResult(
        groundedness_score=round(score, 4),
        verdict=verdict,
        latency_ms=round(latency_ms, 1),
    )
