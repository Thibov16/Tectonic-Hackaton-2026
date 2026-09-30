"""SQLite store for trust-aware documents, claims and links."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    doc_id TEXT,
    version TEXT,
    status TEXT,
    country TEXT,
    customer TEXT,
    effective_date TEXT,
    created_date TEXT,
    last_modified TEXT,
    author TEXT,
    owner TEXT,
    approved_by TEXT,
    approval_date TEXT,
    next_review TEXT,
    source_location TEXT,
    raw_text TEXT NOT NULL,
    metadata_confidence REAL NOT NULL DEFAULT 0.5,
    retracted INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    unit TEXT,
    scope_country TEXT,
    scope_customer TEXT,
    valid_from TEXT,
    valid_to TEXT,
    fragment TEXT NOT NULL,
    extractor TEXT NOT NULL DEFAULT 'rule',
    confidence REAL NOT NULL DEFAULT 0.7
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_doc INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    to_doc INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    UNIQUE(from_doc, to_doc, relation)
);

CREATE TABLE IF NOT EXISTS experts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT,
    topics TEXT NOT NULL DEFAULT '[]',
    countries TEXT NOT NULL DEFAULT '[]',
    last_active TEXT,
    available INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docs_doc_id ON documents(doc_id);
CREATE INDEX IF NOT EXISTS idx_docs_customer ON documents(customer);
CREATE INDEX IF NOT EXISTS idx_docs_country ON documents(country);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def wipe(conn: sqlite3.Connection) -> None:
    for table in ("chunks", "links", "claims", "experts", "documents"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def upsert_document(conn: sqlite3.Connection, doc: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO documents (
            path, doc_type, title, doc_id, version, status, country, customer,
            effective_date, created_date, last_modified, author, owner,
            approved_by, approval_date, next_review, source_location, raw_text,
            metadata_confidence, retracted, metadata_json
        ) VALUES (
            :path, :doc_type, :title, :doc_id, :version, :status, :country, :customer,
            :effective_date, :created_date, :last_modified, :author, :owner,
            :approved_by, :approval_date, :next_review, :source_location, :raw_text,
            :metadata_confidence, :retracted, :metadata_json
        )
        ON CONFLICT(path) DO UPDATE SET
            doc_type=excluded.doc_type,
            title=excluded.title,
            doc_id=excluded.doc_id,
            version=excluded.version,
            status=excluded.status,
            country=excluded.country,
            customer=excluded.customer,
            effective_date=excluded.effective_date,
            created_date=excluded.created_date,
            last_modified=excluded.last_modified,
            author=excluded.author,
            owner=excluded.owner,
            approved_by=excluded.approved_by,
            approval_date=excluded.approval_date,
            next_review=excluded.next_review,
            source_location=excluded.source_location,
            raw_text=excluded.raw_text,
            metadata_confidence=excluded.metadata_confidence,
            retracted=excluded.retracted,
            metadata_json=excluded.metadata_json
        """,
        {
            **doc,
            "retracted": 1 if doc.get("retracted") else 0,
            "metadata_json": json.dumps(doc.get("metadata") or {}, default=_json_default),
        },
    )
    conn.commit()
    row = conn.execute("SELECT id FROM documents WHERE path = ?", (doc["path"],)).fetchone()
    return int(row["id"])


def insert_claim(conn: sqlite3.Connection, claim: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO claims (
            document_id, subject, value_num, value_text, unit, scope_country,
            scope_customer, valid_from, valid_to, fragment, extractor, confidence
        ) VALUES (
            :document_id, :subject, :value_num, :value_text, :unit, :scope_country,
            :scope_customer, :valid_from, :valid_to, :fragment, :extractor, :confidence
        )
        """,
        claim,
    )
    conn.commit()
    return int(cur.lastrowid)


def clear_claims_for_doc(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("DELETE FROM claims WHERE document_id = ?", (document_id,))
    conn.commit()


def add_link(conn: sqlite3.Connection, from_doc: int, to_doc: int, relation: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO links (from_doc, to_doc, relation)
        VALUES (?, ?, ?)
        """,
        (from_doc, to_doc, relation),
    )
    conn.commit()


def set_retracted(conn: sqlite3.Connection, document_id: int, retracted: bool = True) -> None:
    conn.execute(
        "UPDATE documents SET retracted = ? WHERE id = ?",
        (1 if retracted else 0, document_id),
    )
    conn.commit()


def upsert_expert(conn: sqlite3.Connection, expert: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO experts (name, role, topics, countries, last_active, available, notes)
        VALUES (:name, :role, :topics, :countries, :last_active, :available, :notes)
        ON CONFLICT(name) DO UPDATE SET
            role=excluded.role,
            topics=excluded.topics,
            countries=excluded.countries,
            last_active=excluded.last_active,
            available=excluded.available,
            notes=excluded.notes
        """,
        {
            "name": expert["name"],
            "role": expert.get("role"),
            "topics": json.dumps(expert.get("topics") or []),
            "countries": json.dumps(expert.get("countries") or []),
            "last_active": expert.get("last_active"),
            "available": 1 if expert.get("available", True) else 0,
            "notes": expert.get("notes"),
        },
    )
    conn.commit()
    row = conn.execute("SELECT id FROM experts WHERE name = ?", (expert["name"],)).fetchone()
    return int(row["id"])


def replace_chunks(conn: sqlite3.Connection, document_id: int, chunks: list[str]) -> None:
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    for i, text in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (document_id, ordinal, text) VALUES (?, ?, ?)",
            (document_id, i, text),
        )
    conn.commit()


def list_documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM documents ORDER BY path").fetchall()
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def get_document(conn: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def get_document_by_path(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM documents WHERE path = ?", (path,)).fetchone())


def list_claims(conn: sqlite3.Connection, document_id: int | None = None) -> list[dict[str, Any]]:
    if document_id is None:
        rows = conn.execute("SELECT * FROM claims").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM claims WHERE document_id = ?", (document_id,)
        ).fetchall()
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def list_links(conn: sqlite3.Connection, document_id: int | None = None) -> list[dict[str, Any]]:
    if document_id is None:
        rows = conn.execute("SELECT * FROM links").fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM links
            WHERE from_doc = ? OR to_doc = ?
            """,
            (document_id, document_id),
        ).fetchall()
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def list_experts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM experts ORDER BY name").fetchall()
    out = []
    for row in rows:
        item = row_to_dict(row)
        assert item is not None
        item["topics"] = json.loads(item.get("topics") or "[]")
        item["countries"] = json.loads(item.get("countries") or "[]")
        item["available"] = bool(item.get("available"))
        out.append(item)
    return out


def list_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*, d.path, d.title, d.doc_type, d.status, d.country, d.customer, d.retracted
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        ORDER BY c.document_id, c.ordinal
        """
    ).fetchall()
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]
