#!/usr/bin/env python3
"""
みんなのAV 女优列表爬取（仅公开列表页，不爬详情/编辑页）。
输出 CSV：source_id, ja_name, kana, romaji, works_count
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests

try:
    from curl_cffi import requests as cf_requests
except ImportError:  # pragma: no cover
    cf_requests = None  # type: ignore[assignment]

BASE = "https://www.minnano-av.com/"
LIST_PATH = "actress_list.php"

GOJUON_KEYS = [
    "a", "i", "u", "e", "o",
    "ka", "ki", "ku", "ke", "ko",
    "sa", "shi", "su", "se", "so",
    "ta", "chi", "tsu", "te", "to",
    "na", "ni", "nu", "ne", "no",
    "ha", "hi", "hu", "he", "ho",
    "ma", "mi", "mu", "me", "mo",
    "ya", "yu", "yo",
    "ra", "ri", "ru", "re", "ro",
    "wa", "wo", "n",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.minnano-av.com/",
}

# 列表页结构：h2.ttl=汉字，p.furi=「かな / Romaji」
FURI_RE = re.compile(
    r"^(?P<kana>[\u3040-\u309F\u30A0-\u30FFー･・]+)\s*/\s*(?P<romaji>.+?)\s*$"
)
# 兼容整格纯文本：朝比奈紗良 あさひなさら / Asahina Sara …
NAME_CELL_RE = re.compile(
    r"^(?P<ja>.+?)\s+"
    r"(?P<kana>[\u3040-\u309F\u30A0-\u30FFー･・]+)\s*/\s*"
    r"(?P<romaji>.+?)"
    r"(?:\s+\d{4}年|\s*（|\s*$)"
)

ID_RE = re.compile(r"actress(\d+)\.html", re.I)
PAGE_RE = re.compile(
    r"actress_list\.php\?[^\"'\s]*gojuon=(?P<g>[a-z]+)"
    r"[^\"'\s]*(?:[?&]|&amp;)page=(?P<p>\d+)",
    re.I,
)
TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
TTL_RE = re.compile(
    r'<h2[^>]*class="[^"]*ttl[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>\s*</h2>',
    re.I | re.S,
)
FURI_P_RE = re.compile(
    r'<p[^>]*class="[^"]*furi[^"]*"[^>]*>(.*?)</p>',
    re.I | re.S,
)
DEBUT_P_RE = re.compile(
    r'<p[^>]*class="[^"]*debut-info[^"]*"[^>]*>(.*?)</p>',
    re.I | re.S,
)
IMG_SRC_RE = re.compile(
    r'<img[^>]+src=["\']([^"\']+)["\']',
    re.I,
)


@dataclass(frozen=True)
class ActressRow:
    source_id: str
    ja_name: str
    kana: str
    romaji: str
    works_count: int
    detail_url: str = ""
    img_url: str = ""
    debut_text: str = ""


# 汉字艺名最短长度；假名/罗马字短于此则置空（不整行丢弃）
MIN_NAME_LEN = 2


def log(msg: str) -> None:
    print(msg, flush=True)


def _strip_tags(html: str) -> str:
    text = TAG_RE.sub(" ", html)
    text = re.sub(r"&nbsp;", " ", text, flags=re.I)
    text = re.sub(r"&amp;", "&", text, flags=re.I)
    text = re.sub(r"&lt;", "<", text, flags=re.I)
    text = re.sub(r"&gt;", ">", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _filter_name_parts(
    ja: str, kana: str = "", romaji: str = ""
) -> Optional[tuple[str, str, str]]:
    """汉字名长度 < MIN_NAME_LEN 整行丢弃；假名/罗马字过短则清空。"""
    ja = (ja or "").strip()
    kana = (kana or "").strip()
    romaji = (romaji or "").strip()
    if len(ja) < MIN_NAME_LEN:
        return None
    if len(kana) < MIN_NAME_LEN:
        kana = ""
    if len(romaji) < MIN_NAME_LEN:
        romaji = ""
    return ja, kana, romaji


def parse_name_cell(text: str) -> Optional[tuple[str, str, str]]:
    text = text.strip()
    if not text:
        return None
    m = NAME_CELL_RE.match(text)
    if not m:
        return None
    return _filter_name_parts(
        m.group("ja"), m.group("kana"), m.group("romaji")
    )


def parse_row_html(tr_html: str) -> Optional[ActressRow]:
    id_m = ID_RE.search(tr_html)
    if not id_m:
        return None
    source_id = id_m.group(1)

    ttl_m = TTL_RE.search(tr_html)
    furi_m = FURI_P_RE.search(tr_html)
    ja = _strip_tags(ttl_m.group(1)) if ttl_m else ""
    kana = ""
    romaji = ""
    if furi_m:
        furi_text = _strip_tags(furi_m.group(1))
        fm = FURI_RE.match(furi_text)
        if fm:
            kana = fm.group("kana").strip()
            romaji = fm.group("romaji").strip()

    if not ja:
        # 回退：整行纯文本
        parsed = parse_name_cell(_strip_tags(tr_html))
        if not parsed:
            return None
        ja, kana, romaji = parsed

    filtered = _filter_name_parts(ja, kana, romaji)
    if not filtered:
        return None
    ja, kana, romaji = filtered

    works = 0
    # 作品数在 class=details 之后的纯数字 td
    tds = re.findall(r"<td\b[^>]*>(.*?)</td>", tr_html, flags=re.I | re.S)
    for td in tds:
        if "details" in td.lower() or "actress" in td.lower():
            continue
        wtxt = _strip_tags(td)
        if re.fullmatch(r"\d+", wtxt):
            works = int(wtxt)
            break

    detail_url = urljoin(BASE, f"actress{source_id}.html")
    img_url = ""
    img_m = IMG_SRC_RE.search(tr_html)
    if img_m:
        img_url = urljoin(BASE, img_m.group(1).strip())

    debut_text = ""
    debut_m = DEBUT_P_RE.search(tr_html)
    if debut_m:
        debut_text = _strip_tags(debut_m.group(1))

    return ActressRow(
        source_id=source_id,
        ja_name=ja,
        kana=kana,
        romaji=romaji,
        works_count=works,
        detail_url=detail_url,
        img_url=img_url,
        debut_text=debut_text,
    )


def parse_list_html(html: str) -> tuple[list[ActressRow], int]:
    """解析一页列表，返回 (行, 该 gojuon 最大页码)。"""
    max_page = 1
    for m in PAGE_RE.finditer(html):
        try:
            max_page = max(max_page, int(m.group("p")))
        except ValueError:
            pass

    rows: list[ActressRow] = []
    seen: set[str] = set()
    for tr_html in TR_RE.findall(html):
        row = parse_row_html(tr_html)
        if not row or row.source_id in seen:
            continue
        seen.add(row.source_id)
        rows.append(row)
    return rows, max_page


_CF_HINT = (
    "Cloudflare 拦截未解除。可尝试："
    "1) pip install curl_cffi 后加 --impersonate chrome；"
    "2) 浏览器复制 Cookie 后加 --cookie；"
    "3) 另存列表 HTML 后用 --from-html。"
)


class MinnanoClient:
    def __init__(
        self,
        delay: float = 1.5,
        cookie: str = "",
        impersonate: str = "",
    ) -> None:
        self.delay = delay
        self.impersonate = (impersonate or "").strip()
        self._use_cf = bool(self.impersonate)
        if self._use_cf:
            if cf_requests is None:
                raise SystemExit(
                    "已指定 --impersonate，但未安装 curl_cffi。"
                    "请先执行: pip install curl_cffi"
                )
            self.sess = cf_requests.Session()
            log(f"HTTP 后端: curl_cffi impersonate={self.impersonate}")
        else:
            self.sess = requests.Session()
            log("HTTP 后端: requests（遇 Cloudflare 请加 --impersonate chrome）")
        self.sess.headers.update(HEADERS)
        if cookie:
            self.sess.headers["Cookie"] = cookie

    def _request(self, url: str):
        if self._use_cf:
            return self.sess.get(
                url, timeout=40, impersonate=self.impersonate
            )
        return self.sess.get(url, timeout=40)

    def get(self, url: str) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(8):
            try:
                r = self._request(url)
                if r.status_code in (403, 429, 503):
                    wait = min(60, 2**attempt)
                    log(f"  HTTP {r.status_code}，等待 {wait}s ...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                # Cloudflare challenge 页
                if "Just a moment" in r.text or "cf-browser-verification" in r.text:
                    wait = min(60, 2**attempt)
                    log(f"  Cloudflare 挑战，等待 {wait}s ...")
                    time.sleep(wait)
                    continue
                return r.text
            except Exception as e:
                last_err = e
                wait = min(60, 2**attempt)
                log(f"  请求失败: {e!r}，等待 {wait}s ...")
                time.sleep(wait)
        raise RuntimeError(f"fetch failed: {url} last={last_err!r}\n{_CF_HINT}")

    def list_url(self, gojuon: str, page: int) -> str:
        if page <= 1:
            return urljoin(BASE, f"{LIST_PATH}?gojuon={gojuon}")
        return urljoin(BASE, f"{LIST_PATH}?gojuon={gojuon}&page={page}")


def crawl_gojuon(
    client: MinnanoClient,
    gojuon: str,
    max_pages: Optional[int] = None,
) -> list[ActressRow]:
    url = client.list_url(gojuon, 1)
    log(f"[{gojuon}] {url}")
    html = client.get(url)
    rows, last = parse_list_html(html)
    if max_pages is not None:
        last = min(last, max_pages)
    log(f"[{gojuon}] page 1/{last} -> {len(rows)} rows")
    all_rows = list(rows)
    for page in range(2, last + 1):
        time.sleep(client.delay)
        url = client.list_url(gojuon, page)
        html = client.get(url)
        part, _ = parse_list_html(html)
        all_rows.extend(part)
        if page % 10 == 0 or page == last:
            log(f"[{gojuon}] page {page}/{last} cumulative={len(all_rows)}")
    return all_rows


def dedupe(rows: Iterable[ActressRow]) -> list[ActressRow]:
    by_id: dict[str, ActressRow] = {}
    for r in rows:
        by_id.setdefault(r.source_id, r)
    return sorted(by_id.values(), key=lambda x: (x.ja_name, x.source_id))


def write_csv(path: Path, rows: list[ActressRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "source_id",
                "ja_name",
                "kana",
                "romaji",
                "works_count",
                "detail_url",
                "img_url",
                "debut_text",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.source_id,
                    r.ja_name,
                    r.kana,
                    r.romaji,
                    r.works_count,
                    r.detail_url,
                    r.img_url,
                    r.debut_text,
                ]
            )


def main() -> None:
    p = argparse.ArgumentParser(description="爬取みんなのAV女优列表（汉字/假名/罗马字）")
    p.add_argument("--out", default="minnano_actress.csv", help="输出 CSV 路径")
    p.add_argument("--delay", type=float, default=1.5, help="请求间隔秒")
    p.add_argument(
        "--gojuon",
        default="",
        help="仅爬指定五十音（如 a）；默认全部",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="每个 gojuon 最多爬几页（调试用）",
    )
    p.add_argument(
        "--cookie",
        default="",
        help="可选 Cookie 字符串（浏览器导出，过 Cloudflare 时用）",
    )
    p.add_argument(
        "--impersonate",
        default="",
        help="使用 curl_cffi 模拟浏览器 TLS（推荐 chrome；需 pip install curl_cffi）",
    )
    p.add_argument(
        "--db",
        default="",
        help="若指定则爬完后写入 SQLite actress_names_minnano",
    )
    p.add_argument(
        "--from-html",
        default="",
        help="离线解析已保存的列表 HTML（可重复指定用逗号分隔多个文件）",
    )
    args = p.parse_args()

    collected: list[ActressRow] = []
    if args.from_html:
        for part in args.from_html.split(","):
            path = Path(part.strip())
            if not path:
                continue
            html = path.read_text(encoding="utf-8", errors="replace")
            part_rows, max_page = parse_list_html(html)
            log(f"[html] {path} -> {len(part_rows)} rows (max_page hint={max_page})")
            collected.extend(part_rows)
    else:
        keys = [args.gojuon] if args.gojuon else list(GOJUON_KEYS)
        try:
            client = MinnanoClient(
                delay=args.delay,
                cookie=args.cookie,
                impersonate=args.impersonate,
            )
            for i, g in enumerate(keys):
                if i:
                    time.sleep(client.delay)
                collected.extend(crawl_gojuon(client, g, max_pages=args.max_pages))
        except RuntimeError as e:
            raise SystemExit(str(e)) from e

    rows = dedupe(collected)
    if not rows:
        raise SystemExit(
            "未解析到任何女优行，拒绝写入空结果。\n" + _CF_HINT
        )

    out = Path(args.out)
    write_csv(out, rows)
    log(f"已写入 {out} : {len(rows)} 行")

    if args.db:
        from crawl_to_sqlite import ensure_db, replace_minnano_actress_from_csv

        conn = ensure_db(Path(args.db))
        try:
            n = replace_minnano_actress_from_csv(conn, out)
            log(f"已写入 SQLite {args.db} 表 actress_names_minnano: {n} 行")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
