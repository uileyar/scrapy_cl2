#!/usr/bin/env python3
"""
遍历配置盘符下 HTDDOWNLOAD，将图片/视频索引写入 SQLite 表 local_items。
每个盘符：先 DELETE root=该盘，再批量 INSERT（无命令行参数，盘符与库路径在下方配置）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

# ---------- 可配置 ----------
# 按顺序处理；仅当「盘符:\HTDDOWNLOAD」存在时才扫描
DRIVE_LETTERS: list[str] = ["H", "I", "J", "k", "L", "T", "U"]
HTDDOWNLOAD_DIRNAME = "HTDDOWNLOAD"
# 与 crawl_html 默认一致，可按需修改
DB_PATH = Path("D:/data/cl_db/cl.db")

# 直接父目录名为以下之一，或「纯数字」目录名时：actress 为空，code=文件名（无后缀）
# 另：文件名（无后缀）与父目录名相同（如 temp\JUR-717\JUR-717.jpg）时：actress 为空，code=该文件名（无后缀）
SPECIAL_PARENT_NAMES: frozenset[str] = frozenset(
    name.casefold()
    for name in ("PICPIC", "done", "temp", "FINFINFINFIN", "传媒", "小视频")
)

_IMG_EXT = frozenset(
    ".jpg .jpeg .png .gif .webp .bmp .tif .tiff .heic .heif .avif .jxl".split()
)
_VIDEO_EXT = frozenset(
    ".mp4 .mkv .avi .mov .wmv .flv .webm .m4v .ts .m2ts .mpg .mpeg .rmvb .rm .3gp .asf".split()
)

_CREATE_LOCAL_ITEMS = """
CREATE TABLE IF NOT EXISTS local_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root TEXT NOT NULL,
    actress TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL,
    file_type TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    modify_time TEXT NOT NULL
);
"""

_INSERT_SQL = """
INSERT INTO local_items (root, actress, code, file_type, path, size, modify_time)
VALUES (?, ?, ?, ?, ?, ?, ?);
"""


def _ensure_local_items_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_LOCAL_ITEMS)
    conn.commit()


def _file_type_for_path(path: Path) -> str | None:
    ext = path.suffix.casefold()
    if ext in _IMG_EXT:
        return "img"
    if ext in _VIDEO_EXT:
        return "video"
    return None


def _parent_is_special_or_numeric(parent_name: str) -> bool:
    if parent_name.casefold() in SPECIAL_PARENT_NAMES:
        return True
    return parent_name.isdigit()


def _actress_and_code(path: Path) -> tuple[str, str]:
    parent_name = path.parent.name
    stem = path.stem
    if _parent_is_special_or_numeric(parent_name):
        return "", stem
    if stem.casefold() == parent_name.casefold():
        return "", stem
    return parent_name, stem


def _prune_bea(dirs: list[str]) -> None:
    dirs[:] = [d for d in dirs if d.casefold() != "bea"]


def _iter_media_under_htdownload(letter: str, base: Path):
    """yield (letter, path, file_type)；跳过名为 bea 的子目录。"""
    base_str = os.fspath(base)
    for dirpath, dirnames, filenames in os.walk(base_str, topdown=True):
        _prune_bea(dirnames)
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            ft = _file_type_for_path(p)
            if ft is None:
                continue
            yield letter, p, ft


def _mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def sync_drive(conn: sqlite3.Connection, root_letter: str) -> int:
    letter = root_letter.rstrip(":").upper()
    drive_root = Path(f"{letter}:/")
    ht = drive_root / HTDDOWNLOAD_DIRNAME
    if not ht.is_dir():
        return 0

    conn.execute("DELETE FROM local_items WHERE root = ?", (letter,))
    count = 0
    batch: list[tuple] = []
    batch_size = 500

    for _, p, ft in _iter_media_under_htdownload(letter, ht):
        try:
            st = p.stat()
        except OSError:
            continue
        actress, code = _actress_and_code(p)
        row = (
            letter,
            actress,
            code,
            ft,
            str(p.resolve()),
            int(st.st_size),
            _mtime_iso(p),
        )
        batch.append(row)
        count += 1
        if len(batch) >= batch_size:
            conn.executemany(_INSERT_SQL, batch)
            batch.clear()

    if batch:
        conn.executemany(_INSERT_SQL, batch)
    conn.commit()
    return count


_BYTES_PER_GB = 1024.0**3


def _char_display_width(ch: str) -> int:
    """终端里全角字符占 2 列，半角占 1 列（与常见等宽字体一致）。"""
    if unicodedata.east_asian_width(ch) in ("F", "W"):
        return 2
    return 1


def _display_width(s: str) -> int:
    return sum(_char_display_width(c) for c in s)


def _truncate_display(s: str, max_w: int) -> str:
    if _display_width(s) <= max_w:
        return s
    out: list[str] = []
    w = 0
    for c in s:
        cw = _char_display_width(c)
        if w + cw > max_w - 1:
            break
        out.append(c)
        w += cw
    return "".join(out) + "…"


def _ljust_display(s: str, col_w: int) -> str:
    s = _truncate_display(s, col_w)
    pad = col_w - _display_width(s)
    return s + " " * max(0, pad)


def _rjust_display(s: str, col_w: int) -> str:
    pad = col_w - _display_width(s)
    return " " * max(0, pad) + s


def print_actress_video_stats(conn: sqlite3.Connection) -> None:
    """有演员名的 video：按占用取 TOP50，输出条数与总字节（GB）。"""
    rows = conn.execute(
        """
        SELECT actress, COUNT(*) AS cnt, COALESCE(SUM(size), 0) AS total_bytes
        FROM local_items
        WHERE file_type = 'video' AND TRIM(actress) != ''
        GROUP BY actress
        ORDER BY total_bytes DESC
        LIMIT 50
        """
    ).fetchall()
    name_w, cnt_w, gb_w = 42, 8, 12
    gap = 2
    sep_len = name_w + gap + cnt_w + gap + gb_w

    print()
    print("=== 各演员 video TOP50（GB，按占用降序）===")
    hdr1 = _ljust_display("演员", name_w)
    hdr2 = _rjust_display("影片数", cnt_w)
    hdr3 = _rjust_display("占用(GB)", gb_w)
    print(f"{hdr1}{' ' * gap}{hdr2}{' ' * gap}{hdr3}")
    total_cnt = 0
    total_bytes = 0
    for actress, cnt, tb in rows:
        total_cnt += cnt
        total_bytes += tb
        gb = tb / _BYTES_PER_GB
        c1 = _ljust_display(actress, name_w)
        c2 = _rjust_display(str(cnt), cnt_w)
        c3 = _rjust_display(f"{gb:.2f}", gb_w)
        print(f"{c1}{' ' * gap}{c2}{' ' * gap}{c3}")
    print("-" * sep_len)
    t1 = _ljust_display("合计", name_w)
    t2 = _rjust_display(str(total_cnt), cnt_w)
    t3 = _rjust_display(f"{total_bytes / _BYTES_PER_GB:.2f}", gb_w)
    print(f"{t1}{' ' * gap}{t2}{' ' * gap}{t3}")


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    with closing(conn):
        _ensure_local_items_table(conn)
        for letter in DRIVE_LETTERS:
            n = sync_drive(conn, letter)
            print(f"[{letter}] HTDDOWNLOAD -> local_items 写入 {n} 条")
        print_actress_video_stats(conn)


if __name__ == "__main__":
    main()
