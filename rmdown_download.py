#!/usr/bin/env python3
"""
从 rmdown link 页面拉取 HTML，解析 des/esc/axs/reff/ref，拼接 download.php 并下载种子。
对外入口：download_from_rmdown_url

使用 curl_cffi 模拟 Chrome TLS/HTTP2 指纹以绕过 Cloudflare；
代理：参数 proxy > 环境变量 > 默认 Clash 7897；失败可回退直连。
页面与下载共用 Session（自动维持 Cookie）。
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "需要安装 curl_cffi：pip install curl_cffi"
    ) from e

# Clash Verge 默认代理；设环境变量 RMDOWN_PROXY=direct 可强制直连
_DEFAULT_PROXY = "http://127.0.0.1:7897"
_DIRECT_SENTINELS = frozenset({"", "direct", "none", "off", "0"})

# curl_cffi 会按此生成匹配的 UA / TLS 指纹；勿再手动覆盖 User-Agent
_IMPERSONATE = os.environ.get("RMDOWN_IMPERSONATE", "chrome")

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
    """解析页面中 type=hidden 的 input 字段。"""
    parser = _HiddenInputsParser()
    parser.feed(html)
    return parser.fields


def build_download_url(fields: dict[str, str], base: str = DOWNLOAD_BASE) -> str:
    """根据隐藏字段拼接 download.php URL。"""
    missing = [n for n in REQUIRED_NAMES if not fields.get(n)]
    if missing:
        raise ValueError(f"缺少隐藏字段: {', '.join(missing)}")
    query = {n: fields[n] for n in REQUIRED_NAMES}
    return f"{base.rstrip('/')}?{urlencode(query)}"


def resolve_proxy(proxy: str | None = None) -> Optional[str]:
    """
    解析代理地址。

    参数:
        proxy: 显式代理；None 表示走环境变量/默认；
               ``direct`` / ``none`` / 空串表示强制直连。

    返回:
        代理 URL，或 None 表示直连。
    """
    if proxy is not None:
        raw = proxy.strip()
        if raw.lower() in _DIRECT_SENTINELS:
            return None
        return raw or None

    env = (
        os.environ.get("RMDOWN_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if env is not None:
        raw = env.strip()
        if raw.lower() in _DIRECT_SENTINELS:
            return None
        return raw or None

    return _DEFAULT_PROXY


def build_session(proxy: str | None = None, *, timeout: int = 60) -> Any:
    """
    构建 curl_cffi Session（Chrome 指纹 + Cookie）。

    参数:
        proxy: 代理 URL；None 表示直连（trust_env=False，不吃系统/环境代理）。
        timeout: 默认请求超时秒数。
    """
    kwargs: dict[str, Any] = {
        "impersonate": _IMPERSONATE,
        "timeout": timeout,
        "trust_env": False,  # 避免 Windows Clash 系统代理污染「直连」
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return cffi_requests.Session(**kwargs)


def _filename_from_disposition(cd: str | None) -> str | None:
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?("?)([^";\n]+)\1', cd, re.I)
    if m:
        return m.group(2).strip()
    m = re.findall(r'filename=("?)([^";\n]+)\1?', cd, re.I)
    return m[-1][1].strip() if m else None


def _safe_torrent_name(stem: str) -> str:
    stem = stem.replace("\\", "_").replace("/", "_")
    stem = re.sub(r'[<>:"|?*]', "_", stem)
    return (stem.strip() or "torrent") + ".torrent"


def _looks_like_cf_challenge(body: bytes | str) -> bool:
    """识别 Cloudflare 人机验证页（Just a moment...）。"""
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else body
    low = text.lower()
    return (
        "just a moment" in low
        or "cf-mitigated" in low
        or "cf-browser-verification" in low
        or "challenges.cloudflare.com" in low
    )


def _is_fallback_error(err: BaseException) -> bool:
    """判断是否值得从代理回退到直连。"""
    msg = str(err).lower()
    return any(
        token in msg
        for token in (
            "403",
            "407",
            "429",
            "502",
            "503",
            "520",
            "521",
            "522",
            "523",
            "524",
            "ssl",
            "eof",
            "timed out",
            "timeout",
            "connection refused",
            "connection reset",
            "forbidden",
            "proxy",
            "cloudflare",
            "人机验证",
            "curl:",
            "failed to connect",
        )
    )


def _check_response(resp: Any, *, expect_html: bool = False) -> None:
    """检查响应状态与 CF 挑战页。"""
    body = resp.content or b""
    if _looks_like_cf_challenge(body):
        raise RuntimeError(
            f"HTTP {resp.status_code}: Cloudflare 人机验证拦截（Just a moment...），"
            f"curl_cffi impersonate={_IMPERSONATE} 仍未通过；"
            "可换节点 / 设 DIRECT / 调整 RMDOWN_IMPERSONATE"
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.reason or 'error'}")
    if expect_html and not body:
        raise RuntimeError("页面响应为空")


def _merge_headers(
    base: dict[str, str],
    session_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """合并请求头；支持环境变量 RMDOWN_COOKIE（浏览器过验证后的 Cookie）。"""
    headers = dict(base)
    if session_headers:
        headers.update(session_headers)
    env_cookie = os.environ.get("RMDOWN_COOKIE", "").strip()
    if env_cookie and not any(k.lower() == "cookie" for k in headers):
        headers["Cookie"] = env_cookie
    return headers


def _fetch_text(
    url: str,
    session: Any,
    *,
    referer: str | None = None,
    session_headers: dict[str, str] | None = None,
) -> str:
    """用 Session 拉取 HTML 文本。"""
    headers = _merge_headers(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": referer or "https://www.rmdown.com/",
        },
        session_headers,
    )
    resp = session.get(url, headers=headers)
    _check_response(resp, expect_html=True)
    return resp.text


def download_torrent(
    url: str,
    out_dir: Path,
    session_headers: dict[str, str] | None = None,
    *,
    referer: str | None = None,
    override_name: str | None = None,
    session: Any | None = None,
    proxy: str | None = None,
    timeout: int = 60,
) -> Path:
    """
    下载种子文件到目录。

    参数:
        session: 外部 Session（与页面请求共享 Cookie）；为 None 时按 proxy 新建。
        proxy: 仅在 session 为 None 时生效；见 resolve_proxy。
    """
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    own_session = session is None
    if session is None:
        session = build_session(resolve_proxy(proxy), timeout=timeout)

    headers = _merge_headers(
        {
            "Accept": "*/*",
            "Referer": referer or "https://www.rmdown.com/",
        },
        session_headers,
    )

    try:
        resp = session.get(url, headers=headers)
        _check_response(resp)
        data = resp.content or b""
        if _looks_like_cf_challenge(data):
            raise RuntimeError("download.php 返回 Cloudflare 人机验证页，不是种子文件")
        if not data.startswith(b"d") and b"announce" not in data[:200]:
            # 非严格校验：多数 .torrent 以 bencode dict 'd' 开头
            log.warning("下载内容可能不是 torrent（未以 bencode dict 开头），仍写入文件")
        cd = resp.headers.get("Content-Disposition")
        name = _filename_from_disposition(cd)
    finally:
        if own_session:
            session.close()

    if override_name:
        name = _safe_torrent_name(override_name)
    elif not name or "." not in name:
        q = urlparse(url).query
        ref_m = re.search(r"ref=([^&]+)", q)
        stub = ref_m.group(1)[:16] if ref_m else "torrent"
        name = f"{stub}.torrent"

    path = out_dir / name
    if path.exists():
        # 已有非空种子则复用，避免生成 stem_1.torrent
        try:
            if path.suffix.lower() == ".torrent" and path.stat().st_size > 0:
                return path
        except OSError:
            pass
        stem, suf = path.stem, path.suffix
        n = 1
        while path.exists():
            path = out_dir / f"{stem}_{n}{suf}"
            n += 1

    path.write_bytes(data)
    return path


def _download_once(
    page_url: str,
    out_dir: Path,
    *,
    filename: str | None,
    download_base: str,
    session_headers: dict[str, str] | None,
    proxy: str | None,
    timeout: int,
) -> Path:
    """单次完整流程：Session → 拉页面 → 下种子（同一 Session）。"""
    with build_session(proxy, timeout=timeout) as session:
        mode = f"proxy={proxy}" if proxy else "direct"
        log.debug("rmdown 请求模式: %s impersonate=%s", mode, _IMPERSONATE)

        html = _fetch_text(
            page_url,
            session,
            referer="https://www.rmdown.com/",
            session_headers=session_headers,
        )
        fields = parse_hidden_fields(html)
        dl_url = build_download_url(fields, base=download_base)
        return download_torrent(
            dl_url,
            out_dir,
            session_headers=session_headers,
            referer=page_url,
            override_name=filename,
            session=session,
            timeout=timeout,
        )


def download_from_rmdown_url(
    page_url: str,
    out_dir: str | Path,
    *,
    filename: str | None = None,
    download_base: str = DOWNLOAD_BASE,
    session_headers: dict[str, str] | None = None,
    retries: int = 3,
    proxy: str | None = None,
    fallback_direct: bool = True,
    timeout: int = 60,
) -> Path:
    """
    打开 rmdown 资源页（如 link.php?hash=...），解析隐藏表单字段并下载种子。

    参数:
        page_url: 页面完整 URL
        out_dir: 保存目录
        filename: 文件名（不含扩展名），自动补 ``.torrent``；为 None 时由服务器响应决定
        download_base: download.php 的 URL（不含查询串）
        session_headers: 附加请求头（拉取页面与下载种子时都会合并）
        retries: 最大重试次数（含首次请求）
        proxy: 代理地址；None 走环境变量/默认 7897；``direct`` 强制直连
        fallback_direct: 代理失败时是否回退直连（同一次 attempt 内）
        timeout: 单次 HTTP 超时秒数

    返回:
        已写入文件的绝对路径（pathlib.Path）
    """
    page_url = page_url.strip()
    if not page_url:
        raise ValueError("page_url 为空")

    resolved = resolve_proxy(proxy)
    out_path = Path(out_dir)

    modes: list[Optional[str]] = []
    if resolved:
        modes.append(resolved)
        if fallback_direct:
            modes.append(None)
    else:
        modes.append(None)

    last_err: Exception | None = None
    for attempt in range(retries):
        for mode_proxy in modes:
            label = f"proxy={mode_proxy}" if mode_proxy else "direct"
            try:
                saved = _download_once(
                    page_url,
                    out_path,
                    filename=filename,
                    download_base=download_base,
                    session_headers=session_headers,
                    proxy=mode_proxy,
                    timeout=timeout,
                )
                if mode_proxy is None and resolved is not None:
                    log.info("代理不可用，已直连成功: %s", page_url)
                return saved.resolve()
            except Exception as e:
                last_err = e
                can_fallback = (
                    mode_proxy is not None
                    and fallback_direct
                    and None in modes
                    and _is_fallback_error(e)
                )
                if can_fallback:
                    log.warning(
                        "种子下载失败 (%s, %d/%d): %s，回退直连",
                        label,
                        attempt + 1,
                        retries,
                        e,
                    )
                    continue
                break

        if attempt < retries - 1:
            wait = 2.0 * (2**attempt)
            log.warning(
                "种子下载失败 (%d/%d): %s，%.0fs 后重试",
                attempt + 1,
                retries,
                last_err,
                wait,
            )
            time.sleep(wait)

    raise RuntimeError(f"种子下载失败，已重试 {retries} 次: {last_err}") from last_err


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="从 rmdown 资源页下载种子（curl_cffi）")
    parser.add_argument("page_url", help="资源页 URL")
    parser.add_argument("out_dir", help="保存目录")
    parser.add_argument(
        "--proxy",
        default=None,
        help="代理 URL；传 direct 强制直连；默认读环境变量或 127.0.0.1:7897",
    )
    parser.add_argument(
        "--no-fallback-direct",
        action="store_true",
        help="代理失败时不回退直连",
    )
    args = parser.parse_args()
    path = download_from_rmdown_url(
        args.page_url,
        args.out_dir,
        proxy=args.proxy,
        fallback_direct=not args.no_fallback_direct,
    )
    print(path)


if __name__ == "__main__":
    main()
