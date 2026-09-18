"""
模糊搜索打分 —— 只用标准库 difflib + unicodedata; 拼音层可选 (装了 pypinyin 才启用)。

为什么需要它: 原实现是 SQL 的 `title ILIKE '%关键词%'`, 要求用户「一字不差、连续输入」。
归一化 + 分档打分之后, 下面这些都能命中:
    "cyber punk"   → Cyberpunk Edgerunners   (空格差异: 归一化后成了子串)
    "星穿"         → 星际穿越                 (跳字: 子序列)
    "星际穿跃"     → 星际穿越                 (错别字: 相似度容错)
    "海绵宝宝深海" → 海绵宝宝：深海大冒险      (标点差异)
    "xingji"/"xjcy"→ 星际穿越                 (拼音全拼 / 首字母缩写)

五档打分 (分数越大越像, 0 = 不匹配):
    100 完全相等
     90 前缀
     80 子串
   85~48 拼音   (ASCII 输入命中中文标题: 全拼/首字母, 详见 _pinyin_score)
     60~30 子序列  (允许跳字, 跳得越多扣得越多)
     60~36 相似度容错 (difflib 相似度 ≥ 0.6, 例: 打错一个字)

短查询 (≤2 个字符) 只允许前三档; 拼音层同样从严 —— 巧合的字母组合不算命中
("xy" 挡掉、"in" 挡掉), 否则单字/双字母就能命中半个库, 那是噪声不是功能。

代价: 打分在 Python 里做, 一次查询要遍历该分类下全部标题。个人库量级 (几十~几千部)
是毫秒级; 上万部应当换 SQLite FTS5, 或把归一化后的检索键与拼音键预存成列、用 `LIKE '键%'` 走索引先筛。
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# 拼音层是可选增强: 没装 pypinyin 时中文/英文匹配照常工作, 只是少了拼音档。
# 这里故意 catch 宽异常而不只是 ImportError: pypinyin 的词库是 .json 数据文件,
# 而且在 import 时就读盘 (pinyin_dict.py 末尾直接 _load_pinyin_dict()) ——
# 打包/裁剪环境里若只搬了 .py 没搬 json, 抛的是 FileNotFoundError, 只 catch
# ImportError 会让整个程序启动即崩。降级成"没有拼音档"才是正确行为。
try:
    from pypinyin import lazy_pinyin
except Exception:                                    # pragma: no cover
    lazy_pinyin = None

# 归一化时抹掉的噪声: 空白 + 中英文常见标点/连接符
_NOISE = re.compile(
    r"[\s\-_·、,，.。:：;；!！?？'\"“”‘’()（）\[\]【】{}<>《》/\\|+&~^*#@$%]+"
)

# 汉字区间: 拼音层只对「含汉字的标题 + 纯字母的查询」生效
_CJK = re.compile(r"[\u4e00-\u9fff]")

SCORE_EXACT = 100          # 完全相等
SCORE_PREFIX = 90          # 前缀
SCORE_SUBSTRING = 80       # 子串
SCORE_SUBSEQUENCE = 60     # 子序列基准分 (按跳字数下扣)
SCORE_RATIO_MAX = 60       # 相似度容错的最高分
MIN_SUBSEQ_LEN = 2         # 能做「跳字」的最短查询
SHORT_QUERY = 2            # 长度 ≤ 此值的查询: 只准紧邻跳字, 不准做相似度容错
MAX_GAPS_SHORT = 1         # 短查询最多允许跳 1 个字符
RATIO_MIN = 0.6            # 相似度容错下限, 低于它认为不相关

# ---- 拼音层 ----
SCORE_PINYIN_EXACT = 85        # 全拼或首字母完全相等 (xingjichuanyue / xjcy)
SCORE_PINYIN_PREFIX = 80       # 全拼或首字母前缀 (xingji / xjc)
SCORE_PINYIN_SUBSTRING = 75    # 全拼子串 (chuanyue)
SCORE_PINYIN_RATIO_MAX = 60    # 全拼打字手误的容错上限 (xingjichuanyu)
PINYIN_SUBSTRING_MIN_LEN = 3   # 全拼子串至少 3 个字母, 2 个字母太容易巧合 ("in")
PINYIN_MAX_GAPS = 1            # 首字母缩写最多漏 1 个字母 ("xc" → xjcy), 且必须从首字母开始
PINYIN_RATIO_MIN = 0.8         # 全拼容错门槛比中文严: 拼音串长, 0.6 会放进太多噪声
PINYIN_RATIO_MIN_LEN = 4       # 太短的 ASCII 查询不做全拼相似度


@lru_cache(maxsize=4096)
def normalize(text: str) -> str:
    """
    归一化: NFKC (全角转半角, 如 Ａ→A、：→:) → 转小写 → 抹掉空白与标点。
    查询词和标题都必须过这一层, 否则 "Cyber-Punk" 与 "cyberpunk" 根本没法比。
    lru_cache: 同一标题在每次敲键时都会被反复归一化, 缓存掉重复计算。
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    return _NOISE.sub("", t)


