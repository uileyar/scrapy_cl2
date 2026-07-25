"""
女优名提取（独立模块，从 detail_parse.py 抽离）。

策略优先级（从高到低，命中即返回）：
    1. 显式标签      —— 正文里的「出演者：」「出演：」「【出演女優】」等结构化字段
    2. 角色格人名    —— 标题里「【妻子・環奈】」这类 ・ 分隔的角色格
    3. 尾部方括号    —— 标题尾部「【木内亚美莉】」这类纯方括号艺名
       （原 detail_parse.py 里 head = split('[', '【') 会把这类整段丢弃，属于 bug，这里修复）
    4. 词典精确匹配  —— 用 get_minnano_actress.py 爬到的《みんなのAV》女优名单做已知名字查找
    5. 启发式猜测    —— 兜底：从标题尾段猜最后一个 token 是不是人名（旧逻辑，误判率最高）

词典来源：get_minnano_actress.py 产出的 CSV（列：source_id, ja_name, kana, romaji, ...），
或写入同名列的 SQLite 表（默认表名 actress_names_minnano）。两种来源都可以用，
不强制要求先转 SQLite。

对外主要用两个函数：
    configure_actress_dict(csv_path=..., db_path=..., table=...)  # 启动时调用一次，加载词典
    extract_actress(plain, h4_text=None, topic_title=None) -> Optional[str]

未调用 configure_actress_dict 时，词典匹配这一步会自动跳过，行为退化为
「结构化标记 + 启发式猜测」，不会报错，方便还没建库时直接把这个文件接进去。
"""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 与 detail_parse.py 共用的常量
# ---------------------------------------------------------------------------

# 番号正则：与 detail_parse.CODE_RE 保持一致。之所以在这里单独定义一份而不是从
# detail_parse 里 import，是为了避免 detail_parse.py <-> actress_extract.py 相互
# import 造成循环依赖（detail_parse 需要 import 本文件的 extract_actress）。
CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])\d*([A-Z]{2,}-?\d{2,})(?:[A-Za-z](?![A-Za-z0-9]))?(?![A-Za-z0-9])"
)

FILM_NAME_FIELD_RE = re.compile(r"【影片名[稱称]】[︰：:]([^\n]+)")
CHINESE_TITLE_FIELD_RE = re.compile(r"【中文片名】[︰：:]([^\n]+)")

