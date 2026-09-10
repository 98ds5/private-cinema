"""
文件名解析器: 从资源命名识别电影/动漫, 提取标题、年份、季集号并剥离噪声。
约定式判定优先级: S##E## 高于 EP## 高于年份 高于 Season/S## 子目录, 其余按电影。
标题清洗按类剥离噪声, 思路借鉴 Sonarr/Radarr 的 Parser。
"""
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedName:
    """一次文件名解析的结果"""
    title: str = ""
    original_title: str = ""
    year: Optional[int] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    is_anime: bool = False
    raw_filename: str = ""


# ---- 基础识别正则 (顺序即优先级) ----
RE_SEASON_EPISODE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")   # S01E03
RE_EPISODE_ONLY = re.compile(r"(?:EP|Ep|ep)(\d{1,3})")          # EP03
RE_SEASON_ONLY = re.compile(r"\b[Ss](\d{1,2})\b")               # S01 (无集号)

# 右边界允许到字符串结尾, 否则 "Interstellar.2014" 这种末尾年份匹配不到
RE_YEAR = re.compile(r"[\.\[\(\s](\d{4})(?:[\.\]\)\s]|$)")      # (2014) / .2014. / .2014

# ---- 噪声剥离正则 (分类, 借鉴 Sonarr Parser) ----
# 标签内部允许 . _ - 空格 混用: "DTS-HD.MA.5.1" / "DTS HD MA 5 1" 都要能吃掉
_S = r"[._\-\s]*"

# 方括号内容: 字幕组/压制组最常见的落点
RE_BRACKET = re.compile(r"\[[^\]]*\]|【[^】]*】")

# 音频: 长短语优先, 避免裸 HD / 5.1 先把短语打散
RE_AUDIO = re.compile(
    rf"\b(?:DTS{_S}HD{_S}MA{_S}5{_S}1|DTS{_S}HD{_S}MA|DTS{_S}HD|DTS{_S}MA"
    rf"|DTS{_S}ES|TrueHD{_S}Atmos|TrueHD|Atmos|MA{_S}5{_S}1"
    rf"|DDP?{_S}5{_S}1|DD\+|AC3|AAC|FLAC|MP3|DTS|5{_S}1|7{_S}1|2{_S}0)\b",
    re.IGNORECASE,
)

# 来源 (含流媒体平台标记: NF=Netflix, AMZN=Amazon, HULU, DSYP=Disney+,
#  ATVP=AppleTV+, PCOK=Peacock)
RE_SOURCE = re.compile(
    rf"\b(?:Blu{_S}?Ray|BDRip|BRRip|WEB{_S}?DL|WEBRip|WEB|HDTV|HDRip|DVDRip"
    rf"|DVD|Remux|CAMRip|CAM|PDTV|VHSRip|TS"
    rf"|NF|AMZN|HULU|DSYP|ATVP|PCOK)\b",
    re.IGNORECASE,
)

# 视频编码
RE_CODEC = re.compile(
    rf"\b(?:x264|x265|H{_S}?264|H{_S}?265|HEVC|AVC|xViD|DivX|AV1|VP9"
    rf"|MPEG{_S}?2)\b",
    re.IGNORECASE,
)

# 分辨率
RE_RESOLUTION = re.compile(
    r"\b(?:2160p|1080p|720p|576p|480p|4K|UHD)\b", re.IGNORECASE,
)

# HDR (DV = Dolby Vision, 原盘常见简写)
RE_HDR = re.compile(
    r"\b(?:HDR10\+|HDR10|HDR|DoVi|DV|HLG)\b", re.IGNORECASE,
)

# 杂项发行标记 + 字幕/语言标记
RE_MISC = re.compile(
    r"\b(?:Complete|PROPER|REPACK|EXTENDED|UNRATED|REMASTERED|IMAX|MULTi|DUAL"
    r"|Hybrid|10bit|8bit|Hi10P|HD)\b"
    r"|中英字幕|中文字幕|国语|粤语|中字",
    re.IGNORECASE,
)

# 多语言音轨码堆: 原盘常把语言码连写 (如 ...Eng.Fre.Ger...2160p)。
# 要求「连续 2 个以上」才剥离, 单个词不误伤 (Ind 匹配不到 Indiana, Nor 匹配不到 Norway)。
_LANG_CODES = (
    "Eng", "Fre", "Ger", "Ita", "Por", "Spa", "Cze", "Pol", "Rus", "Tur",
    "Chi", "Jpn", "Kor", "Hin", "Ara", "Heb", "Nld", "Swe", "Nor", "Dan",
    "Fin", "Hun", "Gre", "Tha", "Vie", "Ind", "Ukr", "Slo", "Rom", "Bul",
    "Hrv", "Srp", "Est", "Lav", "Lit", "Mandarin", "Cantonese",
)
RE_LANG_RUN = re.compile(
    r"(?:\b(?:" + "|".join(_LANG_CODES) + r")\b[._\-\s]*){2,}",
    re.IGNORECASE,
)

