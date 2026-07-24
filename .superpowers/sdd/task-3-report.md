# Task 3 Report: Queue Merge and Resume Processing

## Implemented

- Added `_build_work_queue` to merge new list rows, pending threads, and completed threads with missing assets. Full processing takes precedence over patch processing, and list-row metadata wins.
- Added local asset prechecks before downloads. Valid database or same-stem local files are reused; corrupt image/torrent targets are removed before the existing download APIs are called.
- Split detail work into full and patch processors. Full mode retains the `size_gb <= 1.5` filter and always upserts eligible parsed items, including when torrent retrieval fails. Patch mode only retries missing assets for existing items.
- Thread status now becomes `done` only when `thread_assets_complete` is true; all incomplete, empty, or failed detail processing remains `pending`.
- Patch mode chooses an existing valid asset's parent directory where possible, otherwise follows the existing single-/multi-item directory layout.

## Tests

Added `tests/test_crawl_pipeline_resume.py` covering:

1. Pending threads not present on current list pages enter the full queue.
2. Completed threads with a missing image enter the patch queue.
3. Patch mode downloads only the missing image and preserves an existing torrent.
4. A torrent failure still upserts the item and leaves the thread pending.

Verification command:

```text
python -m unittest tests.test_local_assets tests.test_crawl_to_sqlite_resume tests.test_crawl_pipeline_resume -v
```

Result: 13 tests passed.

## Fix: corrupt DB asset path in old directory

Review finding: when `img_path`/`torrent_path` in DB points to a corrupt file under an old directory (not current `save_dir`), `resolve_asset_path` correctly rejects it but cleanup only removed corrupt files under current `save_dir`, leaving the old corrupt file behind.

**Change:** In `_ensure_image` and `_ensure_torrent`, after `resolve_asset_path` returns None, unlink the DB path if it is a regular file that fails the validity check (`missing_ok=True`). Existing save_dir corrupt cleanup unchanged.

**Tests added:** `test_patch_deletes_corrupt_db_image_in_old_dir`, `test_patch_deletes_corrupt_db_torrent_in_old_dir` — corrupt file at old DB path, different `save_dir`, assert old file deleted, download invoked, new valid path written.

Verification command:

```text
python -m unittest tests.test_local_assets tests.test_crawl_to_sqlite_resume tests.test_crawl_pipeline_resume -v
```

Result: 15 tests passed.

## Notes

- The image cleanup deliberately excludes `.torrent` candidates with the same stem, preventing a missing-image retry from deleting a valid torrent before the asset precheck.
