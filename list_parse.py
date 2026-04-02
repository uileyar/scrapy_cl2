#!/usr/bin/env python3
"""
列表页爬取与解析。
对外入口：fetch_and_parse_list_page、build_list_page_url
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from http_fetch import fetch_html as _fetch_html


_TITLE_REMOVE = re.compile(
    r"\[中字\]|\[中文字幕\]|《FHD中文》|\[HD\]|\[有碼高清中文字幕\]"
)


def clean_title(title: str) -> str:
    """统一括号、删除冗余标签。"""
    title = title.replace("【", "[").replace("】", "]")
    title = _TITLE_REMOVE.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def normalize_url(base_url: str, raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""
    if raw_url.startswith(("javascript:", "mailto:", "data:", "#")):
        return ""
    return urljoin(base_url, raw_url)


def is_likely_thread_detail(url: str, base_netloc: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.netloc and p.netloc != base_netloc:
        return False
    path = (p.path or "").lower()
    query = (p.query or "").lower()
    if "htm_data" in path and path.endswith(".html"):
        return True
    if "read.php" in path and "tid=" in query:
        return True
    return False


def _class_has(attrs: List[Tuple[str, str]], name: str) -> bool:
    cls = dict(attrs).get("class") or ""
    return name in cls.split()


class _ListPageParser(HTMLParser):
    """解析 #ajaxtable 内每行：url、title、downloads。

    列结构（0‑indexed）：
        0=贊/标签  1=文章(td.tal)  2=作者  3=回復  4=下载  5=最後發表
    公告行用 <th> 而非 <td>，自动跳过。
    """

    def __init__(self, list_page_url: str) -> None:
        super().__init__()
        self.list_page_url = list_page_url
        self.netloc = urlparse(list_page_url).netloc
        self.items: List[Dict[str, Any]] = []

        self._in_ajax = False
        self._in_row = False
        self._td_idx = -1
        self._in_td = False
        self._in_h3 = False
        self._h3_parts: List[str] = []
        self._current_href: Optional[str] = None
        self._td_text: List[str] = []
        self._row_downloads = ""

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        ad = dict(attrs)
        if tag == "table" and ad.get("id") == "ajaxtable":
            self._in_ajax = True
            return
        if not self._in_ajax:
            return
        if tag == "tr":
            self._in_row = True
            self._td_idx = -1
            self._current_href = None
            self._h3_parts = []
            self._row_downloads = ""
        if tag == "td" and self._in_row:
            self._td_idx += 1
            self._in_td = True
            self._td_text = []
        if self._in_td and tag == "h3":
            self._in_h3 = True
        if self._in_td and tag == "a" and ad.get("href"):
            full = normalize_url(self.list_page_url, ad["href"].strip())
            if full and self._current_href is None and is_likely_thread_detail(full, self.netloc):
                self._current_href = full.split("#")[0]

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
        if tag == "td" and self._in_td:
            if self._td_idx == 4:
                self._row_downloads = "".join(self._td_text).strip()
            self._in_td = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_href:
                title = re.sub(r"\s+", " ", "".join(self._h3_parts)).strip()
                dl_text = self._row_downloads
                downloads = int(dl_text) if dl_text.isdigit() else 0
                self.items.append({
                    "url": self._current_href,
                    "title": clean_title(title),
                    "downloads": downloads,
                })
        if tag == "table" and self._in_ajax:
            self._in_ajax = False

    def handle_data(self, data: str) -> None:
        if self._in_td and self._in_h3:
            self._h3_parts.append(data)
        if self._in_td:
            self._td_text.append(data)


def parse_list_page(html: str, list_page_url: str) -> List[Dict[str, Any]]:
    """解析列表页 HTML。

    :return: ``[{"url": str, "title": str, "downloads": int}, ...]``
    """
    parser = _ListPageParser(list_page_url)
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.items


def fetch_and_parse_list_page(list_page_url: str) -> List[Dict[str, Any]]:
    """拉取列表页并解析，返回 ``[{"url", "title", "downloads"}, ...]``。"""
    html = _fetch_html(list_page_url)
    return parse_list_page(html, list_page_url)


def build_list_page_url(base_list_url: str, page_num: int) -> str:
    """拼接分页 URL。第 1 页返回原 URL。"""
    if page_num <= 1:
        return base_list_url
    parsed = urlparse(base_list_url)
    pairs: List[Tuple[str, str]] = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "page"
    ]
    keys = {k for k, _ in pairs}
    if "fid" in keys and "search" not in keys:
        pairs.append(("search", ""))
    pairs.append(("page", str(page_num)))
    new_query = urlencode(pairs)
    return urlunparse(parsed._replace(query=new_query))