_ACTRESS_TITLE_JUNK = frozenset(
    {
        "紀錄片", "纪录片", "中文字幕", "高清", "有碼", "無碼", "无码",
        "字幕", "女优", "女優", "合集", "精選", "精选", "系列", "限定",
        "初回", "版",
        "制服戀物癖", "制服恋物癖", "戀物癖", "恋物癖",
        "影片格式", "格式類型", "格式类型", "影片大小", "影片時間", "影片时间",
        "影片名稱", "影片名称", "中文片名",
    }
)
# 长叙事 h4/片名尾段易误判的短语（非人名）
_ACTRESS_CN_PHRASE_JUNK = frozenset(
    {
        "青春性交", "首次拍摄", "我在有乐町搭讪", "身材却完美无瑕",
        "真的是软派", "首部作品", "女优合集", "女優合集", "她年纪轻轻",
    }
)
# 启发式尾段若以这些字结尾，几乎一定是题材词而非人名
_ACTRESS_BAD_ENDINGS = ("癖", "篇")
# 多人女优最终拼接符
_ACTRESS_JOIN = "|"
# 人名左右边界：命中左右若仍是中日文姓名字符，则视为更长名字的子串，丢弃
_CJK_NAME_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff・ー]")
# 左侧这些字虽是汉字，但是语法助词/连接，不算「名字的一部分」
_NAME_LEFT_OK = frozenset(
    "的のとがはをにでもへやと與与和跟被讓让把從从給给對对在於于為为是把與"
)
# 名字右侧虽是汉字，但属于「作品/合集/醬」等后缀时仍算边界合法
_OK_AFTER_NAME_RE = re.compile(
    r"^(?:作品|合集|影片|女優|女优|AV|出道|デビュー|DEBUT|"
    r"醬|酱|ちゃん|さん|様|氏|"
    r"[（(【\[])"
)
# 标题繁体/异体 → 词典常见写法（日文汉字或简体）
# 注意：不要把日文「瀬/綾」转成简体，否则入库名会被改坏。
_TRAD_TO_SIMP = str.maketrans(
    {
        "戀": "恋",
        "瀨": "瀬",  # 中文繁体 → 日文
        "澤": "沢",
        "瀧": "滝",
        "濱": "浜",
        "櫻": "桜",
        "實": "実",
        "戶": "戸",
        "條": "条",
        "餘": "余",
        "繪": "絵",
        "繩": "縄",
        "藝": "芸",
        "麗": "丽",
        "優": "优",
    }
)
# 显式标签 / 角色格多人分隔
_ACTRESS_SPLIT_RE = re.compile(r"[、,，|/／]+")
# 名字两侧可剥掉的符号（如「-美ノ嶋めぐり」）
_ACTRESS_EDGE_STRIP = " 　「」『』【】（）()[]-－—–ー_~～·•*"
# 片名里常见全大写英文词，非人名（避免「NO.1 STYLE」误认艺名）
_ACTRESS_LATIN_JUNK = frozenset(
    {
        "STYLE", "BODY", "LOVE", "LOVER", "HEART", "STAR", "STARS", "SWEET",
        "CUTE", "COOL", "PINK", "BLUE", "MODE", "BEAUTY", "BEST", "DREAM",
        "NIGHT", "ANGEL", "DEVIL", "HONEY", "ROSE", "APPLE", "QUEEN", "GIRL",
        "LADY", "PRINCESS", "NEW", "TRUE", "PURE", "DEEP", "HIGH", "MASTER",
        "LIMITED", "SPECIAL", "DEBUT", "HARD", "SOFT", "UNCENSORED", "CENSORED",
    }
)
# 标题尾段猜女优：纯 CJK 候选最长（过长多为叙事句；结构化标记不受限）
_ACTRESS_GUESS_CJK_MAX_LEN = 7
# 句末语气/感叹碎片，勿当人名（如「太瘋狂了」）
_ACTRESS_FRAG_ENDINGS = ("了", "嗎", "吧", "呢", "啊", "呀", "喔", "嘛", "哎", "嘿", "哈", "呐", "咯")

# 片名/标题尾部候选：中日文姓名常见字符（含假名・）
_ACTRESS_TOKEN_RE = re.compile(r"^[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff・]{2,16}$")
_ACTRESS_TOKEN_ONE_RE = re.compile(r"^[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff・]$")
_ACTRESS_SINGLE_HANZI_RE = re.compile(r"^[\u4e00-\u9fff]$")
_ACTRESS_TOPIC_NAME_RE = re.compile(
    r"(?:[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff・]{0,12}影片)?"
    r"([\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff・]{2,12})(?:作品|合集|影片)"
)
# 片名中「【妻子・環奈】」类角色格内的人名（・后一节）
_ROLE_DOT_NAME_RE = re.compile(r"【[^】]{0,32}・([^】]{1,12})】")
# 标题尾部纯方括号艺名，如「...標題...【木内亚美莉】」（不含 ・）
_TRAILING_BRACKET_RE = re.compile(r"【([^】]{1,14})】")

# 词典里做匹配的最短长度：含汉字的名字 >=2；纯假名的名字 >=3
# （纯 2 字假名常和日常词汇撞车，比如「まな」也是常见词根，误判率偏高）
_DICT_MIN_LEN_KANJI = 2
_DICT_MIN_LEN_KANA = 3
_HAS_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")
_PURE_KANA_RE = re.compile(r"^[\u3040-\u309f\u30a0-\u30ffー・]+$")


def _strip_leading_bracket_tags(text: str) -> str:
    """去掉标题前连续 [有碼] [HD/xG] 等方括号标签。"""
    t = text.strip()
    return re.sub(r"(?:\[[^\]]*\]\s*)+", "", t).strip()


