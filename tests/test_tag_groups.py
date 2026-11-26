"""标签分组：一个标签只属于一个组、未分组是算出来的、合并与删组的行为。

分组是检索台的地基，这几条不变量坏了，首页就会静默丢文献。
"""

import importlib
import os
import unittest
from tempfile import TemporaryDirectory


class TagGroupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        os.environ["COLOPHON_LIBRARY"] = self._tmp.name
        # 依次重载，让 db / repo 拿到新的 LIBRARY 常量
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

    def _snapshot(self):
        data = self.repo.list_tag_groups(self.conn)
        groups = {g["name"]: [t["name"] for t in g["tags"]] for g in data["groups"]}
        return groups, [t["name"] for t in data["unfiled"]]

    def test_new_tag_without_group_lands_in_unfiled(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "杂项")
        self.conn.commit()
        groups, unfiled = self._snapshot()
        self.assertEqual(groups, {})
        self.assertEqual(unfiled, ["杂项"])

    def test_tag_created_into_group(self):
        group_id = self.repo.ensure_tag_group(self.conn, "功放组")
        self.conn.commit()
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty", group_id=group_id)
        self.conn.commit()
        groups, unfiled = self._snapshot()
        self.assertEqual(groups, {"功放组": ["Doherty"]})
        self.assertEqual(unfiled, [])

    def test_group_name_creates_group_on_the_fly(self):
        self.repo.add_tag(
            self.conn, self.item_id, "topic", "神经网络", group_name="算法组"
        )
        self.conn.commit()
        groups, _ = self._snapshot()
        self.assertEqual(groups, {"算法组": ["神经网络"]})

    def test_unfiled_tag_moves_into_group(self):
        self.repo.add_tag(self.conn, self.item_id, "topic", "包络跟踪")
        group_id = self.repo.ensure_tag_group(self.conn, "功放组")
        self.conn.commit()
        tag_id = self.repo._find_tag(self.conn, "包络跟踪")
        self.repo.set_tag_group(self.conn, tag_id, group_id)
        groups, unfiled = self._snapshot()
        self.assertEqual(groups, {"功放组": ["包络跟踪"]})
        self.assertEqual(unfiled, [])

    def test_tag_is_never_silently_moved_between_groups(self):
        pa = self.repo.ensure_tag_group(self.conn, "功放组")
        algo = self.repo.ensure_tag_group(self.conn, "算法组")
        self.conn.commit()
        self.repo.add_tag(self.conn, self.item_id, "topic", "DPD", group_id=pa)
        self.conn.commit()
        # 另一个组也「声明」这个标签，不能把已有归属悄悄改掉
        self.repo.ensure_tag(self.conn, "DPD", group_id=algo)
        self.conn.commit()
        groups, _ = self._snapshot()
        self.assertEqual(groups["功放组"], ["DPD"])
        self.assertEqual(groups["算法组"], [])

    def test_empty_group_is_kept(self):
        self.repo.ensure_tag_group(self.conn, "空组")
        self.conn.commit()
        groups, _ = self._snapshot()
        self.assertEqual(groups, {"空组": []})

    def test_merge_inherits_group_from_merged_tag(self):
        group_id = self.repo.ensure_tag_group(self.conn, "功放组")
        self.conn.commit()
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty", group_id=group_id)
        self.conn.commit()
        self.repo.merge(self.conn, "topic", "Doherty", "Doherty PA")
        self.conn.commit()
        groups, unfiled = self._snapshot()
        self.assertEqual(groups, {"功放组": ["Doherty PA"]})
        self.assertEqual(unfiled, [])

    def test_delete_group_returns_tags_to_unfiled(self):
        group_id = self.repo.ensure_tag_group(self.conn, "功放组")
        self.conn.commit()
        self.repo.add_tag(self.conn, self.item_id, "topic", "Doherty", group_id=group_id)
        self.conn.commit()
        self.assertEqual(self.repo.delete_tag_group(self.conn, group_id), 1)
        groups, unfiled = self._snapshot()
        self.assertEqual(groups, {})
        self.assertEqual(unfiled, ["Doherty"])
        # 再删一次：已不存在
        self.assertIsNone(self.repo.delete_tag_group(self.conn, group_id))

    def test_group_name_is_reused_case_insensitively(self):
        first = self.repo.ensure_tag_group(self.conn, "功放组")
        self.conn.commit()
        second = self.repo.ensure_tag_group(self.conn, "功放组")
        self.assertEqual(first, second)
        self.assertEqual(len(self.repo.list_tag_groups(self.conn)["groups"]), 1)


if __name__ == "__main__":
    unittest.main()
