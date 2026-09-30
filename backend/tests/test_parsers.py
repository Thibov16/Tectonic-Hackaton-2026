from __future__ import annotations

from pathlib import Path

from app.ingest.parsers import parse_file


def test_parse_markdown_policy(data_dir: Path):
    path = data_dir / "policies" / "POL-HR-017_remote_work_allowance_BE_v3.0.md"
    docs = parse_file(path)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["doc_id"] == "POL-HR-017"
    assert doc["version"] == "3.0"
    assert "151" in doc["raw_text"]


def test_parse_eml(data_dir: Path):
    path = data_dir / "emails" / "E03_deadline_correction.eml"
    docs = parse_file(path)
    assert docs[0]["parser"] == "eml"
    assert docs[0].get("subject") or docs[0].get("title")


def test_parse_chat(data_dir: Path):
    chats = list((data_dir / "chats").glob("*.txt"))
    assert chats
    docs = parse_file(chats[0])
    assert docs[0]["parser"] == "chat"
    assert docs[0].get("messages")