def _is_single_hanzi_tail_name(tok: str) -> bool:
    return bool(_ACTRESS_SINGLE_HANZI_RE.fullmatch(tok))


def _strip_jav_debut_name_suffix(tok: str) -> str:
    """「乃坂日和AV出道」类：去掉尾部 AV/AV出道，得到纯名。"""
    m = re.match(
        r"^([\u4e00-\u9fff\u3040-\u30ff・]{2,12})AV(?:出道|デビュー|DEBUT)?$",
        tok,
        re.I,
    )
    if m:
        return m.group(1)
    return tok


def _normalize_for_dict(text: str) -> str:
    """繁体归一，便于「渚戀生」命中词典「渚恋生」。"""
    return (text or "").translate(_TRAD_TO_SIMP)


def _is_cjk_name_char(ch: str) -> bool:
    return bool(_CJK_NAME_CHAR_RE.fullmatch(ch))


def _cjk_name_boundary_ok(text: str, start: int, end: int) -> bool:
    """词典命中须是「独立名字」：左右不能仍是姓名字符的延续。

    避免「美優」嵌在「小日向美優」、`一花` 嵌在「黑川一花」这类假阳性。
    左侧助词（的/の）与右侧「作品/醬」等后缀视为合法边界。
    """
    if start > 0:
        left = text[start - 1]
        if _is_cjk_name_char(left) and left not in _NAME_LEFT_OK:
            return False
    if end >= len(text):
        return True
    if not _is_cjk_name_char(text[end]):
        return True
    return bool(_OK_AFTER_NAME_RE.match(text[end:]))


def _clean_actress_token(tok: str) -> str:
    """剥掉名字两侧空白与常见符号（含前导 ``-``）。"""
    return (tok or "").strip(_ACTRESS_EDGE_STRIP)


def _is_plausible_actress_name(name: str) -> bool:
    n = _clean_actress_token(name)
    if not n or len(n) < 2 or len(n) > 8:
        return False
    if n in _ACTRESS_TITLE_JUNK or n in _ACTRESS_CN_PHRASE_JUNK:
        return False
    if n.endswith(_ACTRESS_BAD_ENDINGS) or n.endswith(_ACTRESS_FRAG_ENDINGS):
        return False
    return bool(_ACTRESS_TOKEN_RE.fullmatch(n))


def _actress_from_cn_title_patterns(text: str) -> Optional[str]:
    """中文叙事片名里抠人名：…名 / 的名在 / 名醬 / ～名 / 名色情…"""
    head = re.split(r"[\[【]", text, maxsplit=1)[0]
    head = re.sub(r"(?i)\b(?:HARD|SOFT|UNCENSORED|CENSORED)\b", " ", head)
    head = re.sub(r"（[^）]*）|\([^)]*\)", " ", head)
    head = re.sub(r"\s+", " ", head).strip()
    if not head:
        return None

    patterns = (
        r"[…·・\.]{1,3}\s*([\u4e00-\u9fff]{2,5})\s*$",
        r"[～~〜]\s*([\u4e00-\u9fff]{2,5})\s*$",
        r"的([\u4e00-\u9fff]{2,6})(?:在|於|于|和|與|与|為|为|是|把|跟)",
        r"([\u4e00-\u9fff]{2,6})[醬酱]",
        r"(?:^|[\s　])([\u4e00-\u9fff]{2,5})(?:色情|制服|轉變|转变|AV|出道|引退|記念|紀念)",
        r"(?:紀錄片|纪录片)([\u4e00-\u9fff]{2,5})\s*$",
    )
    for rx in patterns:
        m = re.search(rx, head)
        if m and _is_plausible_actress_name(m.group(1)):
            return _clean_actress_token(m.group(1))
    return None


