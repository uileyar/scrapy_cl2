"""离线断言：detail-signal / detail-xilie / detail-xilie2 三种详情样式。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

from detail_parse import (
    _actress_from_conttpc_plain,
    _h4_size_gb,
    _parse_series_block_text,
    parse_detail_page,
    walk_conttpc,
)

_ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8", errors="replace")


def _item_core_fields(it: Dict[str, Any]) -> Dict[str, Any]:
    """测试报告用字段：番号、标题、出演、大小、封面、种子。"""
    return {
        "code": it.get("code"),
        "title": it.get("title"),
        "actress": it.get("actress"),
        "size_gb": it.get("size_gb"),
        "img_url": it.get("poster_url"),
        "torrent_url": it.get("torrent_url"),
    }


def _print_sample_report(label: str, items: List[Dict[str, Any]], max_rows: int = 20) -> None:
    print(f"\n========== {label} ==========")
    for i, it in enumerate(items[:max_rows]):
        core = _item_core_fields(it)
        print(f"--- item[{i}] ---")
        for k, v in core.items():
            vstr = v if v is None else (v if len(str(v)) <= 120 else str(v)[:117] + "...")
            print(f"  {k}: {vstr!r}")
    if len(items) > max_rows:
        print(f"  ... 共 {len(items)} 条，仅展示前 {max_rows} 条 ---")


def _assert_item_has_media_fields(
    self: unittest.TestCase,
    it: Dict[str, Any],
    need_actress: bool,
    *,
    require_torrent: bool = True,
) -> None:
    self.assertIsNotNone(it.get("title"), "title 不能为空")
    self.assertTrue(str(it["title"]).strip(), "title 不能为空串")
    self.assertIsNotNone(it.get("size_gb"), "影片大小 size_gb 不能为空")
    self.assertIsNotNone(it.get("poster_url"), "封面 img_url(poster_url) 不能为空")
    self.assertIn("http", it["poster_url"] or "")
    if require_torrent:
        self.assertIsNotNone(it.get("torrent_url"), "种子 torrent_url 不能为空")
        self.assertIn("rmdown.com", it["torrent_url"] or "")
    if need_actress:
        self.assertIsNotNone(it.get("actress"), "出演 actress 不能为空")


class TestDetailParseOffline(unittest.TestCase):
    def test_actress_label_出演_vs_演出(self) -> None:
        """草榴部分帖用「出演女優」；旧逻辑只认「演出女優」会漏。"""
        p1 = "foo\n【出演女優】：三田真鈴\n【影片大小】：6.23GB"
        self.assertEqual(_actress_from_conttpc_plain(p1), "三田真鈴")
        p2 = "【演出女優】︰浅野こころ\n"
        self.assertEqual(_actress_from_conttpc_plain(p2), "浅野こころ")

    def test_actress_label_simplified_女优(self) -> None:
        """简体「女优」与繁体「女優」并存于草榴正文。"""
        p = "【出演女优】：輝星きら【影片容量】：8.72G"
        self.assertEqual(_actress_from_conttpc_plain(p), "輝星きら")

    def test_series_block_actress_simplified_女优(self) -> None:
        block = (
            "MIDA-573 标题行\n"
            "【出演女优】：輝星きら\n"
            "【影片大小】：8.72GB"
        )
        pb = _parse_series_block_text(block)
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertEqual(pb.get("actress"), "輝星きら")

    def test_actress_guess_from_film_name_juta_style(self) -> None:
        """无【出演女优】时从【影片名称】尾段猜（7253793 类帖子）。"""
        p = (
            "【影片名称】：[HD/3.82G]JUTA-182 極品！！三十路人妻初次脫衣AV紀錄片 "
            "平原舞衣[有碼高清中文字幕]【影片格式】：MP4"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "平原舞衣")

    def test_actress_tag_beats_film_name_guess(self) -> None:
        """结构化女优字段优先于片名启发式。"""
        p = (
            "【出演女優】：三田真鈴\n"
            "【影片名称】：[HD/3.82G]XXX-001 foo 平原舞衣[有碼]\n"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "三田真鈴")

    def test_actress_guess_from_h4_when_plain_has_no_field(self) -> None:
        """正文无出演/片名行时，用 h4 番号后标题尾段。"""
        h4 = "[有碼] [HD/5.1G]ATID-663 超長標題 栗山莉緒"
        self.assertEqual(_actress_from_conttpc_plain("只有大小\n【影片大小】：5.1GB", h4), "栗山莉緒")

    def test_actress_guess_role_dot_brackets_multi(self) -> None:
        """【角色・名】多段时合并（7253784 类）。"""
        p = (
            "【影片名称】：[HD/4.32G]JUR-555 對不倫的【妻子・環奈】和【對象・純】扭入雞巴"
            "[有碼高清中文字幕]【影片格式】：MP4"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "環奈、純")

    def test_actress_guess_latin_stage_name(self) -> None:
        """片名尾全大写拉丁艺名（如 JULIA）。"""
        p = "【影片名称】：[HD/3.53G]CJOD-480 魔性不倫女人 JULIA[有碼高清中文字幕]"
        self.assertEqual(_actress_from_conttpc_plain(p), "JULIA")

    def test_actress_sone329_no_style_false_positive(self) -> None:
        """新人 NO.1 STYLE 乃坂日和AV出道：勿把 STYLE 当艺名，须拆出乃坂日和。"""
        p = (
            "【影片名稱】：[MP4/ 1.82G] [中文字幕] SONE-329 新人NO.1 STYLE "
            "乃坂日和AV出道 S罩杯隱藏巨乳【格式類型】：MP4"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "乃坂日和")

    def test_actress_guess_none_for_narrative_title(self) -> None:
        """纯叙事标题、无明确姓名时不应误报（如 7253330 / 7253288）。"""
        p1 = (
            "【影片名稱】：[MP4/ 1.78G] [中文字幕] SGKI-027 太瘋狂了！目間新聞"
            "「ON AIR時表情也不會失去」可愛又可愛的電台播音員,第二年的專業精神"
            "【格式類型】：MP4"
        )
        self.assertIsNone(_actress_from_conttpc_plain(p1))
        p2 = (
            "【影片名稱】：[MP4/ 1.56G] [中文字幕] SCOP-865 都內某地域傳言中，有一位巨臀痴女在夜晚出沒，"
            "將男人們吃個精光!!【格式類型】：MP4"
        )
        self.assertIsNone(_actress_from_conttpc_plain(p2))

    def test_actress_chinese_title_field_snos_style(self) -> None:
        """【中文片名】行末尾中文名；日文《FHD中文》尾需去书名号。"""
        p = (
            "【影片名稱】：SNOS-192 紫堂るい《FHD中文》\n"
            "【中文片名】：這麼美的寫真偶像爆乳夾住你能拒絕嗎？ 紫堂留衣\n"
            "【影片大小】：3.94GB"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "紫堂留衣")

    def test_actress_chinese_title_beats_debut_in_japanese_film_name(self) -> None:
        """【中文片名】优先，勿把日文行里的 AV DEBUT 当拉丁艺名。"""
        p = (
            "【影片名稱】：ROE-497 稲森雅 42 歳 AV DEBUT 誕生《FHD中文》\n"
            "【中文片名】：北海道前地方臺主播人妻無盡淫欲AV出道 稲森雅\n"
        )
        self.assertEqual(_actress_from_conttpc_plain(p), "稲森雅")

    def test_h4_fullwidth_mp4_bracket_size(self) -> None:
        self.assertEqual(_h4_size_gb("【有碼】 【MP4/3.12GB】ROE-497 foo"), "3.12")

    def test_actress_skip_h4_when_plain_出演者_blank_dash(self) -> None:
        """出演者：---- 总集勿从 h4 猜名（如 BAZX BEST）。"""
        plain = (
            "出演者：----監督：X\n"
            "h4侧无中文片名时的 junk"
        )
        h4 = "[SD/2.6G] BAZX-413 青春性交 エモい制服美少女 BEST"
        self.assertIsNone(_actress_from_conttpc_plain(plain, h4))

    def test_size_gb_sd_mp4_inline_plain(self) -> None:
        from detail_parse import _size_gb_from_plain

        p = "foo SD-MP4-2.58GB https://rmdown.com/"
        self.assertEqual(_size_gb_from_plain(p), "2.58")

    def test_size_gb_mb_in_plain(self) -> None:
        from detail_parse import _size_gb_from_plain

        p = "【影片大小】：969.8MB\n"
        self.assertEqual(_size_gb_from_plain(p), "0.95")
        p2 = "【影片大小】：1,016MB\nfoo"
        self.assertEqual(_size_gb_from_plain(p2), "0.99")

    def test_actress_出演_colon_japanese_plain(self) -> None:
        """MGS 等帖正文「出演：福田もも」无「者」字。"""
        p = "foo\n出演：福田もも制作：DOC\n品番：MAAN-1167"
        self.assertEqual(_actress_from_conttpc_plain(p), "福田もも")

    def test_actress_出演者_placeholder_dash_treated_as_none(self) -> None:
        """DMM 总集「出演者：----」不得当地下库为女演员名字。"""
        p = "出演者：----監督：K太郎\n品番：h_565scop00592"
        self.assertIsNone(_actress_from_conttpc_plain(p))

    def test_actress_出演者_stops_before_supervisor_field(self) -> None:
        p = "出演者：山田花子監督：K太郎\n"
        self.assertEqual(_actress_from_conttpc_plain(p), "山田花子")

    def test_h4_bracket_sd_size(self) -> None:
        self.assertEqual(_h4_size_gb("[SD/1.5G] MAAN-1167 title"), "1.5")

    def test_signal_atid663(self) -> None:
        html = _read("detail-signal.html")
        r = parse_detail_page(html)
        self.assertEqual(r["style"], "signal")
        self.assertEqual(r.get("torrent_mode"), "single")
        self.assertEqual(len(r["items"]), 1)
        it = r["items"][0]
        self.assertEqual(it["code"], "ATID-663")
        self.assertEqual(it["size_gb"], "5.1")
        self.assertIn("工地", it["title"] or "")
        self.assertEqual(it.get("actress"), "栗山莉緒")
        _assert_item_has_media_fields(self, it, need_actress=True)
        _print_sample_report("detail-signal.html", r["items"])

    def test_xilie_series_one_torrent(self) -> None:
        html = _read("detail-xilie.html")
        _, _, rms = walk_conttpc(html)
        self.assertEqual(len(rms), 1)
        r = parse_detail_page(html)
        self.assertEqual(r["style"], "series_single_header")
        self.assertEqual(r.get("torrent_mode"), "one_torrent")
        self.assertEqual(len(r["items"]), 5)
        codes = [it["code"] for it in r["items"]]
        self.assertEqual(codes[0], "MIMK-194")
        self.assertEqual(r["items"][0]["size_gb"], "4.94")
        self.assertIn("浅野", r["items"][0].get("title") or "")
        self.assertEqual(r["items"][0].get("actress"), "浅野こころ")
        for it in r["items"]:
            self.assertEqual(it.get("torrent_url"), rms[0])
            _assert_item_has_media_fields(self, it, need_actress=False)
        self.assertIsNone(r["items"][1].get("actress"))
        self.assertEqual(r.get("errors"), [])
        _print_sample_report("detail-xilie.html", r["items"])

    def test_xilie2_multi_torrent_order(self) -> None:
        html = _read("detail-xilie2.html")
        _, _, rms = walk_conttpc(html)
        self.assertGreater(len(rms), 1)
        r = parse_detail_page(html)
        self.assertEqual(r["style"], "series_multi_header")
        self.assertEqual(r.get("torrent_mode"), "multi_torrent")
        self.assertGreaterEqual(len(r["items"]), 10)
        self.assertEqual(r["items"][0]["code"], "JUQ-629")
        self.assertEqual(r["items"][0]["size_gb"], "5.83")
        self.assertIn("市来", r["items"][0].get("title") or "")
        self.assertEqual(r["items"][0].get("actress"), "市来まひろ")
        self.assertIn("torrent_count_mismatch", "".join(r.get("errors") or []))
        for i, it in enumerate(r["items"]):
            if i < len(rms):
                self.assertEqual(it.get("torrent_url"), rms[i])
            # 样例 HTML 中影片段 11 段、rmdown 仅 10 个，最后一条可能无种子
            _assert_item_has_media_fields(
                self, it, need_actress=True, require_torrent=(i < len(rms))
            )
        _print_sample_report("detail-xilie2.html", r["items"])

    def test_report_core_fields_json_shape(self) -> None:
        """集中打印三份样本的字段摘要（含番号 code），便于肉眼核对。"""
        for fname in ("detail-signal.html", "detail-xilie.html", "detail-xilie2.html"):
            r = parse_detail_page(_read(fname))
            rows = [_item_core_fields(it) for it in r["items"]]
            print(f"\n>>> {fname} style={r.get('style')} items={len(rows)}")
            print(json.dumps(rows[:3], ensure_ascii=False, indent=2))
            if len(rows) > 3:
                print(f"... ({len(rows) - 3} more)")


if __name__ == "__main__":
    unittest.main()
