# Download Resume & Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the crawl pipeline resume interrupted threads and retry missing image/torrent downloads without re-downloading existing local files or filtered-out items.

**Architecture:** Keep download protocols unchanged. Add a small `local_assets.py` for local file validation/precheck, extend `crawl_to_sqlite.py` with read APIs for pending/missing assets, and rework `crawl_html.py` queue building + done gating (`full` vs `patch` modes).

**Tech Stack:** Python 3, stdlib (`pathlib`, `sqlite3`, `unittest`), existing `image_download` / `rmdown_download`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-download-resume-retry-design.md`
- Do not change `image_download` / `rmdown_download` download protocols (may call them; may delete corrupt local files before calling).
- No new item status / `skip_reason` columns.
- Size filter remains `size_gb <= 1.5` (skip: no upsert, no download).
- Filtered items never enter patch queue (only already-upserted items).
- Mark thread `done` only when every upserted item with a URL has a valid local file for that asset.
- Before any image/torrent network download: check DB path, then target path; skip if valid local file exists.
- Corrupt local target file: delete then re-download (avoid `_1`/`_2` pileup).

---

## File Structure

- Create: `local_assets.py`
  - Image/torrent local validity checks and “resolve existing path” helpers used before download.
- Modify: `crawl_to_sqlite.py`
  - Add `list_pending_threads`, `list_thread_urls_needing_asset_check`, `list_items_for_thread`, and a pure `item_assets_complete` / `thread_assets_complete` helper layer.
- Modify: `crawl_html.py`
  - Build merged queue (list + pending + missing); `full` / `patch` processing; local precheck; fix torrent-fail `continue`; status = done only when assets complete.
- Create: `tests/test_local_assets.py`
- Create: `tests/test_crawl_to_sqlite_resume.py`
- Create: `tests/test_crawl_pipeline_resume.py`
  - Unit tests with temp DB/dirs and mocked network download functions.

---

### Task 1: Local asset validation helpers

**Files:**
- Create: `local_assets.py`
- Create: `tests/test_local_assets.py`

**Interfaces:**
- Produces:
  - `is_valid_image_file(path: str | Path) -> bool`
  - `is_valid_torrent_file(path: str | Path) -> bool`
  - `find_existing_image(save_dir: Path, stem: str) -> Path | None`
  - `find_existing_torrent(save_dir: Path, stem: str) -> Path | None`
  - `resolve_asset_path(*, db_path: str | None, save_dir: Path, stem: str, kind: Literal["image", "torrent"]) -> Path | None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_local_assets.py
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
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m unittest tests.test_local_assets -v`  
Expected: FAIL (module / functions missing)

- [ ] **Step 3: Implement `local_assets.py`**

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m unittest tests.test_local_assets -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add local_assets.py tests/test_local_assets.py
git commit -m "feat: add local image/torrent asset precheck helpers"
```

---

### Task 2: SQLite resume/query APIs

**Files:**
- Modify: `crawl_to_sqlite.py`
- Create: `tests/test_crawl_to_sqlite_resume.py`

**Interfaces:**
- Consumes: existing `ensure_db`, `upsert_thread`, `upsert_item`, `update_thread_status`
- Produces:
  - `list_pending_threads(conn) -> list[dict]`  # keys: url, title, downloads, source, status
  - `list_items_for_thread(conn, thread_url: str) -> list[dict]`
  - `list_candidate_missing_asset_rows(conn) -> list[dict]`  # SQL-side NULL/empty path + non-empty URL
  - `item_needs_asset_retry(item: dict) -> bool`  # Python-side file existence via local_assets
  - `thread_assets_complete(conn, thread_url: str) -> bool`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_crawl_to_sqlite_resume.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crawl_to_sqlite import (
    ensure_db,
    item_needs_asset_retry,
    list_candidate_missing_asset_rows,
    list_items_for_thread,
    list_pending_threads,
    thread_assets_complete,
    update_thread_status,
    upsert_item,
    upsert_thread,
)


