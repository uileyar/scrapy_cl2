"""离线断言：detail-signal / detail-xilie / detail-xilie2 三种详情样式。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

from detail_parse import (
    _actress_from_conttpc_plain,
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
