"""合集新格式：【原文片名】+ [高清中字]番号C + ==== 分隔。"""
from __future__ import annotations

import unittest

from detail_parse import (
    _parse_series_block_text,
    parse_detail_items,
    parse_detail_page,
    parse_detail_series,
    seed_url_from_anchor,
)


_SAMPLE_PLAIN = """
[高清中字]ABW-177C “妖艳”满身精油4场
【原文片名】：ABW-177C 「なまめかしい」オイルまみれ4本番
【是否有码】：有码
【影片大小】：3.54GB
【验証码】：39AEA945BB5742C60B1C58D9957066B94BF940DB
【影片截图】：

【种子名称】：kks11.cc@ABW-177C.torrent
【磁力连接】：magnet:?xt=urn:btih:39AEA9
【下载网址】：點擊進入下載

================================================================================
[高清中字]ADN-361C 不要就这样结束… 被女儿恋人抱了的母亲 七瀬伊织
【原文片名】：ADN-361C まだ終わらせないで… 娘の恋人に抱かれた母親 七瀬いおり
【是否有码】：有码
【影片大小】：2.03GB
【验証码】：8E73AEECAF153422FDE23A30792B638898D9AFA0
【影片截图】：

【种子名称】：kks11.cc@ADN-361C.torrent
【磁力连接】：magnet:?xt=urn:btih:8E73
【下载网址】：點擊進入下載

================================================================================
[高清中字]CAWD-294C 婚礼前私人健身房NTR，被未婚夫没有的男子气概和精瘦肌肉吸引，不断内射 三舩美铃
【原文片名】：CAWD-294C 結婚式直前パーソナルジムNTR 彼にはない男らしさと細マッチョの肉体に魅せられ性欲解消するまで中出ししまくった絶倫性交 三舩みすず
【是否有码】：有码
【影片大小】：2.40GB
【验証码】：026EA0CB237188
【影片截图】：

【种子名称】：kks11.cc@CAWD-294C.torrent
"""

_SAMPLE_HTML = f"""
<html><body>
<h4 class="f16">[有碼] [MP4/有码]日产高清有码中字好片精选30部合集B[08.10]</h4>
<div id="conttpc">
<pre>{_SAMPLE_PLAIN}</pre>
<img src="https://picdcd.com/upload/files/a.jpg"/>
<img src="https://picdcd.com/upload/files/b.jpg"/>
<img src="https://picdcd.com/upload/files/c.jpg"/>
<a href="https://www.rmdown.com/link.php?hash=abc123">down</a>
</div>
</body></html>
"""