# 结尾压制组: 只剥「破折号 + token」(允许多段 -G1-G2), 不剥空格前缀的,
# 否则 "Back to the Future Part II" 会被误伤成 "Back to the Future Part"
RE_TRAILING_GROUP = re.compile(r"(?:[\-–][A-Za-z0-9_.]{2,})+\s*$")


def clean_title(raw: str) -> str:
    """
    剥离资源命名里的年份/季集/画质/来源/编码/压制组等噪声, 得到可读标题。
    中文标题 (如 "头号玩家") 原样保留。
    必须在分隔符仍是原始 ". _ -" 时调用, 归一化空白留到最后一步。
    """
    if not raw:
        return ""

    t = raw

    # 1. 年份 (调用方通常已单独提取, 这里再剥一次保证标题干净)
    t = RE_YEAR.sub(" ", t)

    # 2. 季集标记 (先 S01E03, 再 EP##, 再 S##)
    t = RE_SEASON_EPISODE.sub(" ", t)
    t = RE_EPISODE_ONLY.sub(" ", t)
    t = RE_SEASON_ONLY.sub(" ", t)

    # 3. 方括号内的组名
    t = RE_BRACKET.sub(" ", t)

    # 4. 技术标签 —— 顺序敏感: 音频整串短语必须早于裸 HD / 5.1
    for rx in (RE_AUDIO, RE_LANG_RUN, RE_SOURCE, RE_CODEC,
               RE_RESOLUTION, RE_HDR, RE_MISC):
        t = rx.sub(" ", t)

    # 5. 结尾压制组
    t = RE_TRAILING_GROUP.sub(" ", t)

    # 6. 归一化: 转空格、压缩空白、清理残留破折号、去首尾噪声
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"(?:\s*[\-–]\s*){1,}", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(" -–—·、,|")

    return t


def parse_filename(file_path: str, parent_dir: str = None,
                   root_dir: str = None) -> ParsedName:
    """
    解析一个视频文件路径, 返回 ParsedName。
    parent_dir / root_dir: 文件所在目录与媒体库根目录 (root_dir 建议传入)。

    标题来源的关键规则: 只有「一片一文件夹」时才用目录名作标题; 若 parent_dir
    就是 root_dir (扁平布局, 文件直接躺在库根目录), 必须改用文件名,
    否则整个库会被错误合并成一个以目录名命名的作品。
    """
    path = Path(file_path)
    filename = path.stem
    result = ParsedName(raw_filename=filename, original_title=filename)

    # 1. 含 S##E##: 判为动漫
    se_match = RE_SEASON_EPISODE.search(filename)
    if se_match:
        result.season_number = int(se_match.group(1))
        result.episode_number = int(se_match.group(2))
        result.is_anime = True

    # 2. 含 EP##: 判为动漫, 默认第 1 季
    if not se_match:
        ep_match = RE_EPISODE_ONLY.search(filename)
        if ep_match:
            result.episode_number = int(ep_match.group(1))
            result.season_number = 1
            result.is_anime = True

    # 扁平布局判定: 文件直接位于库根目录时不使用目录名作标题
    flat_layout = False
    if parent_dir and root_dir:
        try:
            flat_layout = (
                Path(parent_dir).resolve() == Path(root_dir).resolve()
            )
        except OSError:
            flat_layout = False

    use_dir = bool(parent_dir) and not flat_layout

    # 3. 年份: 嵌套布局优先目录名, 扁平布局用文件名;
    #    两者都取不到时回退到完整路径 (例如调用方未传 parent_dir,
    #    但路径里的父目录含 "(2010)")。
    search_text = parent_dir if use_dir else filename
    year_match = RE_YEAR.search(search_text) or RE_YEAR.search(str(path))
    if year_match:
        year_val = int(year_match.group(1))
        if 1900 <= year_val <= 2099:        # 过滤掉 ISBN 之类误匹配
            result.year = year_val

    # 4. 标题: 原始分隔符状态下清洗, 清洗失败则回退到未清洗的名字
    raw_source = Path(parent_dir).name if use_dir else filename
    title = clean_title(raw_source)
    result.title = title if title else raw_source

    return result


def is_anime_directory(dir_path: Path) -> bool:
    """目录含 Season N / S## 子目录时判为动漫目录"""
    try:
        for child in dir_path.iterdir():
            if child.is_dir() and re.match(
                r"(Season\s*\d+|S\d+)", child.name, re.IGNORECASE
            ):
                return True
    except OSError:
        pass
    return False
