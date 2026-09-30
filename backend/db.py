"""SQLite helpers for classified client documents."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "knowledge.db"
DEFAULT_DATA_DIR = PROJECT_ROOT / "testset"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '[]',
    industry TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id),
    client_status TEXT NOT NULL DEFAULT 'resolved',
    source_type TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    entities TEXT NOT NULL DEFAULT '{}',
    source_date TEXT,
    doc_status TEXT,
    classified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_client_id ON documents(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS idx_documents_source_date ON documents(source_date);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Lightweight migrations for existing knowledge.db files
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "source_date" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN source_date TEXT")
    if "doc_status" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN doc_status TEXT")
    conn.commit()


def _loads_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _loads_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def row_to_client(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "aliases": _loads_list(row["aliases"]),
        "industry": row["industry"],
    }


def row_to_document(row: sqlite3.Row, *, include_body: bool = True) -> dict[str, Any]:
    keys = set(row.keys())
    doc = {
        "id": row["id"],
        "client_id": row["client_id"],
        "client_name": row["client_name"] if "client_name" in keys else None,
        "client_status": row["client_status"],
        "source_type": row["source_type"],
        "file_path": row["file_path"],
        "title": row["title"],
        "summary": row["summary"],
        "tags": _loads_list(row["tags"]),
        "entities": _loads_dict(row["entities"]),
        "source_date": row["source_date"] if "source_date" in keys else None,
        "doc_status": row["doc_status"] if "doc_status" in keys else None,
        "classified_at": row["classified_at"],
    }
    if include_body:
        doc["body"] = row["body"]
    return doc


def find_client_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    needle = name.casefold().strip()
    if not needle:
        return None
    rows = conn.execute("SELECT * FROM clients").fetchall()
    for row in rows:
        client = row_to_client(row)
        candidates = [client["name"], *client["aliases"]]
        if any(candidate.casefold().strip() == needle for candidate in candidates):
            return client
        # Partial containment: "Acme" matches "Acme Manufacturing NV"
        if any(
            needle in candidate.casefold() or candidate.casefold() in needle
            for candidate in candidates
            if len(needle) >= 3 and len(candidate) >= 3
        ):
            return client
    return None


def upsert_client(
    conn: sqlite3.Connection,
    name: str,
    *,
    aliases: list[str] | None = None,
    industry: str | None = None,
) -> dict[str, Any]:
    existing = find_client_by_name(conn, name)
    alias_set = {a.strip() for a in (aliases or []) if a and a.strip()}
    if existing:
        merged = {*(existing["aliases"] or []), *alias_set}
        # Keep canonical name; merge aliases and fill industry if missing
        conn.execute(
            """
            UPDATE clients
            SET aliases = ?, industry = COALESCE(industry, ?)
            WHERE id = ?
            """,
            (json.dumps(sorted(merged, key=str.casefold)), industry, existing["id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (existing["id"],)).fetchone()
        return row_to_client(row)

    conn.execute(
        "INSERT INTO clients (name, aliases, industry) VALUES (?, ?, ?)",
        (name.strip(), json.dumps(sorted(alias_set, key=str.casefold)), industry),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM clients WHERE name = ?", (name.strip(),)).fetchone()
    return row_to_client(row)


def upsert_document(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    source_type: str,
    title: str,
    body: str,
    summary: str,
    tags: list[str],
    entities: dict[str, Any],
    classified_at: str,
    client_id: int | None,
    client_status: str,
    source_date: str | None = None,
    doc_status: str | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO documents (
            client_id, client_status, source_type, file_path, title, body,
            summary, tags, entities, source_date, doc_status, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            client_id = excluded.client_id,
            client_status = excluded.client_status,
            source_type = excluded.source_type,
            title = excluded.title,
            body = excluded.body,
            summary = excluded.summary,
            tags = excluded.tags,
            entities = excluded.entities,
            source_date = excluded.source_date,
            doc_status = excluded.doc_status,
            classified_at = excluded.classified_at
        """,
        (
            client_id,
            client_status,
            source_type,
            file_path,
            title,
            body,
            summary,
            json.dumps(tags),
            json.dumps(entities),
            source_date,
            doc_status,
            classified_at,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM documents WHERE file_path = ?", (file_path,)).fetchone()
    return int(row["id"])


def list_clients(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM clients ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_client(row) for row in rows]


def get_client(conn: sqlite3.Connection, client_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return row_to_client(row) if row else None


def _document_select_sql() -> str:
    return """
        SELECT d.*, c.name AS client_name
        FROM documents d
        LEFT JOIN clients c ON c.id = d.client_id
    """


def list_documents_for_client(
    conn: sqlite3.Connection,
    client_id: int,
    *,
    tag: str | None = None,
    include_body: bool = True,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        _document_select_sql() + " WHERE d.client_id = ? ORDER BY d.classified_at DESC",
        (client_id,),
    ).fetchall()
    docs = [row_to_document(row, include_body=include_body) for row in rows]
    if tag:
        needle = tag.casefold()
        docs = [doc for doc in docs if any(t.casefold() == needle for t in doc["tags"])]
    return docs


def search_documents(
    conn: sqlite3.Connection,
    query: str,
    *,
    client: str | None = None,
    tag: str | None = None,
    include_body: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = _document_select_sql() + " WHERE 1=1"
    params: list[Any] = []

    if client:
        matched = find_client_by_name(conn, client)
        if matched:
            sql += " AND d.client_id = ?"
            params.append(matched["id"])
        else:
            sql += " AND (c.name LIKE ? OR d.body LIKE ? OR d.summary LIKE ?)"
            like = f"%{client}%"
            params.extend([like, like, like])

    if query.strip():
        like = f"%{query.strip()}%"
        sql += " AND (d.title LIKE ? OR d.summary LIKE ? OR d.body LIKE ? OR d.tags LIKE ?)"
        params.extend([like, like, like, like])

    sql += " ORDER BY d.classified_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    docs = [row_to_document(row, include_body=include_body) for row in rows]
    if tag:
        needle = tag.casefold()
        docs = [doc for doc in docs if any(t.casefold() == needle for t in doc["tags"])]
    return docs


def search_documents_by_terms(
    conn: sqlite3.Connection,
    terms: list[str],
    *,
    client_id: int | None = None,
    include_body: bool = True,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Find documents matching any of the search terms (OR), optionally scoped to a client."""
    cleaned = [term.strip() for term in terms if term and len(term.strip()) >= 2]
    if not cleaned and client_id is None:
        return []

    sql = _document_select_sql() + " WHERE 1=1"
    params: list[Any] = []

    if client_id is not None:
        # Client-linked docs plus keyword hits from the wider corpus
        sql += " AND (d.client_id = ?"
        params.append(client_id)
        if cleaned:
            clauses = []
            for term in cleaned:
                like = f"%{term}%"
                clauses.append(
                    "(d.title LIKE ? OR d.summary LIKE ? OR d.body LIKE ? OR d.tags LIKE ?)"
                )
                params.extend([like, like, like, like])
            sql += " OR (" + " OR ".join(clauses) + ")"
        sql += ")"
    elif cleaned:
        clauses = []
        for term in cleaned:
            like = f"%{term}%"
            clauses.append(
                "(d.title LIKE ? OR d.summary LIKE ? OR d.body LIKE ? OR d.tags LIKE ?)"
            )
            params.extend([like, like, like, like])
        sql += " AND (" + " OR ".join(clauses) + ")"
    else:
        return []

    sql += " ORDER BY d.classified_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [row_to_document(row, include_body=include_body) for row in rows]


def list_unresolved_documents(
    conn: sqlite3.Connection, *, include_body: bool = False
) -> list[dict[str, Any]]:
    rows = conn.execute(
        _document_select_sql()
        + " WHERE d.client_id IS NULL OR d.client_status = 'unresolved'"
        + " ORDER BY d.classified_at DESC"
    ).fetchall()
    return [row_to_document(row, include_body=include_body) for row in rows]