class TestAnthologySeriesFormat(unittest.TestCase):
    def test_parse_block_原文片名_format(self) -> None:
        block = """[高清中字]ABW-177C “妖艳”满身精油4场
【原文片名】：ABW-177C 「なまめかしい」オイルまみれ4本番
【是否有码】：有码
【影片大小】：3.54GB
"""
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb["code"], "ABW-177")
        self.assertIn("妖艳", pb["title"] or "")
        self.assertEqual(pb["size_gb"], "3.54")
        self.assertIsNone(pb.get("actress"))

    def test_anthology_actress_from_原文片名(self) -> None:
        block = """[高清中字]ADN-361C 不要就这样结束… 被女儿恋人抱了的母亲 七瀬伊织
【原文片名】：ADN-361C まだ終わらせないで… 娘の恋人に抱かれた母親 七瀬いおり
【是否有码】：有码
【影片大小】：2.03GB
"""
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb["actress"], "七瀬いおり")

    def test_anthology_actress_prefers_ja_over_cn_false_positive(self) -> None:
        """中文标题可能误抽「男子气概」；应以原文片名尾部女优为准。"""
        block = """[高清中字]CAWD-294C 婚礼前私人健身房NTR，被未婚夫没有的男子气概和精瘦肌肉吸引，不断内射 三舩美铃
【原文片名】：CAWD-294C 結婚式直前パーソナルジムNTR 彼にはない男らしさと細マッチョの肉体に魅せられ性欲解消するまで中出ししまくった絶倫性交 三舩みすず
【是否有码】：有码
【影片大小】：2.40GB
"""
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb["actress"], "三舩みすず")
        self.assertNotEqual(pb["actress"], "男子气概")

    def test_series_plain_splits_on_equals(self) -> None:
        out = parse_detail_series(
            "",
            _SAMPLE_PLAIN,
            [
                "https://picdcd.com/upload/files/a.jpg",
                "https://picdcd.com/upload/files/b.jpg",
                "https://picdcd.com/upload/files/c.jpg",
            ],
            ["https://www.rmdown.com/link.php?hash=abc123"],
        )
        self.assertEqual(out["style"], "series_anthology")
        self.assertEqual(out["torrent_mode"], "one_torrent")
        self.assertEqual(len(out["items"]), 3)
        codes = [it["code"] for it in out["items"]]
        self.assertEqual(codes, ["ABW-177", "ADN-361", "CAWD-294"])
        self.assertEqual(out["items"][0]["size_gb"], "3.54")
        self.assertIsNone(out["items"][0]["actress"])
        self.assertEqual(out["items"][1]["actress"], "七瀬いおり")
        self.assertEqual(out["items"][2]["actress"], "三舩みすず")
        self.assertEqual(
            out["items"][0]["torrent_url"],
            "https://www.rmdown.com/link.php?hash=abc123",
        )
        self.assertTrue(out["items"][0]["poster_url"])

    def test_parse_detail_items_anthology_html(self) -> None:
        items = parse_detail_items(_SAMPLE_HTML)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["code"], "ABW-177")
        self.assertIsNotNone(items[0]["title"])
        self.assertEqual(items[0]["size_gb"], "3.54")
        self.assertIn("rmdown.com", items[0]["torrent_url"] or "")

    def test_anthology_actress_from_影片名称代号(self) -> None:
        """新合集格式：无【原文片名】/【演出女優】，用【影片名称代号】尾部人名。"""
        block = """【MP4】[高清有码]ADN-798 公公怎么看待我？ 凤美优
【影片名称代号】：ADN-798 公公怎么看待我？ 凤美优
【影片格式】：MP4
【是否有码】：有码
【影片大小】：2.06GB
"""
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb["code"], "ADN-798")
        self.assertEqual(pb["actress"], "凤美优")

    def test_anthology_actress_ignores_leading_cn_bracket_tag(self) -> None:
        """片名开头的【重大发表】不是艺名；应取尾部「清野咲」。"""
        block = (
            "【MP4】[高清有码]CAWB-012 【重大发表】腰围52cm 美乳细腰身材 "
            "美丽天才芭蕾舞者 终于中出大解禁 超过50发色情主义 清野咲\n"
            "【影片名称代号】：CAWB-012 【重大发表】腰围52cm 美乳细腰身材 "
            "美丽天才芭蕾舞者 终于中出大解禁 超过50发色情主义 清野咲\n"
            "【影片大小】：2.20GB\n"
        )
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb["actress"], "清野咲")
        self.assertNotEqual(pb["actress"], "重大发表")

    def test_classic_series_block_still_works(self) -> None:
        classic = """ABW-177 妖艳满身精油4场
【發行日期】：2021-01-01
【演出女優】：三田真鈴
【影片大小】：3.54GB
"""
        pb = _parse_series_block_text(classic)
        self.assertEqual(pb["code"], "ABW-177")
        self.assertEqual(pb["actress"], "三田真鈴")

    def test_seed_url_prefers_magnet_anchor_text(self) -> None:
        href = (
            "https://2023.redircdn.com/?magnet:?xt=urn:btih:"
            "9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11______cc@ADN-385C&z"
        )
        text = (
            "magnet:?xt=urn:btih:9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11.cc@ADN-385C"
        )
        self.assertEqual(seed_url_from_anchor(href, text), text)

    def test_seed_url_extracts_magnet_from_wrapper_href(self) -> None:
        href = (
            "https://2023.redircdn.com/?magnet:?xt=urn:btih:"
            "9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11.cc@ADN-385C&z"
        )
        out = seed_url_from_anchor(href, "點擊進入下載")
        self.assertTrue(out and out.startswith("magnet:"))
        self.assertIn("9C9954F1B14117D967A5A547BC5D23EC3BF63D48", out)

    def test_anthology_magnet_anchors_saved_not_rmdown(self) -> None:
        mag1 = (
            "magnet:?xt=urn:btih:9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11.cc@ADN-385C"
        )
        mag2 = (
            "magnet:?xt=urn:btih:39AEA945BB5742C60B1C58D9957066B94BF940DB"
            "&dn=kks11.cc@ABW-177C"
        )
        html = f"""
<html><body>
<h4 class="f16">[有碼] [MP4/有码]日产高清有码中字好片精选30部合集B[08.10]</h4>
<div id="conttpc">
[高清中字]ADN-385C 标题一
【原文片名】：ADN-385C 日文一
【是否有码】：有码
【影片大小】：2.03GB
<a target="_blank" href="https://2023.redircdn.com/?{mag1}&z">{mag1}</a>
================================================================================
[高清中字]ABW-177C 标题二
【原文片名】：ABW-177C 日文二
【是否有码】：有码
【影片大小】：3.54GB
<a target="_blank" href="https://2023.redircdn.com/?{mag2}&z">{mag2}</a>
</div>
</body></html>
"""
        items = parse_detail_items(html)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["code"], "ADN-385")
        self.assertEqual(items[0]["torrent_url"], mag1)
        self.assertEqual(items[1]["code"], "ABW-177")
        self.assertEqual(items[1]["torrent_url"], mag2)
        page = parse_detail_page(html)
        self.assertEqual(page["torrent_mode"], "multi_torrent")


if __name__ == "__main__":
    unittest.main()
