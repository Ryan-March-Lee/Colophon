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

    def test_item_payload_has_no_summary_fields(self):
        item = self.repo.get_item(self.conn, self.item_id)
        self.assertNotIn("memory", item)
        self.assertNotIn("aiBrief", item)

    def test_legacy_memory_patch_is_ignored(self):
        item = self.repo.patch_item(
            self.conn, self.item_id, {"memory": "旧客户端的概括", "note_md": "笔记"}
        )
        self.assertEqual(item["note"], "笔记")
        self.assertNotIn("memory", item)

    def test_note_is_still_writable(self):
        item = self.repo.patch_item(self.conn, self.item_id, {"note_md": "长笔记"})
        self.assertEqual(item["note"], "长笔记")

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
    """老库里的 memory / ai_brief 两列必须靠 init_db 显式删掉，schema.sql 帮不上。"""

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
              status TEXT NOT NULL DEFAULT 'todo',
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
            INSERT INTO item (id, title, memory, ai_brief, added_at, updated_at)
              VALUES ('itm_old', '旧文献', '一句话概括', 'AI 摘要', '2026-01-01', '2026-01-01');
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("COLOPHON_LIBRARY", None)
        self._tmp.cleanup()

    def test_init_db_drops_legacy_summary_columns(self):
        self.db.init_db()
        self.db.init_db()  # 第二次也要幂等
        conn = self.db.connect()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(item)")}
            self.assertNotIn("memory", columns)
            self.assertNotIn("ai_brief", columns)
            row = conn.execute("SELECT id, note_md FROM item").fetchone()
            self.assertEqual(row["id"], "itm_old")
            self.assertEqual(row["note_md"], "")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
