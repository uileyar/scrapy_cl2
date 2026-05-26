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

        # 1.9（前半）：新 thread 先入库，状态 pending
        for thread in all_threads:
            upsert_thread(
                conn,
                thread["url"],
                thread["title"],
                thread["downloads"],
                source=source,
                status="pending",
            )

        # ── 1.5 ~ 1.8：逐条处理详情页 ──
        for i, thread in enumerate(all_threads, 1):
            detail_url = thread["url"]
            log.info("[%d/%d] %s", i, len(all_threads), detail_url)

            try:
                items = fetch_and_parse_detail(
                    detail_url,
                    topic_title=thread.get("title"),
                )
            except Exception as e:
                log.error("详情页请求失败 %s: %s", detail_url, e)
                continue

            if not items:
                log.warning("未解析到番号条目 %s", detail_url)
                continue

            # 1.6：多番号时以列表标题创建子目录
            if len(items) > 1:
                safe_title = _sanitize_dirname(thread["title"])
                save_dir = day_dir / safe_title
            else:
                save_dir = day_dir
            save_dir.mkdir(parents=True, exist_ok=True)

            for item in items:
                size_gb = item.get("size_gb")
                if size_gb and float(size_gb) <= 1.5:
                    continue
                code_title = item.get("code_title") or item.get("code") or "unknown"
                safe_name = _sanitize_filename(code_title)

                # 1.6b：下载图片，code_title 作为文件名
                img_path: str | None = None
                if item.get("img_url"):
                    try:
                        p = download_image_from_url(
                            item["img_url"],
                            save_dir,
                            filename=safe_name,
                        )
                        img_path = str(p)
                        log.info("  IMG %s", p.name)
                    except Exception as e:
                        log.error("  IMG-ERR %s: %s", item["img_url"], e)

                # 1.7：下载种子，code_title 作为文件名
                torrent_path: str | None = None
                if item.get("torrent_url"):
                    try:
                        p = download_from_rmdown_url(
                            item["torrent_url"],
                            save_dir,
                            filename=safe_name,
                        )
                        torrent_path = str(p)
                        log.info("  TORRENT %s", p.name)
                    except Exception as e:
                        log.error("  TORRENT-ERR %s: %s", item["torrent_url"], e)
                        continue

                # 1.8：番号条目入库（title_transfer：与 code_title 不同的译文才写入）
                ct = (item.get("code_title") or "").strip()
                title_tr = (translate_code_title(item.get("code_title")) or "").strip()
                title_transfer = (
                    title_tr if title_tr and title_tr != ct else None
                )
                upsert_item(
                    conn,
                    thread_url=detail_url,
                    code=item.get("code") or "unknown",
                    code_title=item.get("code_title"),
                    title_transfer=title_transfer,
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

            # 1.9（后半）：thread 状态更新为 done
            update_thread_status(conn, detail_url, "done")
            log.info("  OK %d 个番号处理完毕", len(items))

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
