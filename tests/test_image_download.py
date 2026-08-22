from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from image_download import _replace_host, download_image_from_url


class TestImageDownload(unittest.TestCase):
    def test_reuses_existing_valid_image_instead_of_suffix(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            existing = out / "stem.jpg"
            existing.write_bytes(jpeg)

            with mock.patch(
                "image_download._fetch",
                return_value=(jpeg, None, "http://x/a.jpg", "image/jpeg"),
            ):
                got = download_image_from_url(
                    "http://x/a.jpg", out, filename="stem"
                )

            self.assertEqual(got, existing.resolve())
            self.assertFalse((out / "stem_1.jpg").exists())

    def test_pkapic_host_maps_to_odjsk(self) -> None:
        url = "https://pkapic.cc/upload/files/2026/08/10/x.jpg"
        self.assertEqual(
            _replace_host(url),
            "https://odjsk.com/upload/files/2026/08/10/x.jpg",
        )

    def test_pkapic_html_fallback_uses_odjsk(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        html = b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\r'
        calls: list[str] = []

        def fake_do_fetch(url: str, headers: dict) -> tuple:
            calls.append(url)
            if "odjsk.com" in url:
                return jpeg, None, url, "image/jpeg"
            if url.startswith("http://"):
                return html, None, url, "text/html"
            raise OSError("ssl eof")

        with tempfile.TemporaryDirectory() as td:
            with mock.patch("image_download._do_fetch", side_effect=fake_do_fetch):
                got = download_image_from_url(
                    "https://pkapic.cc/upload/files/2026/08/10/x.jpg",
                    td,
                    filename="x",
                )
            self.assertTrue(got.is_file())
            self.assertEqual(got.read_bytes()[:3], b"\xff\xd8\xff")
            self.assertTrue(any("odjsk.com" in u for u in calls))


if __name__ == "__main__":
    unittest.main()
