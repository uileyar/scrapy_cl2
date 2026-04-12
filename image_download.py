#!/usr/bin/env python3
"""
按图片直链下载到本地。
对外入口：download_image_from_url
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
}


def _filename_from_disposition(cd: str | None) -> str | None:
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?("?)([^";\n]+)\1', cd, re.I)
    if m:
        return m.group(2).strip()
    m = re.findall(r'filename=("?)([^";\n]+)\1?', cd, re.I)
    return m[-1][1].strip() if m else None


def _guess_filename(image_url: str, content_type: str | None) -> str:
    parsed = urlparse(image_url)
    path = parsed.path or ""
    base = Path(path).name if path else ""
    name: str | None = None
    if base and re.match(r"^[\w.\-+%]+$", base, re.I):
        name = base
    if not name or "." not in name:
        ct = (content_type or "").split(";")[0].strip().lower()
        ext = _MIME_EXT.get(ct, ".jpg")
        slug = Path(parsed.path).stem or "image"
        slug = re.sub(r"[^\w\-]+", "_", slug)[:80] or "image"
        name = f"{slug}{ext}"
    return name


def _safe_name(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*]', "_", name)
    return name.strip() or "image.jpg"


_MAGIC_EXT: list[tuple[bytes, str]] = [
    (b"\x89PNG", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
]


def _detect_image_ext(data: bytes) -> str | None:
    """识别 data 的真实图片格式，非图片返回 None。"""
    if len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    for magic, ext in _MAGIC_EXT:
        if data[: len(magic)] == magic:
            return ext
    return None


def _fix_ext_by_magic(name: str, data: bytes) -> str:
    """根据文件内容的 magic bytes 修正扩展名。"""
    real_ext = _detect_image_ext(data)
    if real_ext is None:
        return name
    stem, cur_ext = Path(name).stem, Path(name).suffix
    if cur_ext.lower() != real_ext:
        return stem + real_ext
    return name


_WP_PROXY_RE = re.compile(r"^https?://i[0-3]\.wp\.com/", re.I)


def _unwrap_wp_proxy(url: str) -> str:
    """将 i{0-3}.wp.com 代理 URL 还原为原始直链。"""
    if _WP_PROXY_RE.match(url):
        real = _WP_PROXY_RE.sub("https://", url)
        parsed = urlparse(real)
        return parsed._replace(query="").geturl()
    return url


_HOST_REPLACE: dict[str, str] = {
    "picdcd.com": "odjsk.com",
    "gdvdvb.com": "odjsk.com",
    "adipcd.com": "odjsk.com",
}


def _replace_host(url: str) -> str:
    """按映射表替换 URL 中的域名。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    new_host = _HOST_REPLACE.get(host)
    if new_host is None:
        return url
    netloc = parsed.netloc.replace(host, new_host, 1)
    return urlunparse(parsed._replace(netloc=netloc))


def _do_fetch(url: str, headers: dict[str, str]) -> tuple[bytes, str | None, str, str | None]:
    """发起 GET 请求，返回 (data, content_disposition, final_url, content_type)。"""
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=60) as resp:
        return (
            resp.read(),
            resp.headers.get("Content-Disposition"),
            resp.geturl(),
            resp.headers.get("Content-Type"),
        )


def _fetch_with_http_fallback(url: str, headers: dict[str, str]) -> tuple[bytes, str | None, str, str | None]:
    """尝试下载，HTTPS 失败则降级 HTTP 重试。"""
    try:
        return _do_fetch(url, headers)
    except URLError as exc:
        if not (url.startswith("https://") and isinstance(exc.reason, OSError)):
            raise
        http_url = url.replace("https://", "http://", 1)
        log.warning("HTTPS 失败，降级 HTTP 重试: %s -> %s", url, http_url)
        return _do_fetch(http_url, headers)


def _fetch(url: str, headers: dict[str, str]) -> tuple[bytes, str | None, str, str | None]:
    """下载图片，依次尝试: 原始URL → HTTP降级 → 域名替换 → 替换后HTTP降级。"""
    alt_url = _replace_host(url)
    has_alt = alt_url != url

    try:
        result = _fetch_with_http_fallback(url, headers)
    except (URLError, OSError):
        if not has_alt:
            raise
        log.warning("原始域名失败，尝试替换域名: %s -> %s", url, alt_url)
        return _fetch_with_http_fallback(alt_url, headers)

    if has_alt and not _detect_image_ext(result[0]):
        log.warning("原始域名返回非图片数据，尝试替换域名: %s -> %s", url, alt_url)
        return _fetch_with_http_fallback(alt_url, headers)

    return result


def download_image_from_url(
    image_url: str,
    out_dir: str | Path,
    *,
    filename: str | None = None,
    referer: str | None = None,
    session_headers: dict[str, str] | None = None,
) -> Path:
    """
    根据图片直链下载文件。

    :param image_url: 完整图片 URL
    :param out_dir: 保存目录
    :param filename: 文件名（不含扩展名），扩展名自动从 Content-Type 或 URL 推断；
                     为 None 时由 URL / Content-Disposition 决定完整文件名
    :param referer: 可选 Referer（部分 CDN / 防盗链需要填来源页）
    :param session_headers: 附加请求头
    :return: 已保存文件的绝对路径（pathlib.Path）
    """
    url = image_url.strip() if isinstance(image_url, str) else str(image_url).strip()
    if not url:
        raise ValueError("image_url 为空")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("image_url 须为 http(s) 地址")
    url = _unwrap_wp_proxy(url)

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "image/png,image/jpeg,image/gif,image/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer.strip()
    if session_headers:
        headers.update(session_headers)

    data, cd, final_url, ct = _fetch(url, headers)

    if not data:
        raise ValueError(f"下载内容为空: {url}")
    if _detect_image_ext(data) is None:
        snippet = data[:64].decode("utf-8", errors="replace")
        raise ValueError(
            f"下载内容不是图片 ({len(data)} bytes, 开头: {snippet!r}): {url}"
        )

    if filename:
        guessed = _guess_filename(final_url or url, ct)
        ext = Path(guessed).suffix or ".jpg"
        name = _safe_name(filename + ext)
    else:
        cd_name = _filename_from_disposition(cd)
        name = cd_name or _guess_filename(final_url or url, ct)
        name = _safe_name(name)

    name = _fix_ext_by_magic(name, data)
    path = out_dir / name
    if path.exists():
        stem, suf = path.stem, path.suffix
        n = 1
        while path.exists():
            path = out_dir / f"{stem}_{n}{suf}"
            n += 1

    path.write_bytes(data)
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="按图片直链下载到本地")
    parser.add_argument("image_url", help="图片完整 URL")
    parser.add_argument("out_dir", help="保存目录")
    parser.add_argument(
        "--referer",
        default=None,
        help="可选 Referer（防盗链站点可能需要）",
    )
    args = parser.parse_args()
    try:
        saved = download_image_from_url(
            args.image_url,
            args.out_dir,
            referer=args.referer,
        )
        print(saved)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