class TestCrawlToSqliteResume(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = ensure_db(self.db)
        self.files = Path(self.tmp.name) / "files"
        self.files.mkdir()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_list_pending_threads(self) -> None:
        upsert_thread(self.conn, "http://a", "A", status="pending")
        upsert_thread(self.conn, "http://b", "B", status="done")
        rows = list_pending_threads(self.conn)
        self.assertEqual([r["url"] for r in rows], ["http://a"])

    def test_missing_when_path_null(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg", img_path=None,
            torrent_url="http://rm/1", torrent_path=None,
        )
        cands = list_candidate_missing_asset_rows(self.conn)
        self.assertTrue(any(r["thread_url"] == "http://t" for r in cands))
        items = list_items_for_thread(self.conn, "http://t")
        self.assertTrue(item_needs_asset_retry(items[0]))
        self.assertFalse(thread_assets_complete(self.conn, "http://t"))

    def test_complete_when_files_exist_and_no_url_ok(self) -> None:
        img = self.files / "x.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        tor = self.files / "x.torrent"
        tor.write_bytes(b"d4:infod4:name4:testee")
        upsert_thread(self.conn, "http://t", "T", status="pending")
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg", img_path=str(img),
            torrent_url="http://rm/1", torrent_path=str(tor),
        )
        upsert_item(
            self.conn, "http://t", "CODE-2",
            img_url=None, img_path=None,
            torrent_url=None, torrent_path=None,
        )
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        self.assertFalse(item_needs_asset_retry(list_items_for_thread(self.conn, "http://t")[0]))

    def test_path_set_but_file_missing_needs_retry(self) -> None:
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg",
            img_path=str(self.files / "gone.jpg"),
            torrent_url=None, torrent_path=None,
        )
        item = list_items_for_thread(self.conn, "http://t")[0]
        self.assertTrue(item_needs_asset_retry(item))
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m unittest tests.test_crawl_to_sqlite_resume -v`  
Expected: FAIL (functions missing)

- [ ] **Step 3: Implement APIs in `crawl_to_sqlite.py`**

Add imports and functions (append near existing API; keep `thread_exists` behavior unchanged):

```python
from local_assets import is_valid_image_file, is_valid_torrent_file


def list_pending_threads(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT url, title, downloads, source, status FROM threads WHERE status = 'pending'"
    ).fetchall()
    return [dict(r) for r in rows]


def list_items_for_thread(conn: sqlite3.Connection, thread_url: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, thread_url, code, code_title, title_transfer, actress, size_gb,
               img_url, img_path, torrent_url, torrent_path, source
        FROM items WHERE thread_url = ?
        """,
        (thread_url,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_candidate_missing_asset_rows(conn: sqlite3.Connection) -> list[dict]:
    """SQL candidates: has URL and (path NULL/empty). File-missing-with-path checked in Python."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT thread_url, code, img_url, img_path, torrent_url, torrent_path
        FROM items
        WHERE
          (img_url IS NOT NULL AND img_url != '' AND (img_path IS NULL OR img_path = ''))
          OR
          (torrent_url IS NOT NULL AND torrent_url != ''
           AND (torrent_path IS NULL OR torrent_path = ''))
        """
    ).fetchall()
    return [dict(r) for r in rows]


def item_needs_asset_retry(item: dict) -> bool:
    img_url = (item.get("img_url") or "").strip()
    tor_url = (item.get("torrent_url") or "").strip()
    if img_url and not is_valid_image_file(item.get("img_path") or ""):
        return True
    if tor_url and not is_valid_torrent_file(item.get("torrent_path") or ""):
        return True
    return False


def thread_assets_complete(conn: sqlite3.Connection, thread_url: str) -> bool:
    items = list_items_for_thread(conn, thread_url)
    if not items:
        return False
    return not any(item_needs_asset_retry(it) for it in items)
```

Also add a convenience used by queue building:

```python
def list_thread_urls_with_missing_assets(conn: sqlite3.Connection) -> list[str]:
    urls: set[str] = set()
    for row in list_candidate_missing_asset_rows(conn):
        urls.add(row["thread_url"])
    # also scan items that have path set but file gone
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT thread_url, img_url, img_path, torrent_url, torrent_path FROM items
        WHERE (img_url IS NOT NULL AND img_url != '' AND img_path IS NOT NULL AND img_path != '')
           OR (torrent_url IS NOT NULL AND torrent_url != ''
               AND torrent_path IS NOT NULL AND torrent_path != '')
        """
    ).fetchall()
    for r in rows:
        if item_needs_asset_retry(dict(r)):
            urls.add(r["thread_url"])
    return sorted(urls)
