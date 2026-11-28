import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ai


class AIAnswerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "app" / "schema.sql"
        self.conn.executescript(schema.read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT INTO item (id, title, added_at, updated_at) VALUES (?, ?, ?, ?)",
            ("itm_test", "Example paper", "", ""),
        )

    def test_prompt_labels_source_excerpts_with_page(self):
        messages = ai.build_messages(
            "Compare these papers",
            [{"title": "Example paper", "year": 2024, "page": 5, "kind": "page", "text": "Paper text."}],
            1,
        )

        self.assertIn("[1] Example paper", messages[1]["content"])
        self.assertIn("第 5 页", messages[1]["content"])
        self.assertIn("Paper text.", messages[1]["content"])
        self.assertIn("[n]", messages[0]["content"])

    def test_ask_saves_and_returns_citations_without_count_field(self):
        excerpts = [
            {"itemId": "itm_test", "title": "Example paper", "year": 2024,
             "page": 2, "kind": "page", "text": "Paper text."},
            {"itemId": "itm_test", "title": "Example paper", "year": 2024,
             "page": 4, "kind": "page", "text": "More paper text."},
        ]
        with patch.object(ai, "ensure_passages"), \
             patch.object(ai, "rank", return_value=excerpts), \
             patch.object(ai, "chat", return_value=("Second source [2], then first [1], again [2].", "test-model")):
            result = ai.ask(self.conn, "What does this paper discuss?", ["itm_test"])

        self.assertEqual(result["answer"], "Second source [1], then first [2], again [1].")
        self.assertEqual(result["citations"], [{
            "itemId": "itm_test", "title": "Example paper", "page": 4, "kind": "page",
        }, {
            "itemId": "itm_test", "title": "Example paper", "page": 2, "kind": "page",
        }])
        self.assertNotIn("cites", result)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(message)")}
        self.assertIn("cites_json", columns)
        rows = self.conn.execute("SELECT role, content, cites_json FROM message ORDER BY rowid").fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("user", "What does this paper discuss?", "[]"),
            ("assistant", result["answer"], json.dumps(result["citations"], ensure_ascii=False)),
        ])


if __name__ == "__main__":
    unittest.main()