def _actress_from_dict_title_suffix(text: str) -> Optional[str]:
    """片名末尾粘连人名：如「…紀錄片石川美鈴」。

    对结尾 CJK 串从长到短试词典精确命中（整段候选，不受左邻汉字边界限制）。
    """
    if _DICT is None:
        return None
    head = re.split(r"[\[【]", text, maxsplit=1)[0]
    head = re.sub(r"(?i)\b(?:HARD|SOFT|UNCENSORED|CENSORED)\b", " ", head)
    head = re.sub(r"（[^）]*）|\([^)]*\)", " ", head).strip()
    m = re.search(r"([\u4e00-\u9fff\u3040-\u30ff・]{2,8})\s*$", head)
    if not m:
        return None
    tail = m.group(1)
    for length in range(len(tail), 1, -1):
        cand = tail[-length:]
        if not _ACTRESS_TOKEN_RE.fullmatch(cand):
            continue
        key = _normalize_for_dict(cand)
        if key not in _DICT.names:
            continue
        hits = _DICT.find_all(cand)
        if hits:
            return hits[0]
    return None


def _join_actress_names(names: Iterable[str]) -> Optional[str]:
    """多人用 ``|`` 拼接；去重、清洗、过滤 junk。"""
    out: List[str] = []
    seen: set = set()
    for raw in names:
        n = _clean_actress_token(raw)
        if not n or n in seen:
            continue
        if n in _ACTRESS_TITLE_JUNK or n in _ACTRESS_CN_PHRASE_JUNK:
            continue
        if n.endswith(_ACTRESS_BAD_ENDINGS):
            continue
        seen.add(n)
        out.append(n)
    return _ACTRESS_JOIN.join(out) if out else None


def _finalize_actress(s: Optional[str]) -> Optional[str]:
    """统一出口：拆分旧分隔符、清洗符号、再以 ``|`` 拼接。"""
    if not s:
        return None
    return _join_actress_names(_ACTRESS_SPLIT_RE.split(s))


# ---------------------------------------------------------------------------
# 词典：多模式最长匹配（前缀树），不依赖第三方库
# ---------------------------------------------------------------------------


class _NameTrie:
    """简单的字符前缀树，支持「从某个起点开始的最长命中」。

    比起把几千个名字拼成一个巨大的 `|` 交替正则，前缀树在名字数量很大时
    构建更快、匹配也是线性的（每个起点最多走 max(name_len) 步），
    而且不用担心正则引擎对超大交替表达式的性能坑。
    """

    def __init__(self) -> None:
        self._root: dict = {}

    def insert(self, name: str, display: Optional[str] = None) -> None:
        """按 ``name``（通常已繁简归一）建路径，``display`` 为对外返回的原名。"""
        node = self._root
        for ch in name:
            node = node.setdefault(ch, {})
        node["$"] = display or name

    def find_all(self, text: str) -> List[Tuple[int, int, str]]:
        """返回文本中所有不重叠的最长命中：[(start, end, name), ...]。

        命中须通过 CJK 姓名边界校验；未通过则不消费该段，从下一字符继续，
        避免把「美優」嵌在「小日向美優」里误匹配后又跳过真名起点。
        """
        results: List[Tuple[int, int, str]] = []
        i, n = 0, len(text)
        while i < n:
            node = self._root
            j = i
            last_match: Optional[str] = None
            last_end = i
            while j < n and text[j] in node:
                node = node[text[j]]
                j += 1
                if "$" in node:
                    last_match = node["$"]
                    last_end = j
            if last_match and _cjk_name_boundary_ok(text, i, last_end):
                results.append((i, last_end, last_match))
                i = last_end
            else:
                i += 1
        return results

    def find_first(self, text: str) -> Optional[str]:
        hits = self.find_all(text)
        return hits[0][2] if hits else None


