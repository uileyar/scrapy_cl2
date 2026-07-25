import csv
import re
import time
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Set, Tuple

import requests

from crawl_to_sqlite import ensure_db, replace_actress_names_from_csv

ZH_API = "https://zh.wikipedia.org/w/api.php"
JA_API = "https://ja.wikipedia.org/w/api.php"
ZH_CATEGORY = "Category:日本AV女優"
JA_CATEGORY = "Category:日本のAV女優"
OUT_CSV = "/mnt/d/myworking/rtb-doc/actress_names.csv"
OUT_DB = "D:/data/cl_db/cl.db"

# MediaWiki 要求可识别的 User-Agent；默认 python-requests 会被 403
HEADERS = {
    "User-Agent": "youyou-bot/1.0 (personal research; contact: local-script)",
    "Accept": "application/json",
}

DISCARD_TITLES = {
    "AV女優",
    "AV事務所",
    "日本のAV女優一覧",
    "AV女优",
    "AV女優のアジア進出",
    "AV女優板",
    "@YOU",
    "東京愛情動作故事",
    "Template:AV女優",
}

# 去掉维基消歧后缀：末尾 (...) / （...），兼容半角/全角括号与前导空格
SUFFIX_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*$")


def log(msg: str) -> None:
    """打印进度并立即刷新。"""
    print(msg, flush=True)


def wiki_get(api_url: str, params: dict) -> dict:
    """带 429 退避的 MediaWiki GET。"""
    for attempt in range(10):
        r = requests.get(api_url, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 429:
            wait = min(60, 2**attempt)
            log(f"429，等待 {wait}s ...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate limited: {api_url}")


def fetch_category_pages(api_url: str, category: str) -> Set[str]:
    """分页拉取分类下页面标题（不含子分类递归）。"""
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmlimit": "500",
        "cmtype": "page",
        "format": "json",
    }
    pages: Set[str] = set()
    while True:
        data = wiki_get(api_url, params)
        batch = data.get("query", {}).get("categorymembers", [])
        for m in batch:
            pages.add(m["title"])
        log(f"  已取 {len(pages)} ...")
        cont = data.get("continue")
        if not cont:
            break
        params.update(cont)
        time.sleep(0.5)
    return pages


def batched(items: Iterable[str], size: int) -> Iterator[list]:
    """按固定大小切分批次。"""
    batch: list = []
    for x in items:
        batch.append(x)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def fetch_langlinks(api_url: str, titles: Set[str], lllang: str, label: str) -> Dict[str, str]:
    """批量查询条目标题到目标语言的 langlinks。"""
    mapping: Dict[str, str] = {}
    ordered = sorted(titles)
    total = (len(ordered) + 49) // 50
    for i, batch in enumerate(batched(ordered, 50), 1):
        params = {
            "action": "query",
            "prop": "langlinks",
            "lllang": lllang,
            "lllimit": "max",
            "titles": "|".join(batch),
            "format": "json",
        }
        data = wiki_get(api_url, params)
        for page in data.get("query", {}).get("pages", {}).values():
            title = page.get("title")
            links = page.get("langlinks") or []
            if title and links:
                mapping[title] = links[0].get("*") or ""
        if i % 10 == 0 or i == total:
            log(f"  {label} {i}/{total}，命中 {len(mapping)}")
        time.sleep(1.2)
    return mapping


def clean_name(name: str) -> str:
    """去除末尾括号消歧后缀，并去掉首尾空格。"""
    if not name:
        return ""
    s = name.strip()
    while True:
        nxt = SUFFIX_RE.sub("", s).strip()
        if nxt == s:
            return nxt
        s = nxt


def should_discard(ja: str, zh: str) -> bool:
    """判断是否为非人名噪声条目。"""
    return ja in DISCARD_TITLES or zh in DISCARD_TITLES


