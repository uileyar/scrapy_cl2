#!/usr/bin/env python3
"""
按图片直链下载到本地。
对外入口：download_image_from_url
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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


_WP_PROXY_RE = re.compile(r"^https?://i[0-3]\.wp\.com/", re.I)


def _unwrap_wp_proxy(url: str) -> str:
    """将 i{0-3}.wp.com 代理 URL 还原为原始直链。"""
    if _WP_PROXY_RE.match(url):
        real = _WP_PROXY_RE.sub("https://", url)
        parsed = urlparse(real)
        return parsed._replace(query="").geturl()
    return url


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
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer.strip()
    if session_headers:
        headers.update(session_headers)

    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
        cd = resp.headers.get("Content-Disposition")
        final_url = resp.geturl()
        ct = resp.headers.get("Content-Type")

    if filename:
        guessed = _guess_filename(final_url or url, ct)
        ext = Path(guessed).suffix or ".jpg"
        name = _safe_name(filename + ext)
    else:
        cd_name = _filename_from_disposition(cd)
        name = cd_name or _guess_filename(final_url or url, ct)
        name = _safe_name(name)

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
