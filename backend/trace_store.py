"""
trace_store.py - SQLite-backed trace store for guardrail and evaluator events.

Replaces the in-memory trace log with a persistent SQLite database (trace.db).
Stores events in a single 'traces' table with columns:
  id, timestamp, event_type, input_summary, blocked_or_verdict, score, latency_ms, mode
"""

import datetime
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

DB_PATH = Path(__file__).resolve().parent / "trace.db"


def get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with dict-like row access."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the traces table and ensure the 'mode' column exists."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    blocked_or_verdict TEXT NOT NULL,
                    score REAL NOT NULL,
                    latency_ms REAL NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'realtime'
                );
                """
            )
            # Run lightweight migration if existing DB was created without 'mode'
            cursor = conn.execute("PRAGMA table_info(traces)")
            columns = [col["name"] for col in cursor.fetchall()]
            if "mode" not in columns:
                conn.execute(
                    "ALTER TABLE traces ADD COLUMN mode TEXT NOT NULL DEFAULT 'realtime'"
                )
    finally:
        conn.close()


def add_trace(
    event_type: str,
    input_summary: str,
    blocked_or_verdict: Union[str, bool],
    score: float,
    latency_ms: float,
    mode: str = "realtime",
    timestamp: Optional[str] = None,
) -> int:
    """
    Insert a trace record into SQLite.
    
    Normalizes boolean blocked_or_verdict:
      True  -> 'blocked'
      False -> 'allowed'
    """
    if isinstance(blocked_or_verdict, bool):
        verdict_str = "blocked" if blocked_or_verdict else "allowed"
    else:
        verdict_str = str(blocked_or_verdict)

    if not timestamp:
        timestamp = datetime.datetime.now().isoformat(timespec="milliseconds")

    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO traces (
                    timestamp,
                    event_type,
                    input_summary,
                    blocked_or_verdict,
                    score,
                    latency_ms,
                    mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    event_type,
                    input_summary,
                    verdict_str,
                    float(score),
                    float(latency_ms),
                    mode,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_recent_traces(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent traces, newest first."""
    init_db()
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            SELECT id, timestamp, event_type, input_summary, blocked_or_verdict, score, latency_ms, mode
            FROM traces
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_trace_stats() -> Dict[str, Any]:
    """
    Compute summary metrics from the traces table:
      - total_checks: total number of records in traces
      - block_rate: percentage of guardrail checks that were blocked (0.0 - 100.0)
      - average_latency: overall average latency_ms across all traces
      - guardrail_average_latency_ms: average latency for realtime guardrail checks
      - eval_average_latency_ms: average latency for background context evaluations
      - average_groundedness: average score for context_eval events
    """
    init_db()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Total traces / checks
        cursor.execute("SELECT COUNT(*) FROM traces")
        total_checks = cursor.fetchone()[0]

        # Guardrail checks count
        cursor.execute(
            "SELECT COUNT(*) FROM traces WHERE event_type = 'guardrail_check'"
        )
        guardrail_count = cursor.fetchone()[0]

        # Blocked guardrail checks
        cursor.execute(
            """
            SELECT COUNT(*) FROM traces
            WHERE event_type = 'guardrail_check'
              AND blocked_or_verdict IN ('blocked', 'True', 'true', '1')
            """
        )
        blocked_count = cursor.fetchone()[0]

        block_rate = (
            round((blocked_count / guardrail_count) * 100.0, 2)
            if guardrail_count > 0
            else 0.0
        )

        # Realtime guardrail checks average latency
        cursor.execute(
            "SELECT AVG(latency_ms) FROM traces WHERE event_type = 'guardrail_check' OR mode = 'realtime'"
        )
        guardrail_avg_row = cursor.fetchone()[0]
        guardrail_average_latency = (
            round(float(guardrail_avg_row), 2)
            if guardrail_avg_row is not None
            else 0.0
        )

        # Background context eval average latency
        cursor.execute(
            "SELECT AVG(latency_ms) FROM traces WHERE event_type = 'context_eval' OR mode = 'background'"
        )
        eval_avg_row = cursor.fetchone()[0]
        eval_average_latency = (
            round(float(eval_avg_row), 2)
            if eval_avg_row is not None
            else 0.0
        )

        # Average groundedness score across context_eval events
        cursor.execute(
            "SELECT AVG(score) FROM traces WHERE event_type = 'context_eval'"
        )
        avg_groundedness_row = cursor.fetchone()[0]
        average_groundedness = (
            round(float(avg_groundedness_row), 4)
            if avg_groundedness_row is not None
            else 0.0
        )

        return {
            "total_checks": total_checks,
            "block_rate": block_rate,
            "guardrail_average_latency_ms": guardrail_average_latency,
            "eval_average_latency_ms": eval_average_latency,
            "average_groundedness": average_groundedness,
        }
    finally:
        conn.close()


# Ensure DB table exists on module load
init_db()
