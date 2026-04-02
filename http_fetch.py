"""HTML 拉取（统一 User-Agent 与重试）。"""
from __future__ import annotations

import logging
import time
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_html(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    user_agent: str | None = None,
) -> str:
    """拉取页面 HTML，失败则指数退避重试。"""
    ua = user_agent or DEFAULT_UA
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": ua})
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = 2.0 * (2**attempt)
                log.warning(
                    "拉取失败 (%d/%d) %s: %s，%.0fs 后重试",
                    attempt + 1,
                    retries,
                    url,
                    e,
                    wait,
                )
                time.sleep(wait)
    raise RuntimeError(f"拉取失败，已重试 {retries} 次: {last_err}") from last_err