@dataclass
class ActressDict:
    """已知女优名词典：主要用 ja_name 做匹配，kana 作为弱信号补充。"""

    names: set = field(default_factory=set)
    _trie: _NameTrie = field(default_factory=_NameTrie, repr=False)

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "ActressDict":
        inst = cls()
        for raw in names:
            display = (raw or "").strip()
            if not display:
                continue
            n = _normalize_for_dict(display)
            if _PURE_KANA_RE.match(n):
                if len(n) < _DICT_MIN_LEN_KANA:
                    continue
            elif _HAS_KANJI_RE.search(n):
                if len(n) < _DICT_MIN_LEN_KANJI:
                    continue
            else:
                # 纯罗马字/其他字符不用于中日文标题的子串匹配
                continue
            if n in _ACTRESS_TITLE_JUNK or n in _ACTRESS_CN_PHRASE_JUNK:
                continue
            if n not in inst.names:
                inst.names.add(n)
                inst._trie.insert(n, display=display)
        return inst

    @classmethod
    def from_csv(cls, csv_path: str | Path) -> "ActressDict":
        path = Path(csv_path)
        names: List[str] = []
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for col in ("ja_name", "kana"):
                    val = (row.get(col) or "").strip()
                    if val:
                        names.append(val)
        return cls.from_names(names)

    @classmethod
    def from_sqlite(
        cls,
        db_path: str | Path,
        table: str = "actress_names_minnano",
    ) -> "ActressDict":
        conn = sqlite3.connect(str(db_path))
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            wanted = [c for c in ("ja_name", "kana") if c in cols]
            if not wanted:
                raise ValueError(
                    f"表 {table} 里没有 ja_name/kana 列，无法建立词典"
                    "（检查 crawl_to_sqlite.py 写入的列名是否一致）"
                )
            names: List[str] = []
            for row in conn.execute(f"SELECT {', '.join(wanted)} FROM {table}"):
                names.extend(v for v in row if v)
            # 并入维基中文别名（如 河合あすな → 河合明日菜）
            try:
                wiki_cols = {r[1] for r in conn.execute("PRAGMA table_info(actress_names)")}
                if "ja_name" in wiki_cols:
                    for row in conn.execute(
                        "SELECT ja_name, zh_name FROM actress_names"
                        if "zh_name" in wiki_cols
                        else "SELECT ja_name, '' FROM actress_names"
                    ):
                        for v in row:
                            if v and str(v).strip():
                                names.append(str(v).strip())
            except sqlite3.Error:
                pass
            return cls.from_names(names)
        finally:
            conn.close()

    def find_first(self, text: str) -> Optional[str]:
        if not text:
            return None
        hits = self.find_all(text)
        return hits[0] if hits else None

    def find_all(self, text: str) -> List[str]:
        """返回文本里所有不重叠命中的已知名字（按出现顺序，已去重保序）。

        在繁简归一后的文本上匹配，返回词典里的原始写法。
        """
        if not text:
            return []
        seen: set = set()
        out: List[str] = []
        for _, _, name in self._trie.find_all(_normalize_for_dict(text)):
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def __len__(self) -> int:
        return len(self.names)


# 模块级单例：未调用 configure_actress_dict 时保持 None，词典匹配这一步自动跳过
_DICT: Optional[ActressDict] = None


def configure_actress_dict(
    csv_path: Optional[str | Path] = None,
    db_path: Optional[str | Path] = None,
    table: str = "actress_names_minnano",
) -> int:
    """加载女优名词典，返回加载到的名字数量。csv_path 优先于 db_path。

    建议在进程启动时调用一次；get_minnano_actress.py 跑完后不管你是只留了 CSV
    还是同时 --db 写了 SQLite，这里都能直接接。
    """
    global _DICT
    if csv_path:
        _DICT = ActressDict.from_csv(csv_path)
    elif db_path:
        _DICT = ActressDict.from_sqlite(db_path, table=table)
    else:
        _DICT = None
    return len(_DICT) if _DICT else 0


def get_actress_dict() -> Optional[ActressDict]:
    return _DICT


# ---------------------------------------------------------------------------
# 结构化标记提取
# ---------------------------------------------------------------------------


def _actress_names_from_role_brackets(text: str) -> Optional[str]:
    """从「【头衔・人名】」格式中收集・后人名（多女优以 ``|`` 连接）。"""
    names: List[str] = []
    seen: set = set()
    for m in _ROLE_DOT_NAME_RE.finditer(text):
        n = _clean_actress_token(m.group(1))
        if not n:
            continue
        ok = bool(_ACTRESS_TOKEN_RE.match(n)) or bool(_ACTRESS_TOKEN_ONE_RE.match(n))
        if not ok or len(n) > 12:
            continue
        if n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        return None
    return _join_actress_names(names)


