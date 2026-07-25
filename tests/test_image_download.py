from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from image_download import download_image_from_url


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


if __name__ == "__main__":
    unittest.main()
