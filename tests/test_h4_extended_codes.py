"""signal h4：FC2PPV / 欧美点分番号 + 无番号时 title 回退。"""
from __future__ import annotations

import unittest

from detail_parse import parse_detail_items, parse_detail_signal


def _signal_item(h4: str, *, plain: str = "") -> dict:
    out = parse_detail_signal(
        "",
        h4,
        plain,
        [],
        ["https://www.rmdown.com/link.php?hash=abc"],
        topic_title=h4,
    )
    return out["items"][0]


class TestH4ExtendedCodes(unittest.TestCase):
    def test_fc2ppv_code_and_full_title_rest(self) -> None:
        h4 = (
            "新作 [無碼] [HD/2.4G] [中字] FC2PPV-3954834 "
            "同一公司的前辈和后辈。与两位美女的后宫性爱 篠宮花音 & ひかり唯"
        )
        it = _signal_item(h4)
        self.assertEqual(it["code"], "FC2PPV-3954834")
        self.assertEqual(
            it["title"],
            "同一公司的前辈和后辈。与两位美女的后宫性爱 篠宮花音 & ひかり唯",
        )
        self.assertEqual(it["size_gb"], "2.4")

    def test_fc2_ppv_variant_normalized(self) -> None:
        h4 = "新作 [HD/3.8G] FC2-PPV-3879833 18岁的女大学生康娜酱 超敏感体质"
        it = _signal_item(h4)
        self.assertEqual(it["code"], "FC2PPV-3879833")
        self.assertEqual(it["title"], "18岁的女大学生康娜酱 超敏感体质")

    def test_western_dotted_full_id_as_code(self) -> None:
        h4 = (
            "新作 [歐美] [HD/1.79G] "
            "Blacked.15.10.03.Kate.England.And.Ash.Hollywood 浪漫闺蜜情"
        )
        it = _signal_item(h4)
        self.assertEqual(
            it["code"], "Blacked.15.10.03.Kate.England.And.Ash.Hollywood"
        )
        self.assertEqual(it["title"], "浪漫闺蜜情")

    def test_western_dotted_second_sample(self) -> None:
        h4 = (
            "新作 [歐美] [HD/1.71G] "
            "Blacked.15.10.08.Jade.Nile.And.Chanel.Preston 两个好姐妹一起享受大黑屌"
        )
        it = _signal_item(h4)
        self.assertEqual(
            it["code"], "Blacked.15.10.08.Jade.Nile.And.Chanel.Preston"
        )
        self.assertTrue(it["title"].startswith("两个好姐妹"))

    def test_standard_code_still_wins(self) -> None:
        h4 = "新作 [有碼] [HD/6.2G] IPX-123 测试片名 (三田真鈴)"
        it = _signal_item(h4)
        self.assertEqual(it["code"], "IPX-123")
        self.assertIn("测试片名", it["title"])

    def test_no_code_fallback_title_from_h4(self) -> None:
        h4 = "新作 [無碼] [HD/2.0G] [中字] 只有描述没有番号的标题文本"
        it = _signal_item(h4)
        self.assertIsNone(it["code"])
        self.assertEqual(it["title"], "只有描述没有番号的标题文本")

    def test_parse_detail_items_persists_title(self) -> None:
        html = """
        <h4 class="f16">新作 [歐美] [HD/1.79G] Blacked.15.10.03.Kate.England.And.Ash.Hollywood 浪漫闺蜜情</h4>
        <div id="conttpc">
          <a href="https://www.rmdown.com/link.php?hash=abc">down</a>
        </div>
        """
        items = parse_detail_items(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["code"], "Blacked.15.10.03.Kate.England.And.Ash.Hollywood"
        )
        self.assertEqual(items[0]["title"], "浪漫闺蜜情")


if __name__ == "__main__":
    unittest.main()
