# Task 4 Report: Full Regression + Manual Smoke Checklist

## Status

**Resume/retry scope: PASS** — all 15 resume/retry/local_assets tests green; all 5 smoke checklist items verified with evidence.

**Full suite: FAIL (4 errors)** — unrelated to Tasks 1–3; missing offline HTML fixtures never committed to repo.

**Commits:** none (no resume/retry regressions to fix).

---

## Step 1: Unit Test Run

Command:

```bash
python -m unittest discover -s tests -v
```

Summary (2026-07-25):

| Module | Pass | Fail/Error |
|--------|------|------------|
| `test_crawl_pipeline_resume` | 6 | 0 |
| `test_crawl_to_sqlite_resume` | 4 | 0 |
| `test_local_assets` | 5 | 0 |
| `test_detail_parse` (inline/unit) | 30 | 0 |
| `test_detail_parse` (HTML fixtures) | 0 | **4 errors** |
| **Total** | **45** | **4 errors** |

```
Ran 49 tests in 0.332s
FAILED (errors=4)
```

### Errors (pre-existing fixture gap, not resume/retry)

All four fail with `FileNotFoundError` — fixtures not in workspace or git:

- `detail-signal.html` → `test_signal_atid663`, `test_report_core_fields_json_shape`
- `detail-xilie.html` → `test_xilie_series_one_torrent`
- `detail-xilie2.html` → `test_xilie2_multi_torrent_order`

`git ls-files "*.html"` returns empty; fixtures were never tracked.

### Resume/retry subset (clean)

```bash
python -m unittest tests.test_crawl_pipeline_resume tests.test_crawl_to_sqlite_resume tests.test_local_assets -v
```

```
Ran 15 tests in 0.344s
OK
```

Full verbose log saved: `.superpowers/sdd/task-4-test-output.txt`

---

## Step 2: Smoke Checklist Evidence

All checks used throwaway temp DBs under `tempfile.TemporaryDirectory()`; no production `cl.db` touched.

### 1. Pending thread not on list → processed as `full`

**Evidence:** `test_queue_includes_pending_not_on_list` + throwaway smoke run.

- Queue: `[{'url': 'http://old', 'mode': 'full', ...}]` with empty `list_threads`.
- After mocked `_process_thread_full`: thread `status=done`, 1 item upserted.
- **PASS**

### 2. `done` thread, `img_url` set / `img_path` NULL → `patch`, download, `done`

**Evidence:** `test_queue_patch_for_done_missing_img` + `test_patch_downloads_only_missing_and_skips_existing`.

- Queue mode `patch` for done thread with missing image path.
- `download_image_from_url` called once; after patch `thread_assets_complete(conn, "http://t")` is True.
- **PASS**

### 3. Valid local torrent path → no `download_from_rmdown_url`

**Evidence:** `test_patch_downloads_only_missing_and_skips_existing`.

- Pre-seeded valid `.torrent` at DB path; patch only missing image.
- `torrent_download.assert_not_called()`.
- **PASS**

### 4. Item with no `img_url` does not block `done`

**Evidence:** `test_complete_when_files_exist_and_no_url_ok`.

- Item CODE-2: `img_url=None`, `torrent_url=None`; sibling has valid files.
- `thread_assets_complete` returns True.
- **PASS**

### 5. `size_gb=1.0` in detail → not upserted; absent from missing-asset query

**Evidence:** throwaway smoke via mocked `_process_thread_full` with two parsed items (`SMALL` @ 1.0G, `BIG` @ 2.0G).

- DB codes after full: `['BIG']` only — `SMALL` skipped by `float(size_gb) <= 1.5` filter.
- `list_candidate_missing_asset_rows`: `[]`.
- `list_thread_urls_with_missing_assets`: `[]`.
- **PASS**

---

## Step 3: Commit

Skipped — no resume/retry regressions found; no code changes.

---

## Concerns

1. **Missing HTML fixtures** block 4 `test_detail_parse` tests on fresh checkout. Not introduced by resume/retry work; fixtures should be added to repo or tests should skip when files absent.
2. **Smoke item 5** has no permanent unit test (verified ad hoc this task); consider adding to `test_crawl_pipeline_resume.py` in a follow-up.

---

## Final Review Fix: Fully Size-Filtered Threads

### Change

`_process_thread_full` now tracks parsed items that entered the upsert/download
path (`kept`) and items skipped by the `size_gb <= 1.5` filter (`filtered`).
When every parsed item was filtered and no persisted item still needs an asset
retry, it marks the thread `done`. Detail-fetch failures and empty parses retain
their existing `pending` behavior. `thread_assets_complete` remains unchanged.

### Regression Coverage

Added permanent tests in `tests/test_crawl_pipeline_resume.py` for:

- all items at or below 1.5 GB: no rows, no download calls, thread `done`;
- a mixed filtered/kept parse: only the kept row is stored and the thread is
  `done` when that row has no required asset.

### Evidence (2026-07-25)

```bash
python -m unittest tests.test_crawl_pipeline_resume
```

```
Ran 8 tests in 0.259s
OK
```
