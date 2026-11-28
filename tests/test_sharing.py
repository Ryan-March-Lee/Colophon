import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf

from app import repo, sharing


class SharePackageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source_path = Path(temp.name) / "source"
        self.local_path = Path(temp.name) / "local"
        self.source = self.make_library()
        self.local = self.make_library()

    def make_library(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / "app" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        return conn

    def pdf(self, marker="first version"):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((50, 50), f"DOI: 10.1234/Example\n{marker}\nAbstract\nPaper abstract.")
            return doc.tobytes()

    def seed(self, conn, path, data=None, title="Example paper"):
        with patch.object(repo, "LIBRARY", path):
            item_id = repo.import_pdf(conn, "original.pdf", data or self.pdf())["item_id"]
            repo.patch_fields(conn, item_id, {
                "title": title, "year": 2025, "authors": ["Alice"], "orgs": ["Lab"],
                "abstract": "Paper abstract.", "doi": "https://doi.org/10.1234/EXAMPLE",
            })
        return item_id

    def transfer(self, data):
        preview = sharing.import_package(self.local, data)
        return sharing.import_package(self.local, data, preview["token"])

    def test_round_trip_keeps_metadata_private_fields_and_group_rules(self):
        pdf = self.pdf()
        item_id = self.seed(self.source, self.source_path, pdf)
        repo.patch_item(self.source, item_id, {"starred": True, "note_md": "My note"})
        repo.add_tag(self.source, item_id, "topic", "Common", group_name="Source group")
        repo.add_tag(self.source, item_id, "topic", "New tag", group_name="Source group")
        tag = repo._find_tag(self.source, "New tag")
        repo.set_tag_definition(self.source, tag, "Definition")
        plain = json.loads(sharing.export_package(self.source, [item_id], False, False))
        self.assertNotIn("groups", plain)
        self.assertNotIn("group_id", plain["tags"][0])
        self.assertNotIn("note", plain["items"][0])
        data = sharing.export_package(self.source, [item_id], True, True)
        exported = json.loads(data)["items"][0]
        self.assertNotIn("cites", exported)
        for private in ("starred", "fileName", "original_name", "rel_path", "needs_review"):
            self.assertNotIn(private, exported)
        self.assertNotIn(b"%PDF", data)

        repo.ensure_tag(self.local, "Common")
        self.local.commit()
        with patch.object(repo, "LIBRARY", self.local_path):
            preview = sharing.import_package(self.local, data)
            self.assertEqual(preview["added"], 1)
            self.assertEqual(repo.list_items(self.local), [])
            self.assertEqual(sharing.import_package(self.local, data, preview["token"])["added"], 1)
            item, = repo.list_items(self.local)
            self.assertFalse(item["hasFile"])
            self.assertFalse(item["needsReview"])
            self.assertFalse(item["starred"])
            self.assertEqual(item["note"], "My note")
            self.assertEqual(item["abstract"], "Paper abstract.")
            groups = repo.list_tag_groups(self.local)
            self.assertEqual([tag["name"] for tag in groups["unfiled"]], ["Common"])
            self.assertEqual(groups["groups"][0]["tags"][0]["definition"], "Definition")
            repo.patch_item(self.local, item["id"], {"note_md": "Local note"})
            self.assertEqual(self.transfer(data)["merged"], 1)
            self.assertEqual(repo.get_item(self.local, item["id"])["note"], "Local note")
            self.assertEqual(repo.import_pdf(self.local, "renamed.pdf", pdf)["status"], "attached")
            attached, = repo.list_items(self.local)
            self.assertTrue(attached["hasFile"])
            self.assertEqual(attached["id"], item["id"])

    def test_different_pdf_matches_in_both_orders(self):
        item_id = self.seed(self.source, self.source_path)
        data = sharing.export_package(self.source, [item_id], False, False)
        other_pdf = self.pdf("updated version")
        with patch.object(repo, "LIBRARY", self.local_path):
            existing = repo.import_pdf(self.local, "received.pdf", other_pdf)["item_id"]
            self.assertEqual(self.transfer(data)["merged"], 1)
            item, = repo.list_items(self.local)
            self.assertEqual(item["id"], existing)
            self.assertEqual(item["title"], "Example paper")
            self.assertTrue(item["hasFile"])

            self.local = self.make_library()
            self.assertEqual(self.transfer(data)["added"], 1)
            self.assertEqual(repo.import_pdf(self.local, "received-updated.pdf", other_pdf)["status"], "attached")
            self.assertEqual(len(repo.list_items(self.local)), 1)

    def test_legacy_share_citation_count_is_ignored(self):
        item_id = self.seed(self.source, self.source_path)
        raw = json.loads(sharing.export_package(self.source, [item_id], False, False))
        raw["items"][0]["cites"] = 42
        package = sharing.read_package(json.dumps(raw).encode())
        self.assertFalse(hasattr(package.items[0], "cites"))

    def test_exact_file_with_multiple_printed_dois_still_attaches(self):
        pdf = self.pdf("DOI: 10.1234/another")
        item_id = self.seed(self.source, self.source_path, pdf)
        data = sharing.export_package(self.source, [item_id], False, False)
        with patch.object(repo, "LIBRARY", self.local_path):
            self.transfer(data)
            self.assertEqual(repo.import_pdf(self.local, "same.pdf", pdf)["status"], "attached")

    def test_conflicts_version_and_stale_preview(self):
        pdf = self.pdf()
        item_id = self.seed(self.source, self.source_path, pdf)
        data = sharing.export_package(self.source, [item_id], False, False)
        with patch.object(repo, "LIBRARY", self.local_path):
            preview = sharing.import_package(self.local, data)
            existing = self.seed(self.local, self.local_path, pdf)
            with self.assertRaisesRegex(ValueError, "重新预览"):
                sharing.import_package(self.local, data, preview["token"])
            repo.patch_fields(self.local, existing, {"doi": "10.1234/other"})
            report = self.transfer(data)
            self.assertEqual((report["added"], report["merged"], len(report["conflicts"])), (0, 0, 1))
            bad = data.replace(b'"version": 1', b'"version": 2')
            with self.assertRaisesRegex(ValueError, "格式版本"):
                sharing.import_package(self.local, bad)
            self.assertEqual(len(repo.list_items(self.local)), 1)

    def test_title_alone_does_not_merge_but_reimport_uses_stable_key(self):
        item_id = self.seed(self.source, self.source_path)
        raw = json.loads(sharing.export_package(self.source, [item_id], False, False))
        raw["items"][0].update(doi=None, pdf_hashes=[])
        data = json.dumps(raw).encode()
        self.seed(self.local, self.local_path)
        self.assertEqual(self.transfer(data)["added"], 1)
        self.assertEqual(self.transfer(data)["merged"], 1)
        self.assertEqual(len(repo.list_items(self.local)), 2)