@lru_cache(maxsize=4096)
def pinyin_key(text: str):
    """
    返回 (全拼, 首字母), 例如 星际穿越 → ("xingjichuanyue", "xjcy")。
    没有汉字 (纯英文片名) 或没装 pypinyin 时返回 None —— 调用方据此跳过拼音档。
    先过 normalize(): 拼音只关心字, 标点空格在这里就已经没意义了。
    """
    if lazy_pinyin is None or not _CJK.search(text or ""):
        return None
    parts = lazy_pinyin(normalize(text))
    full = "".join(parts)
    initials = "".join(p[0] for p in parts if p and p[0].isalnum())
    return (full, initials) if full else None


def _subsequence_gaps(query: str, target: str, must_start: bool = False):
    """
    query 是否为 target 的子序列 (允许跳字); 是则返回被跳过的字符数, 否则 None。
    例: "星穿" 在 "星际穿越" 里 -> 跳过 1 个字符。
    must_start=True 时要求第一个字符就命中 target[0] (拼音首字母缩写用得上)。
    """
    i, first, last = 0, -1, -1
    for idx, ch in enumerate(target):
        if i < len(query) and ch == query[i]:
            if first < 0:
                first = idx
            last = idx
            i += 1
    if i < len(query):
        return None
    if must_start and first != 0:
        return None
    return (last - first + 1) - len(query)


def _pinyin_score(q: str, title: str) -> int:
    """
    ASCII 查询 → 中文标题拼音的打分, 不适用时返回 0。
    只对「纯字母数字查询 + 含汉字标题」生效: 中文查询和英文标题都走上面的字符档。
    (允许数字: 标题里的 "2"/"1" 也在拼音键里, 所以 "tdx2" / "pdd1" 这种缩写要能敲)
    """
    if not (q.isascii() and q.isalnum()):
        return 0
    keys = pinyin_key(title)
    if not keys:
        return 0
    full, initials = keys

    # 1) 全拼/首字母完全相等, 2) 前缀
    if q == full or q == initials:
        return SCORE_PINYIN_EXACT
    if full.startswith(q) or initials.startswith(q):
        return SCORE_PINYIN_PREFIX

    # 3) 全拼子串 ("chuanyue"): 至少 3 个字母, 2 个字母太容易巧合 ("in" 命中 xingji...)
    if len(q) >= PINYIN_SUBSTRING_MIN_LEN and q in full:
        return SCORE_PINYIN_SUBSTRING

    # 4) 首字母缩写允许漏 1 个字母, 但必须从第一个字母开始:
    #    "xc" → xjcy 放行; "xy" 跳过 2 个 → 挡掉; "jy" 不从开头 → 挡掉
    gaps = _subsequence_gaps(q, initials, must_start=True)
    if gaps is not None and gaps <= PINYIN_MAX_GAPS:
        return SCORE_PINYIN_SUBSTRING - gaps * 3

    # 5) 全拼打字手误 ("xingjichuanyu" 少个 e): 门槛比中文严, 且太短不算
    if len(q) >= PINYIN_RATIO_MIN_LEN:
        ratio = SequenceMatcher(None, q, full).ratio()
        if ratio >= PINYIN_RATIO_MIN:
            return int(SCORE_PINYIN_RATIO_MAX * ratio)
    return 0


def match_score(query: str, title: str) -> int:
    """
    给「查询词 → 标题」打分, 0 表示不匹配。
    传原始文本即可, 函数内部自己归一化。
    """
    q = normalize(query)
    t = normalize(title)
    if not q or not t:
        return 0

    # 第 1~3 档: 完全相等 / 前缀 / 子串
    if q == t:
        return SCORE_EXACT
    if t.startswith(q):
        return SCORE_PREFIX
    if q in t:
        return SCORE_SUBSTRING

    # 拼音档: 敲 "xingji" / "xjcy" 命中中文标题。放在字符档之后、跳字档之前 ——
    # 中文直接命中永远优先于拼音命中。
    pinyin_score = _pinyin_score(q, title)
    if pinyin_score:
        return pinyin_score

    # 第 4 档: 子序列 (跳字), 跳得越多分越低, 最低 30。
    # 短查询限制跳字额度: "星穿"→星际穿越 要放行, 但两个字符跳太远就是巧合了。
    if len(q) >= MIN_SUBSEQ_LEN:
        gaps = _subsequence_gaps(q, t)
        if gaps is not None and (len(q) > SHORT_QUERY or gaps <= MAX_GAPS_SHORT):
            return max(SCORE_SUBSEQUENCE - gaps * 3, SCORE_SUBSEQUENCE - 30)

    # 短查询到此为止: 一两个字符算相似度没有意义, 只会引入噪声
    if len(q) <= SHORT_QUERY:
        return 0

    # 第 5 档: 相似度容错 (错别字), 相似度不够高就不算命中
    ratio = SequenceMatcher(None, q, t).ratio()
    if ratio >= RATIO_MIN:
        return int(SCORE_RATIO_MAX * ratio)
    return 0


def is_match(query: str, title: str) -> bool:
    """只要是否命中, 不关心分档 (给调用方省一个 > 0 的判断)"""
    return match_score(query, title) > 0
