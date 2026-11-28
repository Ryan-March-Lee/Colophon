"""一篇文献不压成一句话：item 不再有 memory / ai_brief，短文本只落在标签定义上。

这两条都是产品决定，不是实现细节：少一个字段，列表行和检索就少一个分类轴；
标签的一句话定义压的是一个概念，不是一篇文献。
"""

import importlib
import os
import sqlite3
import unittest
from tempfile import TemporaryDirectory


class ItemTextFieldsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        os.environ["COLOPHON_LIBRARY"] = self._tmp.name
        import app.paths
        import app.db
        import app.repo

        importlib.reload(app.paths)
        importlib.reload(app.db)
        importlib.reload(app.repo)
        self.repo = app.repo
        self.db = app.db
        self.db.init_db()
        self.conn = self.db.connect()
        self.item_id = self.repo.import_pdf(
            self.conn, "paper.pdf", b"%PDF-1.4 test"
        )["item_id"]

    def tearDown(self):
        self.conn.close()
        os.environ.pop("COLOPHON_LIBRARY", None)
        self._tmp.cleanup()

    def _columns(self):
        return {row["name"] for row in self.conn.execute("PRAGMA table_info(item)")}

    def test_item_has_no_one_line_summary_columns(self):
        columns = self._columns()
        self.assertNotIn("memory", columns)
        self.assertNotIn("ai_brief", columns)
        self.assertNotIn("cites", columns)

    def test_item_payload_has_no_summary_fields(self):
        item = self.repo.get_item(self.conn, self.item_id)
        self.assertNotIn("memory", item)
        self.assertNotIn("aiBrief", item)
        self.assertNotIn("cites", item)

    def test_legacy_memory_patch_is_ignored(self):
        item = self.repo.patch_item(
            self.conn, self.item_id, {"memory": "旧客户端的概括", "note_md": "笔记"}
        )
        self.assertEqual(item["note"], "笔记")
        self.assertNotIn("memory", item)

    def test_note_is_still_writable(self):
        item = self.repo.patch_item(self.conn, self.item_id, {"note_md": "长笔记"})
        self.assertEqual(item["note"], "长笔记")

    def test_missing_abstract_keeps_otherwise_complete_item_in_queue(self):
        self.repo.patch_fields(self.conn, self.item_id, {
            "title": "A complete citation", "year": 2024, "venue": "Journal",
            "authors": ["Alice Smith"], "orgs": ["Example University"],
        })
        # 旧库曾将没有摘要的条目视为完整。
        self.conn.execute("UPDATE item SET needs_review=0 WHERE id=?", (self.item_id,))
        self.conn.commit()
        self.assertEqual(self.repo.mark_items_missing_metadata_for_review(self.conn), 1)
        item, = self.repo.import_queue(self.conn)
        self.assertEqual(item["missing"], ["abstract"])

        item = self.repo.apply_metadata(self.conn, self.item_id, {"abstract": "PDF abstract."})
        self.assertFalse(item["needsReview"])
        self.assertEqual(item["missing"], [])
        self.assertEqual(self.repo.import_queue(self.conn), [])

    def test_automatic_metadata_preserves_existing_abstract_and_note(self):
        self.repo.patch_item(self.conn, self.item_id, {"note_md": "My notes"})
        self.repo.patch_fields(self.conn, self.item_id, {"abstract": "Manual abstract."})
        item = self.repo.apply_metadata(self.conn, self.item_id, {"abstract": "PDF abstract."})
        self.assertEqual(item["abstract"], "Manual abstract.")
        self.assertEqual(item["note"], "My notes")

    def test_tag_definition_is_saved_and_normalized(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "功放")
        self.conn.commit()
        tag_id = self.repo._find_tag(self.conn, "功放")
        saved = self.repo.set_tag_definition(self.conn, tag_id, "  一类射频放大器  ")
        self.assertEqual(saved["definition"], "一类射频放大器")
        tags = self.repo.list_tag_groups(self.conn)
        names = {t["name"]: t["definition"] for t in tags["unfiled"]}
        self.assertEqual(names["功放"], "一类射频放大器")

    def test_unknown_tag_definition_returns_none(self):
        self.assertIsNone(self.repo.set_tag_definition(self.conn, "tg_nope", "x"))