```

**Note:** `ensure_db` / callers may reset `row_factory`; after these helpers, either restore `conn.row_factory = None` or document that callers tolerate `Row`. Prefer restoring at end of each helper: `conn.row_factory = None`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m unittest tests.test_crawl_to_sqlite_resume -v`  
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add crawl_to_sqlite.py tests/test_crawl_to_sqlite_resume.py
git commit -m "feat: add sqlite APIs for pending and missing assets"
```

---

### Task 3: Queue merge + full/patch processing in `crawl_html.py`

**Files:**
- Modify: `crawl_html.py`
- Create: `tests/test_crawl_pipeline_resume.py`

**Interfaces:**
- Consumes: Task 1 + Task 2 APIs; `download_image_from_url`; `download_from_rmdown_url`
- Produces:
  - `_build_work_queue(...)` → `list[dict]` with keys `url`, `title`, `downloads`, `mode` (`full`|`patch`)
  - `_ensure_asset(...)` → local path or None (precheck then download)
  - `_process_thread_full` / `_process_thread_patch`
  - `crawl_pipeline` uses queue; never marks `done` unless `thread_assets_complete`

- [ ] **Step 1: Write failing pipeline tests (mocked downloads)**

```python
# tests/test_crawl_pipeline_resume.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crawl_to_sqlite import (
    ensure_db,
    list_items_for_thread,
    thread_assets_complete,
    update_thread_status,
    upsert_item,
    upsert_thread,
)
from crawl_html import _build_work_queue, _process_thread_patch, crawl_pipeline


class TestCrawlPipelineResume(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "cl.db"
        self.dl = self.root / "dl"
        self.dl.mkdir()
        self.conn = ensure_db(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_queue_includes_pending_not_on_list(self) -> None:
        upsert_thread(self.conn, "http://old", "Old", downloads=1, status="pending")
        q = _build_work_queue(
            conn=self.conn,
            list_threads=[],  # nothing from list pages
            source="zz",
        )
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["url"], "http://old")
        self.assertEqual(q[0]["mode"], "full")

    def test_queue_patch_for_done_missing_img(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn, "http://t", "C1", code_title="C1 Title",
            img_url="http://img/1.jpg", img_path=None,
            torrent_url=None, torrent_path=None,
        )
        q = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        self.assertEqual(q[0]["mode"], "patch")
        self.assertEqual(q[0]["url"], "http://t")

    def test_patch_downloads_only_missing_and_skips_existing(self) -> None:
        files = self.root / "assets"
        files.mkdir()
        tor = files / "C1 Title.torrent"
        tor.write_bytes(b"d4:infod4:name4:testee")
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn, "http://t", "C1", code_title="C1 Title",
            img_url="http://img/1.jpg", img_path=None,
            torrent_url="http://rm/1", torrent_path=str(tor),
        )

        def fake_img(url, out_dir, filename=None, **kw):
            p = Path(out_dir) / f"{filename}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            return p.resolve()

        with mock.patch("crawl_html.download_image_from_url", side_effect=fake_img) as m_img, \
             mock.patch("crawl_html.download_from_rmdown_url") as m_tor:
            _process_thread_patch(
                conn=self.conn,
                thread_url="http://t",
                title="T",
                save_dir=files,
                source="zz",
                delay_sec=0,
            )
            m_img.assert_called_once()
            m_tor.assert_not_called()
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        items = list_items_for_thread(self.conn, "http://t")
        self.assertTrue(items[0]["img_path"])

    def test_torrent_fail_still_upserts_keeps_pending(self) -> None:
        """Regression: torrent error must not skip upsert_item; thread stays pending."""
        upsert_thread(self.conn, "http://t", "T", status="pending")

        def boom(*a, **k):
            raise RuntimeError("rmdown down")

        with mock.patch("crawl_html.fetch_and_parse_detail", return_value=[{
                "code": "C1",
                "code_title": "C1 Title",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": "http://rm/1",
            }]), \
             mock.patch("crawl_html.download_from_rmdown_url", side_effect=boom), \
             mock.patch("crawl_html.translate_code_title", return_value=None):
            from crawl_html import _process_thread_full
            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )
        items = list_items_for_thread(self.conn, "http://t")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["torrent_path"])
        row = self.conn.execute(
            "SELECT status FROM threads WHERE url=?", ("http://t",)
        ).fetchone()
        self.assertEqual(row[0], "pending")
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m unittest tests.test_crawl_pipeline_resume -v`  
Expected: FAIL (`_build_work_queue` / `_process_thread_patch` missing)

- [ ] **Step 3: Implement helpers and rewire `crawl_pipeline`**

Core logic to add/replace in `crawl_html.py` (keep existing sanitize helpers):

```python
from local_assets import resolve_asset_path, is_valid_image_file, is_valid_torrent_file
from crawl_to_sqlite import (
    list_pending_threads,
    list_thread_urls_with_missing_assets,
    list_items_for_thread,
    thread_assets_complete,
    item_needs_asset_retry,
    # ... existing imports
)

