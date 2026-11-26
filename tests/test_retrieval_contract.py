"""检索内核的数据契约：/api/items 必须带上 topicIds（标签 id），标签名不能当身份。

检索台用标签 id 判断布尔项，因为改名和合并都会让名字变；id 不变。
名字仍要留着给列表行和悬停定义用，所以是两份，不是替换。
"""

import importlib
import os
import unittest
from tempfile import TemporaryDirectory


class ItemTagIdTests(unittest.TestCase):
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

    def _item(self, item_id=None):
        return self.repo.get_item(self.conn, item_id or self.item_id)

    def test_item_carries_both_names_and_ids(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty")
        self.conn.commit()
        item = self._item()
        self.assertEqual(item["tags"]["topic"], ["Doherty"])
        self.assertEqual(len(item["topicIds"]), 1)
        tag_id = item["topicIds"][0]
        row = self.conn.execute("SELECT name FROM tag WHERE id = ?", (tag_id,)).fetchone()
        self.assertEqual(row["name"], "Doherty")

    def test_merged_away_id_disappears_and_survivor_is_live(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty")
        self.conn.commit()
        before = self._item()
        self.repo.merge(self.conn, "topic", "Doherty", "Doherty PA")
        self.conn.commit()
        after = self._item()
        # 合并是「新名建标签 + 旧标签软删 + 别名」：旧 id 消失，新 id 是活的。
        # 视图里存的旧 id 就是这样失效的（§9：少一个项，不报错）。
        self.assertEqual(after["tags"]["topic"], ["Doherty PA"])
        self.assertEqual(len(after["topicIds"]), 1)
        tag_id = after["topicIds"][0]
        live = self.conn.execute(
            "SELECT 1 FROM tag WHERE id = ? AND deleted_at IS NULL", (tag_id,)
        ).fetchone()
        self.assertIsNotNone(live)
        self.assertNotEqual(after["topicIds"], before["topicIds"])
        old = self.conn.execute(
            "SELECT deleted_at FROM tag WHERE id = ?", (before["topicIds"][0],)
        ).fetchone()
        self.assertIsNotNone(old)
        self.assertIsNotNone(old["deleted_at"])

    def test_merge_into_existing_tag_keeps_its_id(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty")
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty PA")
        self.conn.commit()
        item = self._item()
        names = item["tags"]["topic"]
        keep = item["topicIds"][names.index("Doherty PA")]
        self.repo.merge(self.conn, "topic", "Doherty", "Doherty PA")
        self.conn.commit()
        item = self._item()
        self.assertEqual(item["tags"]["topic"], ["Doherty PA"])
        self.assertEqual(item["topicIds"][0], keep)

    def test_untagged_item_has_empty_ids(self):
        item = self._item()
        self.assertEqual(item["tags"]["topic"], [])
        self.assertEqual(item["topicIds"], [])

    def test_list_and_get_agree(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "DPD")
        self.conn.commit()
        single = self.repo.get_item(self.conn, self.item_id)
        listed = self.repo.list_items(self.conn)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["topicIds"], single["topicIds"])
        self.assertEqual(listed[0]["tags"]["topic"], ["DPD"])

    def test_ids_are_stable_under_new_tag_on_same_item(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "DPD")
        self.conn.commit()
        first = set(self._item()["topicIds"])
        self.repo.add_tag(self.conn, self.item_id, "topic", "LNA")
        self.conn.commit()
        second = set(self._item()["topicIds"])
        self.assertEqual(second, first | (second - first))
        self.assertTrue(first <= second, "已有标签的 id 不能因为新加一个标签而变化")


if __name__ == "__main__":
    unittest.main()
