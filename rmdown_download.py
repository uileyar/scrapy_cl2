#!/usr/bin/env python3
"""
从 rmdown link 页面拉取 HTML，解析 des/esc/axs/reff/ref，拼接 download.php 并下载种子。
对外入口：download_from_rmdown_url
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


DOWNLOAD_BASE = "https://www.rmdown.com/download.php"
REQUIRED_NAMES = ("des", "esc", "axs", "reff", "ref")


class _HiddenInputsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        d = {k.lower(): (v or "") for k, v in attrs}
        if d.get("type", "").lower() != "hidden":
            return
        name = d.get("name")
        if not name:
            return
        self.fields[name.lower()] = d.get("value", "")


def parse_hidden_fields(html: str) -> dict[str, str]:
    parser = _HiddenInputsParser()
    parser.feed(html)
    return parser.fields


def build_download_url(fields: dict[str, str], base: str = DOWNLOAD_BASE) -> str:
    missing = [n for n in REQUIRED_NAMES if not fields.get(n)]
    if missing:
        raise ValueError(f"缺少隐藏字段: {', '.join(missing)}")
    query = {n: fields[n] for n in REQUIRED_NAMES}
    return f"{base.rstrip('/')}?{urlencode(query)}"


def _filename_from_disposition(cd: str | None) -> str | None:
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?("?)([^";\n]+)\1', cd, re.I)
    if m:
        return m.group(2).strip()
    m = re.findall(r'filename=("?)([^";\n]+)\1?', cd, re.I)
    return m[-1][1].strip() if m else None


def _fetch_text(url: str, *, referer: str | None = None, session_headers: dict[str, str] | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer or "https://www.rmdown.com/",
    }
    if session_headers:
        headers.update(session_headers)
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _safe_torrent_name(stem: str) -> str:
    stem = stem.replace("\\", "_").replace("/", "_")
    stem = re.sub(r'[<>:"|?*]', "_", stem)
    return (stem.strip() or "torrent") + ".torrent"


def download_torrent(
    url: str,
    out_dir: Path,
    session_headers: dict[str, str] | None = None,
    *,
    referer: str | None = None,
    override_name: str | None = None,
) -> Path:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": referer or "https://www.rmdown.com/",
    }
    if session_headers:
        headers.update(session_headers)

    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
        cd = resp.headers.get("Content-Disposition")
        name = _filename_from_disposition(cd)

    if override_name:
        name = _safe_torrent_name(override_name)
    elif not name or "." not in name:
        q = urlparse(url).query
        ref_m = re.search(r"ref=([^&]+)", q)
        stub = ref_m.group(1)[:16] if ref_m else "torrent"
        name = f"{stub}.torrent"

    path = out_dir / name
    if path.exists():
        stem, suf = path.stem, path.suffix
        n = 1
        while path.exists():
            path = out_dir / f"{stem}_{n}{suf}"
            n += 1

    path.write_bytes(data)
    return path


def download_from_rmdown_url(
    page_url: str,
    out_dir: str | Path,
    *,
    filename: str | None = None,
    download_base: str = DOWNLOAD_BASE,
    session_headers: dict[str, str] | None = None,
    retries: int = 3,
) -> Path:
    """
    打开 rmdown 资源页（如 link.php?hash=...），解析隐藏表单字段并下载种子。

    :param page_url: 页面完整 URL
    :param out_dir: 保存目录
    :param filename: 文件名（不含扩展名），自动补 ``.torrent``；为 None 时由服务器响应决定
    :param download_base: download.php 的 URL（不含查询串）
    :param session_headers: 附加请求头（拉取页面与下载种子时都会合并）
    :param retries: 最大重试次数（含首次请求）
    :return: 已写入文件的绝对路径（pathlib.Path）
    """
    page_url = page_url.strip()
    if not page_url:
        raise ValueError("page_url 为空")

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            html = _fetch_text(page_url, referer="https://www.rmdown.com/", session_headers=session_headers)
            fields = parse_hidden_fields(html)
            dl_url = build_download_url(fields, base=download_base)
            saved = download_torrent(
                dl_url,
                Path(out_dir),
                session_headers=session_headers,
                referer=page_url,
                override_name=filename,
            )
            return saved.resolve()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = 2.0 * (2 ** attempt)
                log.warning("种子下载失败 (%d/%d): %s，%.0fs 后重试", attempt + 1, retries, e, wait)
                time.sleep(wait)
    raise RuntimeError(f"种子下载失败，已重试 {retries} 次: {last_err}") from last_err


def main() -> None:
    parser = argparse.ArgumentParser(description="从 rmdown 资源页下载种子")
    parser.add_argument("page_url", help="资源页 URL")
    parser.add_argument("out_dir", help="保存目录")
    args = parser.parse_args()
    download_from_rmdown_url(args.page_url, args.out_dir)


if __name__ == "__main__":
    main()