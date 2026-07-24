from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_assets import (
    find_existing_image,
    find_existing_torrent,
    is_valid_image_file,
    is_valid_torrent_file,
    resolve_asset_path,
)


class TestLocalAssets(unittest.TestCase):
    def test_valid_jpeg_magic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            self.assertTrue(is_valid_image_file(p))

    def test_empty_or_garbage_image_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.jpg"
            p.write_bytes(b"<html>not image</html>")
            self.assertFalse(is_valid_image_file(p))
            self.assertFalse(is_valid_image_file(Path(td) / "missing.jpg"))

    def test_torrent_nonempty_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.torrent"
            p.write_bytes(b"d4:infod4:name4:testee")
            self.assertTrue(is_valid_torrent_file(p))
            bad = Path(td) / "y.torrent"
            bad.write_bytes(b"")
            self.assertFalse(is_valid_torrent_file(bad))

    def test_find_existing_by_stem(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            img = d / "CODE-001.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            tor = d / "CODE-001.torrent"
            tor.write_bytes(b"d4:infod4:name4:testee")
            self.assertEqual(find_existing_image(d, "CODE-001"), img.resolve())
            self.assertEqual(find_existing_torrent(d, "CODE-001"), tor.resolve())

    def test_resolve_prefers_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            old = d / "old" / "CODE-001.jpg"
            old.parent.mkdir()
            old.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            # different day dir would not contain the file
            new_dir = d / "new"
            new_dir.mkdir()
            got = resolve_asset_path(
                db_path=str(old), save_dir=new_dir, stem="CODE-001", kind="image"
            )
            self.assertEqual(got, old.resolve())
