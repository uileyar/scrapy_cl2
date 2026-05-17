#!/usr/bin/env python3
"""
code_title → 简体中文（爬虫入库与 web_viewer 共用）。

依赖：pip install deep-translator（可选；未安装时非中文标题会回退为原文）。
"""
from __future__ import annotations

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


def translate_title_to_zh(title: str) -> str:
    """调用谷歌网页翻译；限流/异常时重试。成功才写入内存缓存，绝不缓存空串。"""
    t = (title or "").strip()
    if not t:
        return ""
    hit = _title_zh_ok.get(t)
    if hit is not None:
        return hit
    tl = _google_tl()
    if tl is None:
        return ""
    global _tl_last_ts
    backoff = 1.2
    for attempt in range(6):
        try:
            gap = time.monotonic() - _tl_last_ts
            if gap < _TL_MIN_GAP_S:
                time.sleep(_TL_MIN_GAP_S - gap)
            zh = (tl.translate(t) or "").strip()
            _tl_last_ts = time.monotonic()
            if zh:
                _memo_put_zh(t, zh)
                print(f"translate_title_to_zh: {t} -> {zh}")
                return zh
        except Exception:
            _tl_last_ts = time.monotonic()
            time.sleep(backoff)
            backoff = min(backoff * 1.75, 14.0)
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
