#!/usr/bin/env python3
"""本地图片/种子文件校验与下载前路径解析。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from image_download import _detect_image_ext


def is_valid_image_file(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    try:
        data = p.read_bytes()
    except OSError:
        return False
    if not data:
        return False
    return _detect_image_ext(data) is not None


def is_valid_torrent_file(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix.lower() != ".torrent":
        return False
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


def find_existing_image(save_dir: Path, stem: str) -> Optional[Path]:
    save_dir = Path(save_dir)
    if not save_dir.is_dir():
        return None
    # exact stem.* first, then stem_N.* leftovers from older runs
    candidates = sorted(save_dir.glob(f"{stem}.*")) + sorted(save_dir.glob(f"{stem}_*.*"))
    for p in candidates:
        if is_valid_image_file(p):
            return p.resolve()
    return None


def find_existing_torrent(save_dir: Path, stem: str) -> Optional[Path]:
    save_dir = Path(save_dir)
    exact = save_dir / f"{stem}.torrent"
    if is_valid_torrent_file(exact):
        return exact.resolve()
    if not save_dir.is_dir():
        return None
    for p in sorted(save_dir.glob(f"{stem}_*.torrent")):
        if is_valid_torrent_file(p):
            return p.resolve()
    return None


def resolve_asset_path(
    *,
    db_path: str | None,
    save_dir: Path,
    stem: str,
    kind: Literal["image", "torrent"],
) -> Optional[Path]:
    if db_path:
        checker = is_valid_image_file if kind == "image" else is_valid_torrent_file
        if checker(db_path):
            return Path(db_path).resolve()
    if kind == "image":
        return find_existing_image(save_dir, stem)
    return find_existing_torrent(save_dir, stem)
