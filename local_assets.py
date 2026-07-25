#!/usr/bin/env python3
"""本地图片/种子文件校验与下载前路径解析。"""
from __future__ import annotations

from glob import escape as glob_escape
from pathlib import Path
from typing import Literal, Optional

from image_download import _detect_image_ext


def asset_path_on_disk(path: str | Path | None) -> bool:
    """轻量存在性检查（建队列用，不读文件内容）。"""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def is_valid_image_file(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    try:
        with p.open("rb") as f:
            data = f.read(64)
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


def iter_stem_paths(save_dir: Path, stem: str) -> list[Path]:
    """列出 stem.* / stem_*.*，对 [ ] 等 glob 元字符做转义。"""
    save_dir = Path(save_dir)
    if not save_dir.is_dir():
        return []
    esc = glob_escape(stem)
    # exact first, then numbered leftovers from older collision runs
    seen: set[Path] = set()
    out: list[Path] = []
    for p in list(save_dir.glob(f"{esc}.*")) + list(save_dir.glob(f"{esc}_*.*")):
        rp = p.resolve() if p.exists() else p
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def find_existing_image(save_dir: Path, stem: str) -> Optional[Path]:
    for p in iter_stem_paths(save_dir, stem):
        if p.suffix.lower() == ".torrent":
            continue
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
    esc = glob_escape(stem)
    for p in sorted(save_dir.glob(f"{esc}_*.torrent")):
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
