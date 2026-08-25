#!/usr/bin/env python3
"""Synthetic AI workspace persistence checks. Uses no real course material or OpenAI calls."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "StudyLibrary")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["OPENAI_VECTOR_STORE_ID"] = ""
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_ai_workspace_test", server_path)
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    server.DEFAULT_STUDY_ROOT = tmp / "StudyLibrary"
    return server


def handler(server, body: dict | None = None):
    h = object.__new__(server.StudyHubHandler)
    h.parse_body_json = lambda limit=server.MAX_JSON_BODY_SIZE: body or {}
    sent: dict = {}
    h.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    return h, sent


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        root = server.DEFAULT_STUDY_ROOT
        tutorial_dir = root / "TEST1001 - Synthetic Course" / "Week 04" / "02 Exercises" / "Tutorial"
        tutorial_dir.mkdir(parents=True)
        source = tutorial_dir / "Tutorial_04.txt"
        source.write_text("Q1. The synthetic decay model is dN/dt = -kN. Explain the modelling idea.\n", encoding="utf-8")
        server.scan_library(root)

        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        file_row = conn.execute("SELECT * FROM files WHERE filename='Tutorial_04.txt'").fetchone()
        conn.close()

        ask_handler, ask_sent = handler(
            server,
            {
                "context": {"fileId": file_row["id"]},
                "scope": "file",
                "prompt": "Explain this tutorial in markdown with $dN/dt=-kN$.",
            },
        )
        server.StudyHubHandler.handle_ask(ask_handler)
        ask = ask_sent["data"]
        conversation_id = ask["conversationId"]

        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        get_data = server.StudyHubHandler.handle_conversation_get(
            object.__new__(server.StudyHubHandler),
            conn,
            {"id": [str(conversation_id)]},
        )
        list_data = server.StudyHubHandler.handle_conversations_get(
            object.__new__(server.StudyHubHandler),
            conn,
            {"q": ["decay"]},
        )
        conn.close()

        rename_handler, rename_sent = handler(server, {"action": "rename", "id": conversation_id, "title": "Synthetic decay chat"})
        server.StudyHubHandler.handle_conversation_mutation(rename_handler)
        duplicate_handler, duplicate_sent = handler(server, {"action": "duplicate", "id": conversation_id})
        server.StudyHubHandler.handle_conversation_mutation(duplicate_handler)
        delete_handler, delete_sent = handler(server, {"action": "delete", "id": conversation_id})
        server.StudyHubHandler.handle_conversation_mutation(delete_handler)
        clear_handler, clear_sent = handler(server, {"action": "clear"})
        server.StudyHubHandler.handle_conversation_mutation(clear_handler)

        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        deleted = conn.execute("SELECT deleted_at FROM ai_conversations WHERE id=?", (conversation_id,)).fetchone()
        copied_messages = conn.execute(
            "SELECT COUNT(*) AS c FROM ai_messages WHERE conversation_id=?",
            (duplicate_sent["data"]["conversation"]["id"],),
        ).fetchone()["c"]
        active_conversations = conn.execute("SELECT COUNT(*) AS c FROM ai_conversations WHERE deleted_at IS NULL").fetchone()["c"]
        conn.close()

        serialized = json.dumps(get_data, ensure_ascii=False)
        checks = {
            "conversation_created_from_ask": ask["status"] == "local" and conversation_id > 0,
            "messages_persisted": len(get_data["messages"]) == 2,
            "context_preserved": get_data["conversation"]["file_id"] == file_row["id"] and get_data["conversation"]["scope"] == "file",
            "sources_sanitized_for_history": "text_cache_path" not in serialized and "provider_file_id" not in serialized,
            "conversation_search": len(list_data) == 1 and list_data[0]["id"] == conversation_id,
            "rename_conversation": rename_sent["data"]["conversation"]["title"] == "Synthetic decay chat",
            "duplicate_conversation": duplicate_sent["data"]["conversation"]["id"] != conversation_id and copied_messages == 2,
            "delete_conversation": bool(deleted["deleted_at"]),
            "clear_conversations": clear_sent["data"]["ok"] is True and active_conversations == 0,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
