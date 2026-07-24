#!/usr/bin/env python3
"""
主编排：列表页→详情页→图片/种子下载→SQLite。
调用 list_parse / detail_parse / image_download / rmdown_download / crawl_to_sqlite 的 API。
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from crawl_to_sqlite import (
    ensure_db,
    item_needs_asset_retry,
    list_items_for_thread,
    list_pending_threads,
    list_thread_urls_with_missing_assets,
    thread_assets_complete,
    thread_exists,
    update_thread_status,
    upsert_item,
    upsert_thread,
)
from detail_parse import fetch_and_parse_detail
from image_download import download_image_from_url
from list_parse import build_list_page_url, fetch_and_parse_list_page
from rmdown_download import download_from_rmdown_url
from title_translate import translate_code_title
from local_assets import (
    is_valid_image_file,
    is_valid_torrent_file,
    resolve_asset_path,
)

log = logging.getLogger(__name__)

SOURCE_URL_MAP: dict[str, str] = {
    "zz": "https://www.t66y.com/thread0806.php?fid=26",
    "ym": "https://www.t66y.com/thread0806.php?fid=15",
}


URL_SOURCE_MAP: dict[str, str] = {v: k for k, v in SOURCE_URL_MAP.items()}


def resolve_url(source: str) -> str:
    """从 source 标记查表得到列表 URL。"""
    url = SOURCE_URL_MAP.get(source)
    if not url:
        valid = ", ".join(SOURCE_URL_MAP)
        raise ValueError(f"未知的 source: {source!r}，可选值: {valid}")
    return url


def resolve_source(list_url: str) -> str:
    """从列表 URL 查表得到 source 标记。"""
    source = URL_SOURCE_MAP.get(list_url)
    if not source:
        raise ValueError(f"未知的 URL: {list_url}，请在 SOURCE_URL_MAP 中注册")
    return source


def _sanitize_dirname(name: str) -> str:
    """清理目录名，保留中日韩英数字和少量符号。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "untitled"