def _actress_from_trailing_bracket(text: str) -> Optional[str]:
    """标题尾部纯方括号艺名，如「...【木内亚美莉】」。

    修复点：旧版 `_actress_guess_from_title_tail` 里
    `head = re.split(r"[\\[【]", text, maxsplit=1)[0]` 会把第一个方括号之后的
    内容整段丢弃——只要标题不是「・」角色格，这类最常见的纯方括号艺名格式就会
    被直接切掉，等于系统性漏检。这里单独检查所有方括号里的候选，取最后一个
    校验通过的当女优名。
    """
    matches = _TRAILING_BRACKET_RE.findall(text)
    if not matches:
        return None
    for cand in reversed(matches):
        cand = _clean_actress_token(cand)
        if "・" in cand:
            cand = _clean_actress_token(cand.split("・")[-1])
        if not cand or len(cand) > 12:
            continue
        if cand in _ACTRESS_TITLE_JUNK or cand in _ACTRESS_CN_PHRASE_JUNK:
            continue
        if cand.endswith(_ACTRESS_BAD_ENDINGS):
            continue
        if _ACTRESS_TOKEN_RE.fullmatch(cand):
            return cand
    return None


def _actress_from_explicit_labels(plain: str) -> Optional[str]:
    """正文里的「出演者：」「出演：」「【出演女優】」等结构化字段，最高优先级。"""
    m = re.search(
        r"出演者[：:]\s*(.+?)(?=監督|监督|制作|品番|配信|系列|収録|发行|商品|[\r\n]|\Z)",
        plain,
    )
    if m:
        a = _finalize_actress(m.group(1).strip())
        if a and a != "----":
            return a
    m = re.search(
        r"(?<!者)出演[：:]\s*([^\n\r<制作品番配信系列収録发行：:]+?)(?=制作|品番|配信|系列|収録|发行|[\s\r\n]|$)",
        plain,
    )
    if m:
        a = _finalize_actress(m.group(1).strip())
        if a and a != "----":
            return a
    m = re.search(r"【(?:演出|出演)女[優优]】[︰：:]([^【\n]+)", plain)
    if m:
        a = _finalize_actress(m.group(1).strip())
        if a and a != "----":
            return a
    return None


# ---------------------------------------------------------------------------
# 词典匹配 + 启发式猜测（同一段候选文本内，词典优先于启发式）
# ---------------------------------------------------------------------------


def _actress_guess_from_title_tail(text: str) -> Optional[str]:
    """从番号后的标题尾段猜女优名。

    顺序：・角色格 > 尾部方括号艺名（bug 修复） > 词典精确匹配 > 启发式猜最后一个 token。
    词典命中可信度远高于位置猜测，所以放在启发式之前；但两个「结构化方括号」
    信号本身就是标题作者自己标出来的人名，可信度不低于词典查找，所以仍然排在
    词典之前。
    """
    if not text or not text.strip():
        return None
    role = _actress_names_from_role_brackets(text)
    if role:
        return role
    tb = _actress_from_trailing_bracket(text)
    if tb:
        return tb
    if _DICT is not None:
        hits = _DICT.find_all(text)
        if hits:
            return _join_actress_names(hits)
        suf = _actress_from_dict_title_suffix(text)
        if suf:
            return suf
    cn = _actress_from_cn_title_patterns(text)
    if cn:
        return cn
    head = re.split(r"[\[【]", text, maxsplit=1)[0].strip()
    if not head:
        return None
    head = re.sub(r"(?i)\b(?:HARD|SOFT|UNCENSORED|CENSORED)\b", " ", head).strip()
    parts = [p for p in re.split(r"[\s　，。、！!？?…]+", head) if p]
    for idx, tok in enumerate(reversed(parts)):
        is_tail_token = idx == 0
        t_raw = _clean_actress_token(tok)
        t_raw = re.sub(r"《[^》]*》\s*$", "", t_raw).strip()
        t_raw = _clean_actress_token(t_raw)
        if len(t_raw) == 1:
            if _is_single_hanzi_tail_name(t_raw):
                return t_raw
            return None
        if len(t_raw) < 2:
            continue
        if (
            2 <= len(t_raw) <= 15
            and re.fullmatch(r"[A-Z]+", t_raw)
            and t_raw not in _ACTRESS_LATIN_JUNK
        ):
            return t_raw
        t = _strip_jav_debut_name_suffix(t_raw)
        t = _clean_actress_token(t)
        if t.endswith("的"):
            if is_tail_token:
                return None
            continue
        if "的" in t and len(t) > 5:
            if is_tail_token:
                return None
            continue
        if len(t) > _ACTRESS_GUESS_CJK_MAX_LEN:
            if is_tail_token:
                return None
            continue
        if len(t) <= 8 and t.endswith(_ACTRESS_FRAG_ENDINGS):
            if is_tail_token:
                return None
            continue
        if t.endswith(_ACTRESS_BAD_ENDINGS):
            if is_tail_token:
                return None
            continue
        if t in _ACTRESS_TITLE_JUNK:
            if is_tail_token:
                return None
            continue
        if t in _ACTRESS_CN_PHRASE_JUNK:
            if is_tail_token:
                return None
            continue
        if "搭讪" in t and len(t) >= 4:
            if is_tail_token:
                return None
            continue
        if "软派" in t:
            if is_tail_token:
                return None
            continue
        if not _ACTRESS_TOKEN_RE.match(t):
            continue
        if re.search(r"[a-zA-Z]{3,}", t):
            continue
        return t
    return None


