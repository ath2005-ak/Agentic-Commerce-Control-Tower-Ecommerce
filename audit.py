"""
Audit trail. Every agent decision and every Razorpay call gets written here.
This is what you show the judges when they ask "how do I know this is safe."
"""

import sqlite3
import json
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit_logs.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            session_id TEXT,
            action TEXT NOT NULL,
            reasoning TEXT,
            policy_decision TEXT,
            policy_reason TEXT,
            input_json TEXT,
            output_json TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_event(session_id: str, action: str, reasoning: str, policy_decision: str,
              policy_reason: str, input_data: dict, output_data: dict, status: str):
    conn = get_db()
    conn.execute(
        """INSERT INTO audit_log
           (ts, session_id, action, reasoning, policy_decision, policy_reason, input_json, output_json, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            time.time(), session_id, action, reasoning, policy_decision, policy_reason,
            json.dumps(input_data) if isinstance(input_data, (dict, list)) else str(input_data),
            json.dumps(output_data) if isinstance(output_data, (dict, list)) else str(output_data),
            status,
        ),
    )
    conn.commit()
    conn.close()


def get_trail(session_id: str = None, limit: int = 200):
    conn = get_db()
    if session_id and session_id.strip() and session_id.strip().lower() != "all":
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
            (session_id.strip(), limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary(session_id: str = None) -> dict:
    """Counts by policy_decision, for the Control Tower header stats."""
    conn = get_db()
    if session_id and session_id.strip() and session_id.strip().lower() != "all":
        rows = conn.execute(
            "SELECT policy_decision, COUNT(*) FROM audit_log WHERE session_id = ? GROUP BY policy_decision",
            (session_id.strip(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT policy_decision, COUNT(*) FROM audit_log GROUP BY policy_decision"
        ).fetchall()
    conn.close()
    return {decision: count for decision, count in rows}

