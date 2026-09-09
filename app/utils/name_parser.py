"""
文件名解析器 — 核心难点 (答辩高频: "你怎么识别电影和动漫")

V1.0 约定式解析策略 (按优先级):
  1. 文件名含 S##E##  (如 S01E03)  → 动漫, 提取季号+集号
  2. 文件名含 EP##    (如 EP03)    → 动漫, 默认第 1 季
  3. 名称/目录含 (YYYY) / .YYYY.   → 提取年份
  4. 目录下含 "Season N" / "S##" 子目录 → 判定为动漫目录
  5. 其余按电影处理

标题清洗 (clean_title) 借鉴 Sonarr/Radarr 的 Parser 分类思路, 按类剥离噪声:
  年份 → 季集标记 → 方括号组 → 音频 → 来源 → 编码 → 分辨率 → HDR → 杂项 → 结尾压制组

两个关键工程决策 (答辩可讲):
  A. 在分隔符仍为原始 ". _ -" 时就清洗, 而不是先替换成空格再清洗。
     否则 "DTS-HD.MA.5.1" 会变成 "DTS HD MA 5 1", 短语结构被打散, 命中率骤降。
  B. 结尾压制组只剥离「破折号前缀」的 token (如 -FraMeSToR),
     不剥「空格前缀」的, 否则 "Back to the Future Part II" 会被误伤成
     "Back to the Future Part"。
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

# 年份右边界允许是字符串结尾: "Interstellar.2014.mkv" 主干为
# "Interstellar.2014", 年份在末尾, 若强制要求后随分隔符则永远匹配不到。
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

# 多语言音轨列表: 原盘/混流资源常把全部音轨语言码堆在名字里, 例如
#   Pacific.Rim.2013.Eng.Fre.Ger.Ita.Por.Spa.Cze.Pol.Rus.Tur.Chi.Jpn.2160p...
# 要求「连续 2 个以上」才剥离 —— 这样单个词不会被误伤:
#   \bInd\b 匹配不到 "Indiana", \bNor\b 匹配不到 "Norway"。
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

# 结尾压制组: 仅剥「破折号 + token」, 允许连续多段 (-Group1-Group2)
RE_TRAILING_GROUP = re.compile(r"(?:[\-–][A-Za-z0-9_.]{2,})+\s*$")


def clean_title(raw: str) -> str:
    """
    把资源命名里的年份/季集号/画质/来源/编码/音频/压制组等噪声剥离,
    得到可读标题。中文标题 (如 "头号玩家") 原样保留。

    注意: 必须在分隔符仍为原始 ". _ -" 时调用, 归一化空白是最后一步。
    """
    if not raw:
        return ""

    t = raw

    # 1. 年份 (调用方通常已单独提取, 这里再剥一次保证标题干净)
    t = RE_YEAR.sub(" ", t)

    # 2. 季集标记 (S01E03 → EP07 → S01)
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

    # 6. 归一化: 分隔符转空格 → 压缩空白 → 清理残留破折号 → 去首尾噪声
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"(?:\s*[\-–]\s*){1,}", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(" -–—·、,|")

    return t


def parse_filename(file_path: str, parent_dir: str = None,
                   root_dir: str = None) -> ParsedName:
    """
    解析一个视频文件路径 → ParsedName。

    parent_dir: 文件所在目录路径。
    root_dir:   媒体库根目录路径 (强烈建议传入)。

    标题来源规则 (关键):
      目录名更规范的前提是「一片一文件夹」布局, 如
        电影库/盗梦空间 (2010)/Inception.mkv   → 标题取 "盗梦空间"
      但扁平布局下文件直接躺在库根目录, 如
        电影库/Inception.2010.1080p.mkv        → 标题必须取文件名,
      否则整个库会被错误合并成一个以目录名命名的作品。
      因此: 当 parent_dir 就是 root_dir 时, 视为扁平布局, 忽略目录名。
    """
    path = Path(file_path)
    filename = path.stem
    result = ParsedName(raw_filename=filename, original_title=filename)

    # 1. S##E## → 动漫
    se_match = RE_SEASON_EPISODE.search(filename)
    if se_match:
        result.season_number = int(se_match.group(1))
        result.episode_number = int(se_match.group(2))
        result.is_anime = True

    # 2. EP## → 动漫, 默认第 1 季
    if not se_match:
        ep_match = RE_EPISODE_ONLY.search(filename)
        if ep_match:
            result.episode_number = int(ep_match.group(1))
            result.season_number = 1
            result.is_anime = True

    # 扁平布局判定: 文件直接位于库根目录 → 不使用目录名作标题
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
    """目录含 'Season N' / 'S##' 子目录 → 判定为动漫目录"""
    try:
        for child in dir_path.iterdir():
            if child.is_dir() and re.match(
                r"(Season\s*\d+|S\d+)", child.name, re.IGNORECASE
            ):
                return True
    except OSError:
        pass
    return False
