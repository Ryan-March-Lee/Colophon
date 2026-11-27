import hashlib
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import db, repo


class ItemDeletionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.library = Path(tmp.name).resolve()
        (self.library / "files").mkdir()
        library_patch = patch.object(repo, "LIBRARY", self.library)
        library_patch.start()
        self.addCleanup(library_patch.stop)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT INTO item (id, title, note_md, added_at, updated_at) "
            "VALUES ('itm_test', 'Paper', 'A note', '', '')"
        )
        self.original = self.library / "original.pdf"
        self.managed = self.library / "files" / "paper.pdf"
        for path in (self.original, self.managed):
            path.write_bytes(b"%PDF-test")
        self.conn.execute(
            "INSERT INTO file (id, item_id, rel_path, sha256, added_at) "
            "VALUES ('fil_test', 'itm_test', 'files/paper.pdf', ?, '')",
            (hashlib.sha256(self.managed.read_bytes()).hexdigest(),),
        )
        self.conn.execute("INSERT INTO annotation (id, file_id, page, created_at) VALUES ('a', 'fil_test', 1, '')")
        self.conn.commit()

    def test_delete_record_and_managed_pdf(self):
        self.assertEqual(repo.delete_item(self.conn, "itm_test"), {"ok": True, "filesDeleted": 1})
        self.assertEqual(repo.list_items(self.conn), [])
        self.assertIsNone(repo.get_item_file(self.conn, "itm_test"))
        self.assertIsNone(self.conn.execute("SELECT id FROM file").fetchone())
        self.assertIsNone(self.conn.execute("SELECT id FROM annotation").fetchone())
        self.assertFalse(self.managed.exists())
        self.assertFalse((self.library / ".trash").exists())
        self.assertEqual(self.original.read_bytes(), b"%PDF-test")
        self.assertIsNone(repo.delete_item(self.conn, "itm_test"))

    def test_delete_record_when_managed_pdf_is_already_missing(self):
        self.managed.unlink()
        self.assertEqual(repo.delete_item(self.conn, "itm_test")["filesDeleted"], 0)
        self.assertIsNone(repo.get_item(self.conn, "itm_test"))

    def test_external_path_is_not_deleted(self):
        self.conn.execute("UPDATE file SET rel_path = 'files/../original.pdf'")
        self.conn.commit()
        with self.assertRaises(OSError):
            repo.delete_item(self.conn, "itm_test")
        self.assertTrue(self.original.is_file())
        self.assertIsNotNone(repo.get_item(self.conn, "itm_test"))

    def test_failed_file_delete_preserves_record(self):
        with patch.object(Path, "unlink", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                repo.delete_item(self.conn, "itm_test")
        self.assertTrue(self.managed.is_file())
        self.assertIsNotNone(repo.get_item_file(self.conn, "itm_test"))
        self.assertIsNotNone(repo.get_item(self.conn, "itm_test"))

    def test_failed_db_delete_leaves_pdf_in_place(self):
        self.conn.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON item "
            "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repo.delete_item(self.conn, "itm_test")
        self.assertTrue(self.managed.is_file())
        self.assertIsNotNone(repo.get_item(self.conn, "itm_test"))