def _actress_from_chinese_title_plain(plain: str) -> Optional[str]:
    """【中文片名】行常为「长标题 + 空格 + 中文名」，优先于日文【影片名稱】启发式。"""
    m = CHINESE_TITLE_FIELD_RE.search(plain)
    if not m:
        return None
    raw = m.group(1).strip()
    return _actress_guess_from_title_tail(raw)


def _actress_from_topic_title(topic_title: Optional[str]) -> Optional[str]:
    """从帖子标题（如「瀧本雫葉作品」）提取单个女优名；词典命中优先于「XX作品/合集」句式猜测。"""
    if not topic_title:
        return None
    if _DICT is not None:
        hits = _DICT.find_all(topic_title)
        if hits:
            return _join_actress_names(hits)
    for m in _ACTRESS_TOPIC_NAME_RE.finditer(topic_title):
        phrase = m.group(0).strip(" 　「」『』【】（）()")
        name = _clean_actress_token(m.group(1))
        if phrase in _ACTRESS_TITLE_JUNK or phrase in _ACTRESS_CN_PHRASE_JUNK:
            continue
        if not name:
            continue
        if name in _ACTRESS_TITLE_JUNK or name in _ACTRESS_CN_PHRASE_JUNK:
            continue
        if name.endswith(_ACTRESS_BAD_ENDINGS):
            continue
        if _ACTRESS_TOKEN_RE.fullmatch(name):
            return name
    return None


def _actress_from_film_name_plain(plain: str) -> Optional[str]:
    """【影片名稱/名称】行内、番号后的标题尾段启发式。"""
    m = FILM_NAME_FIELD_RE.search(plain)
    if not m:
        return None
    raw = m.group(1).strip()
    raw = re.sub(r"^\[[^\]]+\]\s*", "", raw)
    cm = CODE_RE.search(raw)
    tail = raw[cm.end():].strip() if cm else raw
    tail = _strip_leading_bracket_tags(tail)
    return _actress_guess_from_title_tail(tail)