def _sanitize_filename(name: str) -> str:
    """清理文件名干（不含扩展名）。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "file"


def _build_work_queue(*, conn, list_threads: list[dict], source: str) -> list[dict]:
    """合并列表页、待处理和缺失资源线程；全量处理优先于补丁处理。"""
    del source
    by_url: dict[str, dict] = {}
    for row in list_threads:
        by_url[row["url"]] = {
            "url": row["url"],
            "title": row.get("title") or "",
            "downloads": row.get("downloads") or 0,
            "mode": "full",
        }
    for row in list_pending_threads(conn):
        if row["url"] not in by_url:
            by_url[row["url"]] = {
                "url": row["url"],
                "title": row.get("title") or "",
                "downloads": row.get("downloads") or 0,
                "mode": "full",
            }
    for url in list_thread_urls_with_missing_assets(conn):
        if url in by_url:
            continue
        row = conn.execute(
            "SELECT url, title, downloads FROM threads WHERE url = ?", (url,)
        ).fetchone()
        if row:
            by_url[url] = {
                "url": row[0],
                "title": row[1] or "",
                "downloads": row[2] or 0,
                "mode": "patch",
            }
    return list(by_url.values())


def _ensure_image(
    img_url: str | None,
    img_path_db: str | None,
    save_dir: Path,
    safe_name: str,
) -> str | None:
    if not img_url:
        return img_path_db if is_valid_image_file(img_path_db or "") else None
    existing = resolve_asset_path(
        db_path=img_path_db, save_dir=save_dir, stem=safe_name, kind="image"
    )
    if existing:
        return str(existing)
    if img_path_db:
        db_file = Path(img_path_db)
        if db_file.is_file() and not is_valid_image_file(db_file):
            db_file.unlink(missing_ok=True)
    for path in save_dir.glob(f"{safe_name}.*"):
        if (
            path.is_file()
            and path.suffix.lower() != ".torrent"
            and not is_valid_image_file(path)
        ):
            path.unlink(missing_ok=True)
    try:
        return str(download_image_from_url(img_url, save_dir, filename=safe_name))
    except Exception as exc:
        log.error("  IMG-ERR %s: %s", img_url, exc)
        return None


def _ensure_torrent(
    torrent_url: str | None,
    torrent_path_db: str | None,
    save_dir: Path,
    safe_name: str,
) -> str | None:
    if not torrent_url:
        return torrent_path_db if is_valid_torrent_file(torrent_path_db or "") else None
    existing = resolve_asset_path(
        db_path=torrent_path_db, save_dir=save_dir, stem=safe_name, kind="torrent"
    )
    if existing:
        return str(existing)
    if torrent_path_db:
        db_file = Path(torrent_path_db)
        if db_file.is_file() and not is_valid_torrent_file(db_file):
            db_file.unlink(missing_ok=True)
    target = save_dir / f"{safe_name}.torrent"
    if target.is_file() and not is_valid_torrent_file(target):
        target.unlink(missing_ok=True)
    try:
        return str(download_from_rmdown_url(torrent_url, save_dir, filename=safe_name))
    except Exception as exc:
        log.error("  TORRENT-ERR %s: %s", torrent_url, exc)
        return None


def _process_thread_full(
    *,
    conn,
    thread: dict,
    day_dir: Path,
    source: str,
    delay_sec: float,
) -> None:
    detail_url = thread["url"]
    try:
        items = fetch_and_parse_detail(detail_url, topic_title=thread.get("title"))
    except Exception as exc:
        log.error("详情页请求失败 %s: %s", detail_url, exc)
        update_thread_status(conn, detail_url, "pending")
        return
    if not items:
        log.warning("未解析到番号条目 %s", detail_url)
        update_thread_status(conn, detail_url, "pending")
        return

    save_dir = day_dir / _sanitize_dirname(thread["title"]) if len(items) > 1 else day_dir
    save_dir.mkdir(parents=True, exist_ok=True)
    existing_items = {item["code"]: item for item in list_items_for_thread(conn, detail_url)}
    kept = 0
    filtered = 0

    for item in items:
        size_gb = item.get("size_gb")
        if size_gb and float(size_gb) <= 1.5:
            filtered += 1
            continue
        kept += 1
        code = item.get("code") or "unknown"
        prior = existing_items.get(code, {})
        code_title = item.get("code_title") or code
        safe_name = _sanitize_filename(code_title)
        img_path = _ensure_image(
            item.get("img_url"), prior.get("img_path"), save_dir, safe_name
        )
        torrent_path = _ensure_torrent(
            item.get("torrent_url"), prior.get("torrent_path"), save_dir, safe_name
        )
        translated = (translate_code_title(item.get("code_title")) or "").strip()
        title_transfer = translated if translated and translated != (item.get("code_title") or "").strip() else None
        upsert_item(
            conn,
            thread_url=detail_url,
            code=code,
            code_title=item.get("code_title"),
            title_transfer=title_transfer,
            actress=item.get("actress"),
            size_gb=size_gb,
            img_url=item.get("img_url"),
            img_path=img_path,
            torrent_url=item.get("torrent_url"),
            torrent_path=torrent_path,
            source=source,
        )
        if delay_sec > 0:
            time.sleep(delay_sec)

    remaining_items = list_items_for_thread(conn, detail_url)
    all_filtered_without_retries = (
        kept == 0
        and filtered > 0
        and not any(item_needs_asset_retry(item) for item in remaining_items)
    )
    status = (
        "done"
        if thread_assets_complete(conn, detail_url) or all_filtered_without_retries
        else "pending"
    )
    update_thread_status(conn, detail_url, status)


def _process_thread_patch(
    *,
    conn,
    thread_url: str,
    title: str,
    save_dir: Path,
    source: str,
    delay_sec: float,
) -> None:
    del title
    save_dir.mkdir(parents=True, exist_ok=True)
    for item in list_items_for_thread(conn, thread_url):
        if not item_needs_asset_retry(item):
            continue
        safe_name = _sanitize_filename(item.get("code_title") or item["code"] or "unknown")
        img_path = item.get("img_path")
        torrent_path = item.get("torrent_path")
        if item.get("img_url") and not is_valid_image_file(img_path or ""):
            img_path = _ensure_image(item["img_url"], img_path, save_dir, safe_name)
        if item.get("torrent_url") and not is_valid_torrent_file(torrent_path or ""):
            torrent_path = _ensure_torrent(
                item["torrent_url"], torrent_path, save_dir, safe_name
            )
        upsert_item(
            conn,
            thread_url=thread_url,
            code=item["code"],
            code_title=item.get("code_title"),
            title_transfer=item.get("title_transfer"),
            actress=item.get("actress"),
            size_gb=item.get("size_gb"),
            img_url=item.get("img_url"),
            img_path=img_path,
            torrent_url=item.get("torrent_url"),
            torrent_path=torrent_path,
            source=source,
        )
        if delay_sec > 0:
            time.sleep(delay_sec)
    update_thread_status(
        conn, thread_url, "done" if thread_assets_complete(conn, thread_url) else "pending"
    )


def _patch_save_dir(conn, thread_url: str, title: str, day_dir: Path) -> Path:
    items = list_items_for_thread(conn, thread_url)
    for item in items:
        for path, check in (
            (item.get("img_path"), is_valid_image_file),
            (item.get("torrent_path"), is_valid_torrent_file),
        ):
            if path and check(path):
                return Path(path).parent
    return day_dir / _sanitize_dirname(title) if len(items) > 1 else day_dir


def crawl_pipeline(
    source: str,
    download_dir: Path,
    db_path: Path,
    max_pages: int,
    delay_sec: float,
    list_delay_sec: float,
) -> None:
    list_url = resolve_url(source)
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = download_dir / today / source
    day_dir.mkdir(parents=True, exist_ok=True)

    conn = ensure_db(db_path)
    try:
        # ── 1.1 ~ 1.4：遍历列表分页，收集未入库的 thread ──
        all_threads: list[dict] = []
        for p in range(1, max_pages + 1):
            page_url = build_list_page_url(list_url, p)
            try:
                rows = fetch_and_parse_list_page(page_url)
            except Exception as e:
                log.error("列表页失败 %s: %s", page_url, e)
                break

            new_count = 0
            for row in rows:
                if thread_exists(conn, row["url"]):
                    continue
                all_threads.append(row)
                new_count += 1

            log.info(
                "page=%d url=%s 本页 %d 条 新增 %d 累计 %d",
                p, page_url, len(rows), new_count, len(all_threads),
            )
            if list_delay_sec > 0:
                time.sleep(list_delay_sec)

        work = _build_work_queue(conn=conn, list_threads=all_threads, source=source)
        for thread in work:
            if thread["mode"] != "full":
                continue
            upsert_thread(
                conn,
                thread["url"],
                thread["title"],
                thread["downloads"],
                source=source,
                status="pending",
            )

        for i, thread in enumerate(work, 1):
            log.info("[%d/%d] %s", i, len(work), thread["url"])
            if thread["mode"] == "full":
                _process_thread_full(
                    conn=conn, thread=thread, day_dir=day_dir,
                    source=source, delay_sec=delay_sec,
                )
            else:
                _process_thread_patch(
                    conn=conn,
                    thread_url=thread["url"],
                    title=thread["title"],
                    save_dir=_patch_save_dir(conn, thread["url"], thread["title"], day_dir),
                    source=source,
                    delay_sec=delay_sec,
                )

    finally:
        conn.close()

    log.info("DONE SQLite: %s | 下载目录: %s", db_path, day_dir)


def _setup_logging(log_dir: Path) -> Path:
    """配置 logging：同时输出到控制台和日志文件，返回日志文件路径。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"crawl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return log_file


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="列表页→详情页→图片/种子下载→SQLite"
    )
    p.add_argument(
        "--source",
        default="zz",
        choices=list(SOURCE_URL_MAP),
        help="数据源 (默认: zz)",
    )
    p.add_argument(
        "--url",
        default=None,
        help="直接指定列表页 URL（优先于 --source）",
    )
    p.add_argument(
        "--download-dir",
        default="D:/data/cl_assets",
        help="下载根目录（会在其下按日期创建子目录）",
    )
    p.add_argument(
        "--db-path",
        default="D:/data/cl_db/cl.db",
        help="SQLite 数据库路径",
    )
    p.add_argument(
        "--log-dir",
        default="D:/data/cl_logs",
        help="日志文件存放目录",
    )
    p.add_argument("--max-pages", type=int, default=3, help="列表页翻页数")
    p.add_argument(
        "--delay", type=float, default=8.0,
        help="每个番号下载之间的间隔（秒）",
    )
    p.add_argument(
        "--list-delay", type=float, default=1.0,
        help="列表页每页请求之间的间隔（秒）",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    download_dir = Path(args.download_dir)
    db_path = Path(args.db_path)
    log_file = _setup_logging(Path(args.log_dir))

    log.info("日志文件: %s", log_file)

    if args.url:
        source = resolve_source(args.url)
    else:
        source = args.source

    try:
        crawl_pipeline(
            source=source,
            download_dir=download_dir,
            db_path=db_path,
            max_pages=args.max_pages,
            delay_sec=args.delay,
            list_delay_sec=args.list_delay,
        )
        return 0
    except KeyboardInterrupt:
        log.warning("用户中断")
        return 130
    except Exception as e:
        log.exception("运行失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
