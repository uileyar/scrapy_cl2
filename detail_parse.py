"""
详情页爬取与解析：单番号(signal)、多番号单种子、多番号多种子。
对外入口：fetch_and_parse_detail、parse_detail_items
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from http_fetch import fetch_html as _fetch_html

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

CODE_RE = re.compile(r"\b([A-Z]{2,}-\d+)\b")
SIZE_HD_RE = re.compile(r"\[HD/\s*([\d.]+)\s*GB?\]", re.I)
SIZE_MP4_RE = re.compile(r"\[MP4/\s*([\d.]+)\s*GB?\]", re.I)
SIZE_PLAIN_RE = re.compile(r"【影片大小】[︰：:]\s*([\d.]+)\s*GB?", re.I)
H4_F16_RE = re.compile(
    r'<h4[^>]*\bclass\s*=\s*["\'][^"\']*\bf16\b[^"\']*["\'][^>]*>(.*?)</h4>',
    re.I | re.DOTALL,
)
CONTTPC_OPEN_RE = re.compile(
    r'<div\b[^>]*\bid\s*=\s*["\']conttpc["\'][^>]*>',
    re.I,
)


def strip_html_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_html_tags_keep_nl(fragment: str) -> str:
    """去标签但保留换行，供系列帖按行切分。"""
    t = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    t = re.sub(r"</p\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def parse_h4_f16(html: str) -> Optional[str]:
    m = H4_F16_RE.search(html)
    if not m:
        return None
    return strip_html_tags(m.group(1))


def extract_conttpc_html(html: str) -> str:
    """截取第一个 ``div#conttpc`` 的内部 HTML（从开标签结束到对应闭合 div）。"""
    m = CONTTPC_OPEN_RE.search(html)
    if not m:
        return ""
    start = m.end()
    depth = 1
    i = start
    n = len(html)
    while i < n and depth > 0:
        if html[i] != "<":
            i += 1
            continue
        if html.startswith("<!--", i):
            j = html.find("-->", i + 4)
            i = j + 3 if j != -1 else n
            continue
        mtag = re.match(r"</([a-zA-Z][\w:-]*)\s*>", html[i:])
        if mtag:
            tag = mtag.group(1).lower()
            if tag == "div":
                depth -= 1
            i += mtag.end()
            continue
        mtag = re.match(
            r"<([a-zA-Z][\w:-]*)(\s[^>]*)?/?>",
            html[i:],
        )
        if mtag:
            tag = mtag.group(1).lower()
            rest = mtag.group(0)
            if tag not in VOID_TAGS and not rest.rstrip().endswith("/>"):
                if tag == "div":
                    depth += 1
            i += mtag.end()
            continue
        i += 1
    return html[start:i]