def _build_work_queue(*, conn, list_threads: list[dict], source: str) -> list[dict]:
    """Merge list + pending + missing; full wins over patch; list row wins title/downloads."""
    by_url: dict[str, dict] = {}
    for row in list_threads:
        by_url[row["url"]] = {
            "url": row["url"],
            "title": row.get("title") or "",
            "downloads": row.get("downloads") or 0,
            "mode": "full",
        }
    for row in list_pending_threads(conn):
        if row["url"] in by_url:
            continue
        by_url[row["url"]] = {
            "url": row["url"],
            "title": row.get("title") or "",
            "downloads": row.get("downloads") or 0,
            "mode": "full",
        }
    for url in list_thread_urls_with_missing_assets(conn):
        if url in by_url:
            continue
        # load title from threads table
        t = conn.execute(
            "SELECT url, title, downloads FROM threads WHERE url = ?", (url,)
        ).fetchone()
        if not t:
            continue
        by_url[url] = {
            "url": t[0],
            "title": t[1] or "",
            "downloads": t[2] or 0,
            "mode": "patch",
        }
    return list(by_url.values())


def _ensure_image(img_url, img_path_db, save_dir, safe_name) -> str | None:
    if not img_url:
        return img_path_db if is_valid_image_file(img_path_db or "") else None
    existing = resolve_asset_path(
        db_path=img_path_db, save_dir=save_dir, stem=safe_name, kind="image"
    )
    if existing:
        return str(existing)
    # remove corrupt target candidates with exact stem before download
    for p in Path(save_dir).glob(f"{safe_name}.*"):
        if p.is_file() and not is_valid_image_file(p):
            p.unlink(missing_ok=True)
    try:
        p = download_image_from_url(img_url, save_dir, filename=safe_name)
        return str(p)
    except Exception as e:
        log.error("  IMG-ERR %s: %s", img_url, e)
        return None


def _ensure_torrent(torrent_url, torrent_path_db, save_dir, safe_name) -> str | None:
    if not torrent_url:
        return torrent_path_db if is_valid_torrent_file(torrent_path_db or "") else None
    existing = resolve_asset_path(
        db_path=torrent_path_db, save_dir=save_dir, stem=safe_name, kind="torrent"
    )
    if existing:
        return str(existing)
    target = Path(save_dir) / f"{safe_name}.torrent"
    if target.is_file() and not is_valid_torrent_file(target):
        target.unlink(missing_ok=True)
    try:
        p = download_from_rmdown_url(torrent_url, save_dir, filename=safe_name)
        return str(p)
    except Exception as e:
        log.error("  TORRENT-ERR %s: %s", torrent_url, e)
        return None
