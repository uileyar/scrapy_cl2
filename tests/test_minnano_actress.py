from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crawl_to_sqlite import ensure_db, replace_minnano_actress_from_csv
from get_minnano_actress import parse_list_html, parse_name_cell, write_csv, ActressRow


SAMPLE_HTML = """
<html><body>
<table class="tbllist actress">
<tr><th>名前</th><th>AV作品数</th></tr>
<tr>
  <td><a href="actress82790.html?朝比奈紗良"><img src="p_actress_125_125/790/82790.jpg" alt="朝比奈紗良"></a></td>
  <td class="details">
    <h2 class="ttl"><a href="actress82790.html?朝比奈紗良">朝比奈紗良</a></h2>
    <p class="furi">あさひなさら / Asahina Sara</p>
    <p class="debut-info">2026年08月デビュー</p>
  </td>
  <td>1</td>
</tr>
<tr>
  <td><a href="actress123.html"><img src="/img/no.jpg"></a></td>
  <td class="details">
    <h2 class="ttl"><a href="actress123.html">木村愛心</a></h2>
    <p class="furi">きむらあいしん / Kimura Aishin</p>
  </td>
  <td>12</td>
</tr>
</table>
<a href="/actress_list.php?gojuon=a&page=2">2</a>
<a href="/actress_list.php?gojuon=a&page=84">84</a>
</body></html>
"""


class TestMinnanoParse(unittest.TestCase):
    def test_parse_name_cell(self) -> None:
        p = parse_name_cell(
            "朝比奈紗良 あさひなさら / Asahina Sara 2026年08月デビュー"
        )
        self.assertEqual(p, ("朝比奈紗良", "あさひなさら", "Asahina Sara"))

    def test_filter_short_ja_name(self) -> None:
        self.assertIsNone(parse_name_cell("あ ああ / Aa 2020年01月デビュー"))
        self.assertIsNone(parse_name_cell("A ああ / Aa 2020年01月デビュー"))

    def test_parse_list_html_skips_short_ja(self) -> None:
        html = SAMPLE_HTML.replace(
            "</table>",
            """
<tr>
  <td><a href="actress9.html"><img src="/x.jpg"></a></td>
  <td class="details">
    <h2 class="ttl"><a href="actress9.html">あ</a></h2>
    <p class="furi">あ / A</p>
  </td>
  <td>1</td>
</tr>
</table>
""",
        )
        rows, _ = parse_list_html(html)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r.ja_name) >= 2 for r in rows))

    def test_parse_list_html(self) -> None:
        rows, max_page = parse_list_html(SAMPLE_HTML)
        self.assertEqual(max_page, 84)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source_id, "82790")
        self.assertEqual(rows[0].ja_name, "朝比奈紗良")
        self.assertEqual(rows[0].kana, "あさひなさら")
        self.assertEqual(rows[0].romaji, "Asahina Sara")
        self.assertEqual(
            rows[0].detail_url, "https://www.minnano-av.com/actress82790.html"
        )
        self.assertEqual(
            rows[0].img_url,
            "https://www.minnano-av.com/p_actress_125_125/790/82790.jpg",
        )
        self.assertEqual(rows[0].debut_text, "2026年08月デビュー")
        self.assertEqual(rows[1].ja_name, "木村愛心")
        self.assertEqual(rows[1].works_count, 12)
        self.assertEqual(rows[1].debut_text, "")
        self.assertEqual(
            rows[1].img_url, "https://www.minnano-av.com/img/no.jpg"
        )

    def test_replace_minnano_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "m.csv"
            write_csv(
                csv_path,
                [
                    ActressRow(
                        "1",
                        "朝比奈紗良",
                        "あさひなさら",
                        "Asahina Sara",
                        1,
                        "https://www.minnano-av.com/actress1.html",
                        "https://www.minnano-av.com/a.jpg",
                        "2026年08月デビュー",
                    ),
                    ActressRow(
                        "2",
                        "木村愛心",
                        "きむらあいしん",
                        "Kimura Aishin",
                        3,
                        "https://www.minnano-av.com/actress2.html",
                        "",
                        "",
                    ),
                ],
            )
            conn = ensure_db(root / "t.db")
            try:
                n = replace_minnano_actress_from_csv(conn, csv_path)
                self.assertEqual(n, 2)
                row = conn.execute(
                    "SELECT ja_name, kana, romaji, detail_url, img_url, debut_text "
                    "FROM actress_names_minnano WHERE source_id='1'"
                ).fetchone()
                self.assertEqual(
                    row,
                    (
                        "朝比奈紗良",
                        "あさひなさら",
                        "Asahina Sara",
                        "https://www.minnano-av.com/actress1.html",
                        "https://www.minnano-av.com/a.jpg",
                        "2026年08月デビュー",
                    ),
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