class ConttpcWalkParser(HTMLParser):
    """遍历第一个 ``#conttpc``：文本（br→换行）、img URL、rmdown 链接顺序。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._active = False
        self._depth = 0
        self.plain_chunks: List[str] = []
        self.img_urls: List[str] = []
        self.rmdown_hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        ad = dict(attrs)
        if not self._active:
            if tag == "div" and ad.get("id") == "conttpc":
                self._active = True
                self._depth = 1
            return
        if tag == "br":
            self.plain_chunks.append("\n")
            return
        if tag not in VOID_TAGS:
            self._depth += 1
        if tag == "img":
            url = (ad.get("ess-data") or ad.get("src") or "").strip()
            if url and "adblo_ck" not in url and not url.endswith("adblo_ck.jpg"):
                self.img_urls.append(url)
        elif tag == "a":
            href = (ad.get("href") or "").strip()
            if href and "rmdown.com" in href:
                self.rmdown_hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        if not self._active:
            return
        if tag not in VOID_TAGS:
            self._depth -= 1
        if self._depth <= 0:
            self._active = False

    def handle_data(self, data: str) -> None:
        if self._active:
            self.plain_chunks.append(data)

    def close(self) -> None:  # noqa: A003
        super().close()


def walk_conttpc(html: str) -> Tuple[str, List[str], List[str]]:
    p = ConttpcWalkParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    plain = "".join(p.plain_chunks)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip(), p.img_urls, p.rmdown_hrefs


def _h4_size_gb(h4_text: str) -> Optional[str]:
    for rx in (SIZE_HD_RE, SIZE_MP4_RE):
        m = rx.search(h4_text)
        if m:
            return m.group(1)
    return None


def _size_gb_from_plain(plain: str) -> Optional[str]:
    """从正文中提取【影片大小】后的 GB 数值。"""
    m = SIZE_PLAIN_RE.search(plain)
    return m.group(1) if m else None


def _actress_from_conttpc_plain(plain: str) -> Optional[str]:
    """单番号帖正文常见「出演者：」；系列帖用 【演出女優】︰。"""
    m = re.search(r"出演者[：:]\s*([^\n\r<]+)", plain)
    if m:
        return m.group(1).strip()
    m = re.search(r"【演出女優】[︰：:]([^\n]+)", plain)
    if not m:
        return None
    a = m.group(1).strip()
    return None if a == "----" else a


def parse_detail_signal(
    html: str,
    h4_text: Optional[str],
    plain: str,
    img_urls: List[str],
    rmdown_hrefs: List[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    item: Dict[str, Any] = {
        "code": None,
        "title": None,
        "actress": _actress_from_conttpc_plain(plain),
        "size_gb": None,
        "poster_url": img_urls[0] if img_urls else None,
        "torrent_url": rmdown_hrefs[0] if rmdown_hrefs else None,
    }
    if h4_text:
        item["size_gb"] = _h4_size_gb(h4_text) or _size_gb_from_plain(plain)
        cm = CODE_RE.search(h4_text)
        if cm:
            code = cm.group(1)
            item["code"] = code
            rest = h4_text[cm.end() :].strip()
            item["title"] = rest or None
        else:
            errors.append("h4_no_code")
    else:
        errors.append("no_h4_f16")
    if not item.get("size_gb"):
        item["size_gb"] = _size_gb_from_plain(plain)
    if not item.get("torrent_url"):
        errors.append("no_torrent")
    return {"style": "signal", "items": [item], "errors": errors}


def _parse_series_block_text(block: str) -> Optional[Dict[str, Any]]:
    block = block.strip()
    if not block:
        return None
    m = re.search(
        r"(?m)^([A-Z]{2,}-\d+)\s+(.+?)(?=\n【發行日期】)",
        block,
        re.DOTALL,
    )
    if not m:
        m = re.search(r"(?m)^([A-Z]{2,}-\d+)\s+(.+)$", block, re.DOTALL)
    if not m:
        return None
    code, title_line = m.group(1), m.group(2).strip()
    title_line = re.sub(r"\s+", " ", title_line)
    actress_m = re.search(r"【演出女優】[︰：:]([^\n]+)", block)
    actress = actress_m.group(1).strip() if actress_m else None
    if actress == "----":
        actress = None
    size_m = re.search(r"【影片大小】[︰：:]\s*([\d.]+)\s*GB?", block, re.I)
    size_gb = size_m.group(1) if size_m else None
    return {
        "code": code,
        "title": title_line,
        "actress": actress,
        "size_gb": size_gb,
    }


def _img_from_html_segment(seg_html: str) -> Optional[str]:
    for rx in (
        r"ess-data\s*=\s*['\"]([^'\"]+)['\"]",
        r"src\s*=\s*['\"]([^'\"]+)['\"]",
    ):
        for m in re.finditer(rx, seg_html, re.I):
            url = m.group(1).strip()
            if url and "adblo_ck" not in url:
                return url
    return None


def _torrent_from_html_segment(seg_html: str) -> Optional[str]:
    for m in re.finditer(
        r'href\s*=\s*["\']([^"\']*rmdown\.com[^"\']*)["\']',
        seg_html,
        re.I,
    ):
        return m.group(1).strip()
    return None


def parse_detail_series(
    conttpc_html: str,
    plain: str,
    img_urls: List[str],
    rmdown_hrefs: List[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    name_markers = len(re.findall(r"【文件名稱】[︰：:]", plain))
    film_size_markers = len(re.findall(r"【影片大小】[︰：:]", plain))

    if name_markers >= 2:
        parts = re.split(r"(?=【文件名稱】[︰：:])", conttpc_html)
        blocks_html = [p for p in parts[1:] if p.strip()]
        torrent_mode = "multi_torrent" if len(rmdown_hrefs) > 1 else "one_torrent"
        items: List[Dict[str, Any]] = []
        for seg in blocks_html:
            pseg = strip_html_tags_keep_nl(seg)
            base = _parse_series_block_text(pseg)
            if not base:
                continue
            base["poster_url"] = _img_from_html_segment(seg)
            base["torrent_url"] = _torrent_from_html_segment(seg)
            items.append(base)
        if torrent_mode == "one_torrent" and rmdown_hrefs:
            shared = rmdown_hrefs[0]
            for it in items:
                if not it.get("torrent_url"):
                    it["torrent_url"] = shared
        if torrent_mode == "multi_torrent" and len(items) != len(rmdown_hrefs):
            errors.append(
                f"torrent_count_mismatch items={len(items)} torrents={len(rmdown_hrefs)}"
            )
            for i, it in enumerate(items):
                if i < len(rmdown_hrefs):
                    it["torrent_url"] = rmdown_hrefs[i]
        return {
            "style": "series_multi_header",
            "torrent_mode": torrent_mode,
            "items": items,
            "errors": errors,
        }

    # 单段头部 + 多番号行（detail-xilie）：用 walk 的 plain（含 br 换行），勿用压成一行的 strip
    body_plain = plain
    m = re.search(r"【種子條件】[︰：:][^\n]*(?:\n|$)", plain)
    if m:
        body_plain = plain[m.end() :].lstrip()

    chunks = re.split(r"(?=\n[A-Z]{2,}-\d+\s)", "\n" + body_plain.strip())
    blocks_text = [
        c.strip()
        for c in chunks
        if c.strip() and re.match(r"^[A-Z]{2,}-\d+\s", c.strip(), re.M)
    ]
    parsed_blocks = []
    for b in blocks_text:
        pb = _parse_series_block_text(b)
        if pb:
            parsed_blocks.append(pb)

    torrent_mode = "one_torrent" if len(rmdown_hrefs) <= 1 else "multi_torrent"
    items = []
    for i, pb in enumerate(parsed_blocks):
        it = dict(pb)
        if i < len(img_urls):
            it["poster_url"] = img_urls[i]
        else:
            it["poster_url"] = None
        if torrent_mode == "one_torrent" and rmdown_hrefs:
            it["torrent_url"] = rmdown_hrefs[0]
        elif torrent_mode == "multi_torrent" and i < len(rmdown_hrefs):
            it["torrent_url"] = rmdown_hrefs[i]
        else:
            it["torrent_url"] = None
        items.append(it)

    if len(parsed_blocks) != len(img_urls) and parsed_blocks:
        errors.append(f"img_count_mismatch blocks={len(parsed_blocks)} imgs={len(img_urls)}")
    if torrent_mode == "multi_torrent" and len(parsed_blocks) != len(rmdown_hrefs):
        errors.append(
            f"torrent_block_mismatch blocks={len(parsed_blocks)} torrents={len(rmdown_hrefs)}"
        )

    return {
        "style": "series_single_header",
        "torrent_mode": torrent_mode,
        "items": items,
        "errors": errors,
    }


def _is_strong_series(plain: str, rmdown_count: int, film_size_count: int) -> bool:
    if len(re.findall(r"【文件名稱】[︰：:]", plain)) >= 2:
        return True
    if film_size_count >= 2:
        return True
    if rmdown_count > 1:
        return True
    return False


def parse_detail_page(html: str) -> Dict[str, Any]:
    """
    统一入口。返回 dict:
    style, torrent_mode (series), items[], errors[], h4_text, conttpc_plain (摘要可不存库).
    """
    h4_text = parse_h4_f16(html)
    conttpc_html = extract_conttpc_html(html)
    if not conttpc_html:
        plain, imgs, rms = walk_conttpc(html)
        return {
            "style": "unknown",
            "items": [],
            "errors": ["no_conttpc"],
            "h4_text": h4_text,
        }

    plain, img_urls, rmdown_hrefs = walk_conttpc(html)

    if not plain and conttpc_html:
        plain = strip_html_tags(re.sub(r"<br\s*/?>", "\n", conttpc_html, flags=re.I))

    film_sizes = len(re.findall(r"【影片大小】[︰：:]", plain))
    strong = _is_strong_series(plain, len(rmdown_hrefs), film_sizes)

    if strong:
        out = parse_detail_series(conttpc_html, plain, img_urls, rmdown_hrefs)
        out["h4_text"] = h4_text
        return out

    out = parse_detail_signal(html, h4_text, plain, img_urls, rmdown_hrefs)
    out["torrent_mode"] = "single"
    out["h4_text"] = h4_text
    return out


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

_TITLE_REMOVE = re.compile(
    r"\[中字\]|\[中文字幕\]|《FHD中文》|\[HD\]|\[有碼高清中文字幕\]"
)


def _clean_title(title: str) -> str:
    title = title.replace("【", "[").replace("】", "]")
    title = _TITLE_REMOVE.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def parse_detail_items(html: str) -> List[Dict[str, Any]]:
    """解析详情页 HTML，返回标准化条目列表。

    每个条目字段：``code``, ``code_title``, ``actress``, ``size_gb``, ``img_url``, ``torrent_url``。
    """
    result = parse_detail_page(html)
    items: List[Dict[str, Any]] = []
    for raw in result.get("items", []):
        code = raw.get("code") or ""
        actress = raw.get("actress")
        size_gb = raw.get("size_gb")
        title = _clean_title(raw.get("title") or "")
        if actress:
            title = title.replace(actress, "").strip()

        parts: List[str] = []
        if actress:
            parts.append(actress)
        if code:
            parts.append(code)
        if size_gb:
            parts.append(f"{size_gb}G")
        if title:
            parts.append(title)
        code_title = " ".join(parts)

        items.append({
            "code": code,
            "code_title": code_title,
            "actress": actress,
            "size_gb": size_gb,
            "img_url": raw.get("poster_url"),
            "torrent_url": raw.get("torrent_url"),
        })
    return items


def fetch_and_parse_detail(url: str) -> List[Dict[str, Any]]:
    """拉取详情页并解析，返回 ``[{"code", "code_title", "actress", "size_gb", "img_url", "torrent_url"}, ...]``。"""
    html = _fetch_html(url)
    return parse_detail_items(html)
