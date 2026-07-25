"""actress_extract：CJK 边界、junk 拒识、符号清洗、多人 | 拼接。"""
from __future__ import annotations

import unittest

import actress_extract as ae


class ActressExtractFixesTest(unittest.TestCase):
    def setUp(self) -> None:
        ae._DICT = ae.ActressDict.from_names(
            [
                "美優",
                "小日向美優",
                "一花",
                "黑川一花",
                "美ノ嶋めぐり",
                "瀧本雫葉",
            ]
        )

    def tearDown(self) -> None:
        ae._DICT = None

    def test_dict_rejects_short_name_inside_longer(self) -> None:
        """短名嵌在长名里不得命中（边界）。"""
        self.assertEqual(ae._DICT.find_all("小日向美優"), ["小日向美優"])
        self.assertEqual(ae._DICT.find_all("黑川一花"), ["黑川一花"])
        # 仅短名在词典、长名不在时：也不应抠出子串
        ae._DICT = ae.ActressDict.from_names(["美優", "一花"])
        self.assertEqual(ae._DICT.find_all("小日向美優"), [])
        self.assertEqual(ae._DICT.find_all("黑川一花"), [])

    def test_dict_allows_name_before_works_suffix(self) -> None:
        self.assertEqual(ae._DICT.find_all("瀧本雫葉作品"), ["瀧本雫葉"])

    def test_heuristic_rejects_fetish_junk(self) -> None:
        plain = "【影片名稱】：ABC-001 某某標題 制服戀物癖"
        self.assertIsNone(ae.extract_actress(plain))

    def test_strip_leading_dash(self) -> None:
        plain = "【影片名稱】：ABC-002 標題 -美ノ嶋めぐり"
        self.assertEqual(ae.extract_actress(plain), "美ノ嶋めぐり")

    def test_multi_actress_joined_with_pipe(self) -> None:
        plain = (
            "【影片名称】：[HD/4.32G]JUR-555 對不倫的【妻子・環奈】和【對象・純】"
            "[有碼高清中文字幕]【影片格式】：MP4"
        )
        self.assertEqual(ae.extract_actress(plain), "環奈|純")

    def test_explicit_label_multi_normalized_to_pipe(self) -> None:
        plain = "出演者：環奈、純\n【影片名稱】：XXX-001 foo"
        self.assertEqual(ae.extract_actress(plain), "環奈|純")


if __name__ == "__main__":
    unittest.main()