```

`_process_thread_full`: same flow as current loop, but:
1. Load existing DB item paths when present (`list_items_for_thread` keyed by code) to feed precheck.
2. Use `_ensure_image` / `_ensure_torrent` instead of raw download + `continue`.
3. Always `upsert_item` even if torrent failed.
4. End with:

```python
if thread_assets_complete(conn, detail_url):
    update_thread_status(conn, detail_url, "done")
else:
    update_thread_status(conn, detail_url, "pending")
```

`_process_thread_patch`:
1. `save_dir`: prefer parent of any existing valid path on items; else `day_dir` (today/source). For multi-item with subdir, if any `img_path`/`torrent_path` parent exists use that directory; else `day_dir / sanitize(title)` when `len(items)>1` else `day_dir`.
2. For each item where `item_needs_asset_retry(item)`: call `_ensure_*` only for missing sides; `upsert_item` with new paths.
3. Same done/pending gate.

Rewire `crawl_pipeline`:
1. Collect `list_threads` from pages (unchanged filter via `thread_exists`).
2. `work = _build_work_queue(conn=conn, list_threads=all_threads, source=source)`.
3. Upsert all `full` mode entries as `pending` (patch entries already in DB).
4. For each work item: dispatch full/patch.

- [ ] **Step 4: Run tests — expect PASS**

Run:

```bash
python -m unittest tests.test_local_assets tests.test_crawl_to_sqlite_resume tests.test_crawl_pipeline_resume -v
```

Expected: OK

- [ ] **Step 5: Commit**

```bash
git add crawl_html.py tests/test_crawl_pipeline_resume.py
git commit -m "feat: resume pending threads and retry missing assets"
```

---

### Task 4: Full regression + manual smoke checklist

**Files:**
- None required (verification only); fix any regressions found in previous tasks.

- [ ] **Step 1: Run all unit tests**

```bash
python -m unittest discover -s tests -v
```

Expected: OK (including existing `tests.test_detail_parse`)

- [ ] **Step 2: Spec coverage smoke (manual checklist against a throwaway DB copy)**

Do **not** write to production DB. Copy `cl.db` if verifying manually.

1. Insert a `pending` thread with URL not on list → run pipeline with `max_pages=0` or mock empty list → thread still processed (`full`).
2. `done` thread with `img_url` set / `img_path` NULL → enters `patch`, downloads image, becomes `done`.
3. Existing valid local torrent path → no `download_from_rmdown_url` call.
4. Item with no `img_url` → does not block `done`.
5. `size_gb=1.0` item in detail → not upserted; does not appear in missing-asset query.

- [ ] **Step 3: Commit any fixes** (only if fixes were needed)

```bash
git add -u
git commit -m "fix: address resume/retry regression findings"
```

If nothing to fix, skip commit.

---

## Spec Coverage Checklist (plan self-review)

| Spec requirement | Task |
|---|---|
| Resume DB `pending` not on list | Task 3 `_build_work_queue` |
| Retry done/missing assets | Task 2 + Task 3 `patch` |
| Local precheck before download | Task 1 + Task 3 `_ensure_*` |
| No img/torrent URL does not block done | Task 2 `thread_assets_complete` |
| Filter `size<=1.5` not upserted / not patched | Task 3 full filter + patch only DB rows |
| Torrent fail still upserts; not forever done | Task 3 (remove `continue`) |
| Corrupt file re-download | Task 3 delete then download |
| No protocol changes to download modules | Global constraint |
| No new status columns | Global constraint |

## Placeholder / consistency notes

- Function names locked: `list_pending_threads`, `list_thread_urls_with_missing_assets`, `list_items_for_thread`, `item_needs_asset_retry`, `thread_assets_complete`, `_build_work_queue`, `_process_thread_full`, `_process_thread_patch`, `_ensure_image`, `_ensure_torrent`.
- `list_candidate_missing_asset_rows` is internal SQL helper used by `list_thread_urls_with_missing_assets` / tests.