class LegacyLibraryMigrationTests(unittest.TestCase):
    """移除旧摘要列、阅读状态列和索引，保留文献及关联数据。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        os.environ["COLOPHON_LIBRARY"] = self._tmp.name
        import app.paths
        import app.db

        importlib.reload(app.paths)
        importlib.reload(app.db)
        self.db = app.db

        self.db.ensure_library()
        conn = sqlite3.connect(self.db.DB_PATH)
        conn.executescript(
            """
            CREATE TABLE item (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL DEFAULT 'article',
              title TEXT NOT NULL,
              abstract TEXT NOT NULL DEFAULT '',
              year INTEGER,
              venue TEXT,
              doi TEXT,
              arxiv_id TEXT,
              url TEXT,
              language TEXT NOT NULL DEFAULT 'en',
              status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'reading', 'read')),
              starred INTEGER NOT NULL DEFAULT 0,
              memory TEXT NOT NULL DEFAULT '',
              note_md TEXT NOT NULL DEFAULT '',
              cites INTEGER NOT NULL DEFAULT 0,
              ai_brief TEXT NOT NULL DEFAULT '',
              needs_review INTEGER NOT NULL DEFAULT 0,
              added_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              deleted_at TEXT
            );
            INSERT INTO item (id, title, status, starred, memory, note_md, ai_brief, added_at, updated_at)
              VALUES ('itm_old', '旧文献', 'read', 1, '一句话概括', '读书笔记', 'AI 摘要', '2026-01-01', '2026-01-01');
            CREATE INDEX ix_item_status ON item(status);
            CREATE TABLE saved_view (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              filter_json TEXT NOT NULL,
              sort TEXT NOT NULL DEFAULT 'year',
              created_at TEXT NOT NULL
            );
            INSERT INTO saved_view (id, name, filter_json, sort, created_at)
              VALUES ('view_old', '旧视图', '{}', 'cites', '2026-01-01');
            CREATE TABLE conversation (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              scope_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            INSERT INTO conversation VALUES ('cnv_old', '旧对话', '{}', '2026-01-01');
            CREATE TABLE message (
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL REFERENCES conversation(id),
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              cites_json TEXT NOT NULL DEFAULT '[]',
              model TEXT,
              created_at TEXT NOT NULL
            );
            INSERT INTO message VALUES (
              'msg_old', 'cnv_old', 'assistant', '历史回答 [1]', '[{"title":"旧文献"}]',
              'test-model', '2026-01-01'
            );
            """
        )
        conn.executescript(self.db.SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.executescript(
            """
            INSERT INTO tag (id, name, created_at) VALUES ('tag_old', '功放', '2026-01-01');
            INSERT INTO item_tag (item_id, tag_id, created_at)
              VALUES ('itm_old', 'tag_old', '2026-01-01');
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("COLOPHON_LIBRARY", None)
        self._tmp.cleanup()

    def test_init_db_drops_legacy_columns_and_preserves_related_data(self):
        self.db.init_db()
        self.db.init_db()  # 第二次也要幂等
        conn = self.db.connect()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(item)")}
            self.assertNotIn("memory", columns)
            self.assertNotIn("ai_brief", columns)
            self.assertNotIn("status", columns)
            self.assertNotIn("cites", columns)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ix_item_status'").fetchone())
            row = conn.execute("SELECT id, title, starred, note_md FROM item").fetchone()
            self.assertEqual(tuple(row), ("itm_old", "旧文献", 1, "读书笔记"))
            link = conn.execute("SELECT item_id, tag_id FROM item_tag").fetchone()
            self.assertEqual(tuple(link), ("itm_old", "tag_old"))
            self.assertEqual(
                conn.execute("SELECT sort FROM saved_view WHERE id='view_old'").fetchone()[0],
                "year",
            )
            message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(message)")}
            self.assertIn("cites_json", message_columns)
            message = conn.execute("SELECT content, cites_json, model FROM message WHERE id='msg_old'").fetchone()
            self.assertEqual(tuple(message), ("历史回答 [1]", '[{"title":"旧文献"}]', "test-model"))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
