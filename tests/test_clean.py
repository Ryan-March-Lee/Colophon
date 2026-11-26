"""用真实库的脏数据校对清洗规则；把误判挡在写库之前。"""

from __future__ import annotations

import importlib
import os
import unittest
from tempfile import TemporaryDirectory

from app import clean


class CleanAuthorRuleTests(unittest.TestCase):
    def test_entity_and_leading_marker(self):
        verdict = clean.clean_author("{h.fan", set())
        self.assertEqual(verdict["action"], "drop", "花括号片段不是人名")

    def test_superscript_tail_is_removed(self):
        # 去掉 ∗ 后剩 `Liyuan Xuea`；库里已有同名 `Liyuan Xue`，才继续削掉 a
        verdict = clean.clean_author("Liyuan Xuea∗", {"liyuan xue"})
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Liyuan Xue")

    def test_superscript_tail_keeps_surname_without_evidence(self):
        verdict = clean.clean_author("Liyuan Xuea∗", set())
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Liyuan Xuea")

    def test_digit_tail_is_removed(self):
        verdict = clean.clean_author("Anh-Vu Pham1", set())
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Anh-Vu Pham")

    def test_affiliation_letter_merges_into_known_name(self):
        known = {"yuan ding"}
        verdict = clean.clean_author("Yuan Dingb", known)
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Yuan Ding")

    def test_affiliation_letter_without_known_name_keeps_name(self):
        # 无证据不改：`Haijun Fanb` 也可能是真的姓氏
        verdict = clean.clean_author("Haijun Fanb", set())
        self.assertEqual(verdict["action"], "keep")

    def test_email_is_dropped(self):
        verdict = clean.clean_author("bo.liu@glasgow.ac.uk", set())
        self.assertEqual(verdict["action"], "drop")

    def test_sentence_fragment_is_dropped(self):
        verdict = clean.clean_author(
            "we demonstrate a universal inverse design approach", set()
        )
        self.assertEqual(verdict["action"], "drop")

    def test_accent_split_is_repaired(self):
        verdict = clean.clean_author("Jordi Verd´u", set())
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Jordi Verdú")

    def test_clean_name_is_kept(self):
        verdict = clean.clean_author("Jialin Cai", set())
        self.assertEqual(verdict["action"], "keep")

    def test_hyphen_and_particles_are_kept(self):
        for name in ("Anh-Vu Pham", "Pedro de Paco", "C.-C. Wang"):
            self.assertEqual(clean.clean_author(name, set())["action"], "keep")


class CleanOrgRuleTests(unittest.TestCase):
    def test_html_entity_is_decoded(self):
        verdict = clean.clean_org("ETH Z&#x00FC;rich,Switzerland")
        self.assertEqual(verdict["action"], "auto")
        self.assertIn("Zürich", verdict["value"])

    def test_leading_marker_is_removed(self):
        verdict = clean.clean_org(
            "#Princeton University Electrical and Computer Engineering, USA"
        )
        self.assertEqual(verdict["action"], "auto")
        self.assertTrue(verdict["value"].startswith("Princeton"))

    def test_lowercase_typo_is_not_guessed(self):
        # 「Ihiversity」这类 OCR 错字不做自动猜测，只保证不误改
        verdict = clean.clean_org("Carletan Ihiversity")
        self.assertNotEqual(verdict["action"], "auto")

    def test_short_fragment_is_flagged(self):
        verdict = clean.clean_org("Instituto di")
        self.assertEqual(verdict["action"], "review")


class CleanAuthorNoEvidenceTests(unittest.TestCase):
    """没有同名的「尾部单字母」必须保持原样——那可能是真的姓氏。"""

    def test_real_surname_ending_in_a_is_untouched(self):
        for name in ("Jing Xia", "Kaushik Sengupta", "Liyuan Xue", "Songbai He"):
            verdict = clean.clean_author(name, set())
            self.assertEqual(verdict["action"], "keep", name)

    def test_affiliation_letter_still_merges_when_name_exists(self):
        verdict = clean.clean_author("Yuan Dingb", {"yuan ding"})
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Yuan Ding")

    def test_marked_name_keeps_surname_without_evidence(self):
        # 去掉 ∗ 之后不强拆 a：`Simona Donati Guerrieri3*` 没有疑点才动尾部
        verdict = clean.clean_author("Simona Donati Guerrieri3*", set())
        self.assertEqual(verdict["action"], "auto")
        self.assertEqual(verdict["value"], "Simona Donati Guerrieri")


class CleanApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        os.environ["COLOPHON_LIBRARY"] = self._tmp.name
        import app.paths
        import app.db
        import app.repo

        importlib.reload(app.paths)
        importlib.reload(app.db)
        importlib.reload(app.repo)
        importlib.reload(clean)
        self.repo = app.repo
        self.db = app.db
        self.db.init_db()
        self.conn = self.db.connect()
        self.item_a = self.repo.import_pdf(self.conn, "a.pdf", b"%PDF-1.4 a")["item_id"]
        self.item_b = self.repo.import_pdf(self.conn, "b.pdf", b"%PDF-1.4 b")["item_id"]

    def tearDown(self):
        self.conn.close()
        os.environ.pop("COLOPHON_LIBRARY", None)
        self._tmp.cleanup()

    def _authors(self):
        rows = self.conn.execute(
            "SELECT a.id, a.display_name, COUNT(ia.item_id) AS n "
            "FROM author a LEFT JOIN item_author ia ON ia.author_id = a.id "
            "GROUP BY a.id ORDER BY a.display_name"
        ).fetchall()
        return {r["display_name"]: r["n"] for r in rows}

    def test_rename_merges_into_existing_author(self):
        self.repo.add_tag(self.conn, self.item_a, "author", "Yuan Ding")
        self.repo.add_tag(self.conn, self.item_b, "author", "Yuan Dingb")
        self.conn.commit()
        scan = clean.scan(self.conn)
        target = next(a for a in scan["authors"] if a["name"] == "Yuan Dingb")
        result = clean.apply(
            self.conn,
            author_ops=[{"id": target["id"], "action": "rename", "value": "Yuan Ding"}],
        )
        authors = self._authors()
        self.assertNotIn("Yuan Dingb", authors)
        self.assertEqual(authors["Yuan Ding"], 2)
        self.assertEqual(result["authorsRenamed"], 1)

    def test_drop_removes_author_and_keeps_items(self):
        self.repo.add_tag(self.conn, self.item_a, "author", "bo.liu@glasgow.ac.uk")
        self.conn.commit()
        scan = clean.scan(self.conn)
        target = next(a for a in scan["authors"] if "@" in a["name"])
        result = clean.apply(self.conn, author_ops=[{"id": target["id"], "action": "drop"}])
        authors = self._authors()
        self.assertNotIn("bo.liu@glasgow.ac.uk", authors)
        # 条目不能因此消失
        items = self.repo.list_items(self.conn)
        self.assertEqual(len(items), 2)
        self.assertEqual(result["authorsDropped"], 1)

    def test_keep_is_not_touched_without_an_op(self):
        self.repo.add_tag(self.conn, self.item_a, "author", "Jialin Cai")
        self.repo.add_tag(self.conn, self.item_b, "author", "Jialin Cai1")
        self.conn.commit()
        before = self._authors()
        result = clean.apply(self.conn, author_ops=[])
        self.assertEqual(self._authors(), before)
        self.assertEqual(result["authorsRenamed"], 0)

    def test_org_merge_deduplicates(self):
        self.repo.add_tag(self.conn, self.item_a, "org", "ETH Zurich")
        self.repo.add_tag(self.conn, self.item_b, "org", "ETH Z&#x00FC;rich,Switzerland")
        self.conn.commit()
        scan = clean.scan(self.conn)
        target = next(o for o in scan["orgs"] if o["name"].startswith("ETH Z"))
        clean.apply(
            self.conn,
            org_ops=[{"id": target["id"], "action": "rename", "value": "ETH Zurich"}],
        )
        rows = self.conn.execute(
            "SELECT o.name, COUNT(io.item_id) AS n FROM org o "
            "LEFT JOIN item_org io ON io.org_id = o.id GROUP BY o.id"
        ).fetchall()
        names = {r["name"]: r["n"] for r in rows}
        self.assertEqual(names.get("ETH Zurich"), 2)


if __name__ == "__main__":
    unittest.main()
