"""Ingest CLI and pipeline orchestration."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from app.config import AppConfig, get_config
from app.ingest import store
from app.ingest.chunking import chunk_text
from app.ingest.corrections import apply_corrections
from app.ingest.metadata import normalise_document
from app.ingest.parsers import parse_file
from app.ingest.versioning import apply_versioning
from app.trust.claims import extract_claims_from_text

logger = logging.getLogger(__name__)

SKIP_DIRS = {"ground_truth", ".git"}
SUPPORTED = {".md", ".eml", ".txt", ".csv"}


def iter_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.casefold() not in SUPPORTED:
            continue
        if path.name.casefold() == "readme.md":
            continue
        if any(part.casefold() in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def ingest(config: AppConfig | None = None, *, rebuild: bool = False) -> dict[str, Any]:
    config = config or get_config()
    data_dir = config.data_dir
    if not data_dir.exists():
        raise FileNotFoundError(f"Dataset not found: {data_dir}")

    conn = store.connect(config.db_path)
    store.init_db(conn)
    if rebuild:
        store.wipe(conn)

    files = iter_files(data_dir)
    stats = {"files": 0, "documents": 0, "experts": 0, "claims": 0}

    for path in files:
        parsed_items = parse_file(path)
        stats["files"] += 1
        for parsed in parsed_items:
            if parsed.get("kind") == "expert":
                if parsed.get("name"):
                    store.upsert_expert(
                        conn,
                        {
                            "name": parsed["name"],
                            "role": parsed.get("role"),
                            "topics": parsed.get("topics") or [],
                            "countries": parsed.get("countries") or [],
                            "last_active": parsed.get("last_active"),
                            "available": parsed.get("available", True),
                            "notes": parsed.get("notes"),
                        },
                    )
                    stats["experts"] += 1
                continue

            # Force path absolute for normalisation relative to data_dir
            parsed = {**parsed, "path": str(path if "#" not in str(parsed.get("path", "")) else parsed["path"])}
            if parsed.get("parser") == "ticket_csv":
                # keep synthetic path with ticket id
                pass
            else:
                parsed["path"] = str(path)

            doc = normalise_document(parsed, data_root=data_dir, config=config)
            if not doc:
                continue
            if parsed.get("parser") == "ticket_csv":
                # Preserve ticket-specific relative path
                try:
                    rel_root = str(path.resolve().relative_to(data_dir.resolve()))
                    suffix = str(parsed["path"]).split("#", 1)[-1]
                    doc["path"] = f"{rel_root}#{suffix}"
                except ValueError:
                    doc["path"] = str(parsed["path"])

            doc_id = store.upsert_document(conn, doc)
            stats["documents"] += 1

            store.clear_claims_for_doc(conn, doc_id)
            claims = extract_claims_from_text(
                doc["raw_text"],
                scope_country=doc.get("country"),
                scope_customer=doc.get("customer"),
                valid_from=doc.get("effective_date"),
            )
            for claim in claims:
                claim["document_id"] = doc_id
                store.insert_claim(conn, claim)
                stats["claims"] += 1

            chunks = chunk_text(doc["raw_text"], config.chunk_size, config.chunk_overlap)
            store.replace_chunks(conn, doc_id, chunks)

    apply_versioning(conn, config.today)
    apply_corrections(conn)

    # Seed well-known experts if directory missing fields
    _seed_fallback_experts(conn)

    conn.close()
    logger.info("Ingest complete: %s", stats)
    return stats


def _seed_fallback_experts(conn: Any) -> None:
    defaults = [
        {
            "name": "Sara Claes",
            "role": "Director Payroll Operations BE",
            "topics": ["payroll_deadline", "remote_allowance"],
            "countries": ["BE"],
            "last_active": "2026-09-01",
            "available": True,
        },
        {
            "name": "Marc Peeters",
            "role": "Senior Payroll Expert",
            "topics": ["company_car"],
            "countries": ["BE"],
            "last_active": "2026-07-02",
            "available": True,
        },
        {
            "name": "Katrin Bauer",
            "role": "HR Ops DE",
            "topics": ["sick_leave"],
            "countries": ["DE"],
            "last_active": "2025-11-21",
            "available": True,
        },
        {
            "name": "Nina Vermeulen",
            "role": "Knowledge Manager",
            "topics": ["company_car", "payroll_deadline"],
            "countries": ["BE"],
            "last_active": "2026-07-02",
            "available": True,
        },
    ]
    existing = {e["name"] for e in store.list_experts(conn)}
    for expert in defaults:
        if expert["name"] not in existing:
            store.upsert_expert(conn, expert)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest SD Worx synthetic testset")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    config = get_config()
    if args.data:
        config.data_dir = args.data
    if args.db:
        config.db_path = args.db
    stats = ingest(config, rebuild=args.rebuild)
    conn = store.connect(config.db_path)
    docs = store.list_documents(conn)
    links = store.list_links(conn)
    print(f"Ingested documents={stats['documents']} experts={stats['experts']} claims={stats['claims']}")
    print(f"{'path':50} {'status':12} {'country':8} {'customer'}")
    for doc in docs:
        print(
            f"{(doc['path'] or '')[:50]:50} {(doc.get('status') or '-'):12} "
            f"{(doc.get('country') or '-'):8} {doc.get('customer') or '-'}"
        )
    print(f"Links: {len(links)}")
    for link in links[:30]:
        print(f"  {link['relation']}: {link['from_doc']} -> {link['to_doc']}")
    conn.close()


if __name__ == "__main__":
    main()
