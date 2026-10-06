#!/usr/bin/env python3
"""
code_title → 简体中文（爬虫入库与 web_viewer 共用）。

依赖：pip install deep-translator（可选；未安装时非中文标题会回退为原文）。
"""
from __future__ import annotations

import logging
import time

try:
    from deep_translator import GoogleTranslator as _GoogleTranslator
except ImportError:
    _GoogleTranslator = None

_TITLE_ZH_MEMO_MAX = 4096
_title_zh_ok: dict[str, str] = {}
_tl_google = None
_tl_last_ts: float = 0.0
_TL_MIN_GAP_S = 0.22
_fail_streak = 0
_circuit_open = False
_FAIL_STREAK_OPEN = 3

log = logging.getLogger(__name__)


def _looks_like_translate_error(zh: str) -> bool:
    """识别谷歌翻译返回的 500/限流错误页，避免当作成译文。"""
    compact = (
        (zh or "")
        .lower()
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\xa0", " ")
    )
    markers = (
        "error 500",
        "error 429",
        "error 503",
        "server error",
        "that's an error",
        "there was an error",
        "please try again later",
        "that's all we know",
        "!!1500",
    )
    return any(m in compact for m in markers)


def _mostly_chinese_title(s: str) -> bool:
    """汉字（CJK 统一表意文字）占比超过 60%（不含正好 60%）视为已是中文，不再翻译。"""
    t = s.strip()
    if not t:
        return False
    n_cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    return (n_cjk / len(t)) > 0.60


def _memo_put_zh(original: str, zh: str) -> None:
    if len(_title_zh_ok) >= _TITLE_ZH_MEMO_MAX:
        for _ in range(_TITLE_ZH_MEMO_MAX // 2):
            try:
                _title_zh_ok.pop(next(iter(_title_zh_ok)))
            except StopIteration:
                break
    _title_zh_ok[original] = zh


def _google_tl():
    global _tl_google
    if _GoogleTranslator is None:
        return None
    if _tl_google is None:
        _tl_google = _GoogleTranslator(source="auto", target="zh-CN")
    return _tl_google


def reset_translate_circuit() -> None:
    """测试与新一轮补译前复位熔断。"""
    global _fail_streak, _circuit_open
    _fail_streak = 0
    _circuit_open = False


def translation_circuit_open() -> bool:
    return _circuit_open


def _looks_like_rate_limit(text: str) -> bool:
    low = (text or "").lower()
    return any(
        token in low
        for token in ("429", "too many requests", "/sorry/", "rate limit")
    )


def _note_translate_failure(*, rate_limited: bool = False) -> None:
    global _fail_streak, _circuit_open
    _fail_streak += 1
    if rate_limited or _fail_streak >= _FAIL_STREAK_OPEN:
        if not _circuit_open:
            _circuit_open = True
            log.warning(
                "翻译熔断已打开 rate_limited=%s streak=%d，停止后续 Google 请求",
                rate_limited,
                _fail_streak,
            )


def _note_translate_success() -> None:
    global _fail_streak
    _fail_streak = 0


def translate_title_to_zh(title: str) -> str:
    """调用谷歌网页翻译；限流/异常时重试。成功才写入内存缓存，绝不缓存空串。"""
    t = (title or "").strip()
    if not t:
        return ""
    hit = _title_zh_ok.get(t)
    if hit is not None:
        return hit
    if _circuit_open:
        return ""
    tl = _google_tl()
    if tl is None:
        return ""
    global _tl_last_ts
    backoff = 1.2
    last_rate_limited = False
    for attempt in range(6):
        if _circuit_open:
            return ""
        try:
            gap = time.monotonic() - _tl_last_ts
            if gap < _TL_MIN_GAP_S:
                time.sleep(_TL_MIN_GAP_S - gap)
            zh = (tl.translate(t) or "").strip()
            _tl_last_ts = time.monotonic()
            if _looks_like_translate_error(zh) or _looks_like_rate_limit(zh):
                last_rate_limited = _looks_like_rate_limit(zh)
                zh = ""
                if last_rate_limited:
                    _note_translate_failure(rate_limited=True)
                    return ""
            if zh:
                _memo_put_zh(t, zh)
                _note_translate_success()
                print(f"translate_title_to_zh: {t} -> {zh}")
                return zh
        except Exception as exc:
            _tl_last_ts = time.monotonic()
            last_rate_limited = _looks_like_rate_limit(str(exc))
            if last_rate_limited:
                _note_translate_failure(rate_limited=True)
                return ""
            time.sleep(backoff)
            backoff = min(backoff * 1.75, 14.0)
    _note_translate_failure(rate_limited=last_rate_limited)
    return ""


def translate_code_title(code_title: str | None) -> str:
    """
    传入详情解析得到的 code_title，返回用于入库/展示的标题字符串。

    - 空文本：返回 \"\"。
    - 汉字占比超过 60%：不调用翻译，返回原文。
    - 否则请求翻译；失败或与原文相同：返回原文。
    """
    t = (code_title or "").strip()
    if not t:
        return ""
    if _mostly_chinese_title(t):
        return t
    zh = translate_title_to_zh(t)
    if not zh or zh == t:
        return t
    return zh


def title_transfer_value(code_title: str | None) -> str | None:
    """入库用译文：跳过/失败/与原文相同则返回 None，避免覆盖已有 title_transfer。"""
    t = (code_title or "").strip()
    if not t or _mostly_chinese_title(t):
        return None
    zh = (translate_title_to_zh(t) or "").strip()
    if not zh or zh == t:
        return None
    return zh