def build_pairs(
    zh_names: Set[str],
    ja_names: Set[str],
    ja_to_zh: Dict[str, str],
    zh_to_ja: Dict[str, str],
) -> List[Tuple[str, str]]:
    """
    构建日文/中文两列对照行。
    缺一侧留空；同一人只保留一行。
    """
    rows: Set[Tuple[str, str]] = set()
    covered_zh: Set[str] = set()

    for ja in ja_names:
        zh = ja_to_zh.get(ja, "")
        if not zh and ja in zh_names:
            zh = ja
        if should_discard(ja, zh):
            continue
        rows.add((ja, zh))
        if zh:
            covered_zh.add(zh)

    for zh in zh_names:
        if zh in covered_zh or zh in DISCARD_TITLES:
            continue
        ja = zh_to_ja.get(zh, "")
        if should_discard(ja, zh):
            continue
        # 若反向链指向的日文已有行，且该行中文为空，则补中文；否则新增仅中文行
        if ja:
            existing = {(j, z) for j, z in rows if j == ja}
            if existing:
                for j, z in list(existing):
                    if not z:
                        rows.discard((j, z))
                        rows.add((j, zh))
                        covered_zh.add(zh)
                if zh in covered_zh:
                    continue
            rows.add((ja, zh))
            covered_zh.add(zh)
        else:
            rows.add(("", zh))

    return sorted(rows, key=lambda x: (x[0], x[1]))


def clean_rows(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """落盘前清洗两列名字并去重。"""
    cleaned: Set[Tuple[str, str]] = set()
    for ja, zh in rows:
        ja_c = clean_name(ja)
        zh_c = clean_name(zh)
        if not ja_c and not zh_c:
            continue
        if should_discard(ja_c, zh_c):
            continue
        cleaned.add((ja_c, zh_c))
    return sorted(cleaned, key=lambda x: (x[0], x[1]))


def write_csv(path: str, rows: List[Tuple[str, str]]) -> None:
    """写入日文/中文两列 CSV。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["日文", "中文"])
        w.writerows(rows)


if __name__ == "__main__":
    log("拉取中文维基...")
    names_zh = fetch_category_pages(ZH_API, ZH_CATEGORY)
    log(f"中文: {len(names_zh)}")

    log("拉取日文维基...")
    names_ja = fetch_category_pages(JA_API, JA_CATEGORY)
    log(f"日文: {len(names_ja)}")

    log("查询日文→中文 langlinks...")
    ja_to_zh = fetch_langlinks(JA_API, names_ja, "zh", "ja→zh")
    log(f"日文→中文命中: {len(ja_to_zh)}/{len(names_ja)}")

    # 仅对尚未被 ja→zh 覆盖到的中文名查反向链，减少请求
    covered_by_ja = set(ja_to_zh.values()) | (names_ja & names_zh)
    zh_need = names_zh - covered_by_ja - DISCARD_TITLES
    log(f"查询中文→日文 langlinks（待查 {len(zh_need)}）...")
    zh_to_ja = fetch_langlinks(ZH_API, zh_need, "ja", "zh→ja") if zh_need else {}
    log(f"中文→日文命中: {len(zh_to_ja)}/{len(zh_need)}")

    log("构建两列对照...")
    rows = build_pairs(names_zh, names_ja, ja_to_zh, zh_to_ja)
    rows = clean_rows(rows)

    both = sum(1 for j, z in rows if j and z)
    only_ja = sum(1 for j, z in rows if j and not z)
    only_zh = sum(1 for j, z in rows if z and not j)

    write_csv(OUT_CSV, rows)
    log(f"两列都有: {both} | 仅日文: {only_ja} | 仅中文: {only_zh}")
    log(f"总行数: {len(rows)}")
    log(f"已写入 {OUT_CSV}")

    db_path = Path(OUT_DB)
    conn = ensure_db(db_path)
    try:
        n = replace_actress_names_from_csv(conn, Path(OUT_CSV))
        log(f"已写入 SQLite {db_path} 表 actress_names: {n} 行")
    finally:
        conn.close()
