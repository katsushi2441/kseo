"""SQLiteの入出力。ORMは使わず、必要な問い合わせだけを関数にする。

監査結果は「あとから同じ画面を再現できること」を最優先に保存する。
指摘は集計値ではなく1件ずつ行で持ち、根拠(evidence)も一緒に残す。
スコアだけ保存すると「なぜその点数か」を後から説明できず、
顧客に見せる診断書として成立しないため。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sites_owner ON sites(owner);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    overall INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    categories TEXT NOT NULL DEFAULT '{}',
    counts TEXT NOT NULL DEFAULT '{}',
    pages TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_site ON audits(site_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audits_owner ON audits(owner, created_at DESC);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    url TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT NOT NULL,
    action TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_audit ON findings(audit_id);

CREATE TABLE IF NOT EXISTS advice (
    id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_advice_audit ON advice(audit_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_owner ON usage_events(owner, kind, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- sites -----------------------------------------------------------------


def create_site(owner: str, name: str, url: str) -> dict:
    site_id = new_id()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sites (id, owner, name, url, created_at) VALUES (?,?,?,?,?)",
            (site_id, owner, name, url, now()),
        )
    return get_site(site_id) or {}


def get_site(site_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    return dict(row) if row else None


def list_sites(owner: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sites WHERE owner=? ORDER BY created_at DESC", (owner,)
        ).fetchall()
    return [dict(row) for row in rows]


def count_sites(owner: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sites WHERE owner=?", (owner,)).fetchone()
    return int(row["n"])


def delete_site(site_id: str) -> None:
    with connect() as conn:
        audit_ids = [
            row["id"] for row in conn.execute("SELECT id FROM audits WHERE site_id=?", (site_id,))
        ]
        for audit_id in audit_ids:
            conn.execute("DELETE FROM findings WHERE audit_id=?", (audit_id,))
            conn.execute("DELETE FROM advice WHERE audit_id=?", (audit_id,))
        conn.execute("DELETE FROM audits WHERE site_id=?", (site_id,))
        conn.execute("DELETE FROM sites WHERE id=?", (site_id,))


# --- audits ----------------------------------------------------------------


def start_audit(site_id: str, owner: str) -> str:
    audit_id = new_id()
    with connect() as conn:
        conn.execute(
            "INSERT INTO audits (id, site_id, owner, status, created_at) VALUES (?,?,?,?,?)",
            (audit_id, site_id, owner, "running", now()),
        )
    return audit_id


def finish_audit(
    audit_id: str,
    *,
    score: dict,
    pages: Iterable[dict],
    findings: Iterable[Any],
) -> None:
    page_list = list(pages)
    with connect() as conn:
        conn.execute(
            """UPDATE audits SET status='done', overall=?, page_count=?, categories=?,
                   counts=?, pages=?, finished_at=? WHERE id=?""",
            (
                int(score.get("overall", 0)),
                len(page_list),
                json.dumps(score.get("categories", {}), ensure_ascii=False),
                json.dumps(score.get("counts", {}), ensure_ascii=False),
                json.dumps(page_list, ensure_ascii=False),
                now(),
                audit_id,
            ),
        )
        conn.executemany(
            """INSERT INTO findings (audit_id, rule, category, severity, url, message, evidence, action)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    audit_id,
                    item.rule,
                    item.category,
                    item.severity,
                    item.url,
                    item.message,
                    item.evidence,
                    item.action,
                )
                for item in findings
            ],
        )


def fail_audit(audit_id: str, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE audits SET status='failed', error=?, finished_at=? WHERE id=?",
            (error[:500], now(), audit_id),
        )


def get_audit(audit_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone()
        if not row:
            return None
        audit = dict(row)
        audit["categories"] = json.loads(audit["categories"] or "{}")
        audit["counts"] = json.loads(audit["counts"] or "{}")
        audit["pages"] = json.loads(audit["pages"] or "[]")
        audit["findings"] = [
            dict(item)
            for item in conn.execute(
                """SELECT rule, category, severity, url, message, evidence, action
                   FROM findings WHERE audit_id=?
                   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                            category, rule""",
                (audit_id,),
            )
        ]
    return audit


def list_audits(site_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, status, overall, page_count, counts, created_at, finished_at, error
               FROM audits WHERE site_id=? ORDER BY created_at DESC LIMIT 50""",
            (site_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["counts"] = json.loads(item["counts"] or "{}")
        result.append(item)
    return result


# --- advice ----------------------------------------------------------------


def save_advice(audit_id: str, owner: str, provider: str, model: str, body: str) -> dict:
    advice_id = new_id()
    with connect() as conn:
        conn.execute(
            """INSERT INTO advice (id, audit_id, owner, provider, model, body, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (advice_id, audit_id, owner, provider, model, body, now()),
        )
        row = conn.execute("SELECT * FROM advice WHERE id=?", (advice_id,)).fetchone()
    return dict(row)


def latest_advice(audit_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM advice WHERE audit_id=? ORDER BY created_at DESC LIMIT 1",
            (audit_id,),
        ).fetchone()
    return dict(row) if row else None


# --- usage -----------------------------------------------------------------


def record_usage(owner: str, kind: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO usage_events (owner, kind, created_at) VALUES (?,?,?)",
            (owner, kind, now()),
        )


def usage_this_month(owner: str, kind: str) -> int:
    """今月の利用回数。月初はUTCで切る(表示はJSTだが集計境界は固定でよい)。"""
    prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE owner=? AND kind=? AND created_at LIKE ?",
            (owner, kind, f"{prefix}%"),
        ).fetchone()
    return int(row["n"])
