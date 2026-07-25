"""针对 7362981 等中文片名 / 番号后缀 / HARD 误判的 actress 修复。"""
from __future__ import annotations

import unittest

import actress_extract as ae
from detail_parse import CODE_RE, parse_detail_items


class ActressCnTitleFixesTest(unittest.TestCase):
    def setUp(self) -> None:
        ae._DICT = ae.ActressDict.from_names(
            [
                "渚恋生",
                "小笠原菜乃",
                "小那海あや",
                "小日向美優",
                "美優",
            ]
        )

    def tearDown(self) -> None:
        ae._DICT = None

    def test_code_re_strips_letter_suffix(self) -> None:
        m = CODE_RE.search("START-423v 拜訪")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "START-423")

    def test_dict_boundary_allows_particle_and_jiang(self) -> None:
        self.assertEqual(
            ae._DICT.find_all("溫泉的小笠原菜乃醬（20歲）"),
            ["小笠原菜乃"],
        )

    def test_dict_trad_simp_normalize(self) -> None:
        self.assertEqual(ae._DICT.find_all("發生了…渚戀生"), ["渚恋生"])

    def test_cn_pattern_de_name_zai(self) -> None:
        plain = (
            "【影片名称】：[HD/3.95G]START-411 現役工廠女子擔任一天宿舍媽媽！"
            "讓SODSTAR的天音環奈在工廠員工宿舍照顧你！"
        )
        self.assertEqual(ae.extract_actress(plain), "天音環奈")

    def test_cn_pattern_ellipsis_tail(self) -> None:
        plain = (
            "【影片名称】：[HD/3.50G]START-420 和傲慢的女上司夏日的監視調查21天，"
            "超過兩週後，星還是沒有動彈，我們忍不住焦躁不安，"
            "在剩下的三天裡汗流浹背地發生了…渚戀生"
        )
        self.assertEqual(ae.extract_actress(plain), "渚恋生")

    def test_cn_pattern_name_before_seqing(self) -> None:
        plain = "【影片名称】：[HD/3.03G]VAGU-283 小那海綾色情轉變 制服角色扮演情境玩法"
        self.assertEqual(ae.extract_actress(plain), "小那海綾")

    def test_rejects_hard_latin_junk(self) -> None:
        plain = (
            "【影片名称】：[HD/4.97G]START-423v 拜訪那須鹽原溫泉的小笠原菜乃醬（20歲）"
            "要不要試試看只裹著一條毛巾進入男湯？HARD"
        )
        self.assertEqual(ae.extract_actress(plain), "小笠原菜乃")

    def test_trailing_name_after_wave_dash(self) -> None:
        ae._DICT = ae.ActressDict.from_names(["河合明日菜", "河合あすな"])
        plain = (
            "【影片名稱】：[MP4/1.51G] ABF-249 搞過頭中出溫泉旅館～極品H罩杯隨心所欲～河合明日菜"
        )
        self.assertEqual(ae.extract_actress(plain), "河合明日菜")

    def test_dict_suffix_glued_after_documentary(self) -> None:
        ae._DICT = ae.ActressDict.from_names(["石川美鈴"])
        plain = "【影片名稱】：[MP4/1.55G] JRZE-288 初次拍攝人妻紀錄片石川美鈴"
        self.assertEqual(ae.extract_actress(plain), "石川美鈴")

    def test_trad_lai_to_ja_se(self) -> None:
        ae._DICT = ae.ActressDict.from_names(["綾瀬天"])
        plain = "【影片名称】：[HD/4.92G]START-400 SODSTAR 綾瀨天 引退 紀念107發精液顏射"
        self.assertEqual(ae.extract_actress(plain), "綾瀬天")

    def test_goji_dash_actress_stays_none(self) -> None:
        plain = "出演者：----\nSD-MP4-2.51GB\n"
        h4 = (
            "[SD/2.5G] GOJI-106 「私をペットとして飼ってください…」"
            "狂った愛情で幼なじみを調教し尽くす"
        )
        self.assertIsNone(ae.extract_actress(plain, h4))

    def test_parse_start423v_code_and_actress(self) -> None:
        html = """
        <html><body>
          <h4 class="f16">[有碼] [HD/4.97G]START-423v 拜訪那須鹽原溫泉的小笠原菜乃醬（20歲）HARD</h4>
          <div id="conttpc">
            【影片名称】：[HD/4.97G]START-423v 拜訪那須鹽原溫泉的小笠原菜乃醬（20歲）
            要不要試試看只裹著一條毛巾進入男湯？HARD[有碼高清中文字幕]<br>
            <a href="https://www.rmdown.com/link.php?hash=abc">t</a>
          </div>
        </body></html>
        """
        items = parse_detail_items(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "START-423")
        self.assertEqual(items[0]["actress"], "小笠原菜乃")


if __name__ == "__main__":
    unittest.main()