def _actress_from_h4_tail(h4_text: str) -> Optional[str]:
    """h4 中番号之后、去掉前导方括号块后的标题尾段启发式。"""
    cm = CODE_RE.search(h4_text)
    if not cm:
        return None
    tail = h4_text[cm.end():].strip()
    tail = _strip_leading_bracket_tags(tail)
    return _actress_guess_from_title_tail(tail)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def extract_actress(
    plain: str,
    h4_text: Optional[str] = None,
    topic_title: Optional[str] = None,
) -> Optional[str]:
    """从详情页正文 + h4/主题标题里提取女优名，drop-in 替换旧的
    ``detail_parse._actress_from_conttpc_plain(plain, topic_title or h4_text)``。

    调用方式不变：第二个参数传 ``topic_title or h4_text``（沿用旧调用惯例，
    历史上这个参数实际当作"主题标题"用，即使变量名叫 h4_text）。
    """
    explicit = _actress_from_explicit_labels(plain)
    if explicit:
        return _finalize_actress(explicit)

    # 正文标明出演者：----（总集等）时勿再用片名/h4 猜女优；【中文片名】仍可独占真名
    skip_film_h4_actress_guess = bool(re.search(r"出演者[：:]\s*----", plain))

    cz = _actress_from_chinese_title_plain(plain)
    if cz:
        return _finalize_actress(cz)
    topic = _actress_from_topic_title(h4_text)
    if topic:
        return _finalize_actress(topic)
    if skip_film_h4_actress_guess:
        return None
    g = _actress_from_film_name_plain(plain)
    if g:
        return _finalize_actress(g)
    if h4_text:
        h = _actress_from_h4_tail(h4_text)
        if h:
            return _finalize_actress(h)
    return None


def extract_actress_detailed(
    plain: str,
    h4_text: Optional[str] = None,
    topic_title: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """同 ``extract_actress``，但额外返回命中来源，便于跑历史数据统计各策略命中率。

    source 取值：explicit_label / role_bracket / trailing_bracket /
    dict_match / topic_dict_match / topic_phrase_guess / heuristic_guess / None。
    """
    explicit = _actress_from_explicit_labels(plain)
    if explicit:
        return {"actress": _finalize_actress(explicit), "source": "explicit_label"}

    skip_film_h4_actress_guess = bool(re.search(r"出演者[：:]\s*----", plain))

    def _guess_with_source(text: str) -> Tuple[Optional[str], Optional[str]]:
        if not text or not text.strip():
            return None, None
        role = _actress_names_from_role_brackets(text)
        if role:
            return role, "role_bracket"
        tb = _actress_from_trailing_bracket(text)
        if tb:
            return tb, "trailing_bracket"
        if _DICT is not None:
            hits = _DICT.find_all(text)
            if hits:
                return _join_actress_names(hits), "dict_match"
            suf = _actress_from_dict_title_suffix(text)
            if suf:
                return suf, "dict_suffix"
        cn = _actress_from_cn_title_patterns(text)
        if cn:
            return cn, "cn_title_pattern"
        return _actress_guess_from_title_tail(text), "heuristic_guess"

    cz_m = CHINESE_TITLE_FIELD_RE.search(plain)
    if cz_m:
        name, src = _guess_with_source(cz_m.group(1).strip())
        if name:
            return {"actress": _finalize_actress(name), "source": src}

    if h4_text:
        if _DICT is not None:
            hits = _DICT.find_all(h4_text)
            if hits:
                return {
                    "actress": _finalize_actress(_join_actress_names(hits)),
                    "source": "topic_dict_match",
                }
        topic = _actress_from_topic_title(h4_text)
        if topic:
            return {"actress": _finalize_actress(topic), "source": "topic_phrase_guess"}

    if skip_film_h4_actress_guess:
        return {"actress": None, "source": None}

    fm = FILM_NAME_FIELD_RE.search(plain)
    if fm:
        raw = re.sub(r"^\[[^\]]+\]\s*", "", fm.group(1).strip())
        cm = CODE_RE.search(raw)
        tail = raw[cm.end():].strip() if cm else raw
        tail = _strip_leading_bracket_tags(tail)
        name, src = _guess_with_source(tail)
        if name:
            return {"actress": _finalize_actress(name), "source": src}

    if h4_text:
        cm = CODE_RE.search(h4_text)
        if cm:
            tail = _strip_leading_bracket_tags(h4_text[cm.end():].strip())
            name, src = _guess_with_source(tail)
            if name:
                return {"actress": _finalize_actress(name), "source": src}

    return {"actress": None, "source": None}
