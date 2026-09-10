"""
画质 / 音轨 / 字幕的纯格式化函数。
数据来自 ffprobe 解析出的容器元信息 (存在 MediaFile 上), 不是刮削。
全部是纯函数: 不碰数据库、不碰 Qt。
"""
import json
from typing import Optional

# 分辨率档位表 (按高度取第一个满足的档; 宽银幕特例见 resolution_label)
_RESOLUTIONS = (
    (4320, "8K"),
    (2160, "4K"),
    (1440, "2K"),
    (1080, "1080p"),
    (720, "720p"),
    (576, "576p"),
    (480, "480p"),
)

# HDR 分级, 值越大等级越高; 一部作品多个文件时用它挑最高的那个
_HDR_RANK = {
    "dolby vision": 5, "dv": 5, "dovi": 5,
    "hdr10+": 4, "hdr10 plus": 4,
    "hdr10": 3,
    "hlg": 2,
    "hdr": 1,
    "sdr": 0,
}
_HDR_LABEL = {5: "DV", 4: "HDR10+", 3: "HDR10", 2: "HLG", 1: "HDR", 0: "SDR"}

# 中文字幕的语言代码: 不同压制组/容器写法不统一, 全都认一遍
_CHI_LANGS = {
    "chi", "zho", "zh", "zh-cn", "zh-hans", "zh-hant", "zh-hk", "zh-tw",
    "chinese", "cmn", "yue", "cant", "cantonese", "tw", "hk", "cn", "sc", "tc",
}

# 语言码转中文短名。ffprobe 的写法不统一, ISO 639-1 / 639-2/T / 639-2/B 三套都收,
# 否则 deu、chi 这类码会原样漏到界面上。
_LANG_ZH = {
    # 中文的各种写法
    "chi": "中", "zho": "中", "zh": "中", "cmn": "中", "zh-cn": "中",
    "zh-hans": "中", "zh-hant": "中", "zh-hk": "港", "zh-tw": "台", "yue": "粤",
    "cant": "粤", "cantonese": "粤",
    # 639-2/B (ffprobe 最常见的一套)
    "eng": "英", "ger": "德", "fre": "法", "dut": "荷", "cze": "捷", "rum": "罗",
    "gre": "希腊", "heb": "希", "jpn": "日", "kor": "韩", "spa": "西", "ita": "意",
    "por": "葡", "rus": "俄", "ara": "阿", "pol": "波", "swe": "瑞", "nor": "挪",
    "dan": "丹", "fin": "芬", "hun": "匈", "ukr": "乌", "may": "马来", "fil": "菲",
    "hrv": "克", "bul": "保", "tha": "泰", "vie": "越", "ind": "印尼", "tur": "土",
    "hin": "印地", "per": "波斯", "arm": "亚美尼亚", "geo": "格鲁吉亚",
    "bur": "缅", "slo": "斯洛伐克", "alb": "阿尔巴尼亚", "baq": "巴斯克",
    "tib": "藏", "wel": "威尔士", "ice": "冰岛", "est": "爱沙尼亚",
    # 639-2/T (同一语言的另一套码)
    "deu": "德", "fra": "法", "nld": "荷", "ces": "捷", "ron": "罗", "ell": "希腊",
    "slk": "斯洛伐克", "sqi": "阿尔巴尼亚", "eus": "巴斯克", "cym": "威尔士",
    "isl": "冰岛", "hye": "亚美尼亚", "kat": "格鲁吉亚", "mya": "缅", "bod": "藏",
    "fas": "波斯", "msa": "马来", "tgl": "菲", "lav": "拉脱维亚", "lit": "立陶宛",
    "mkd": "马其顿", "slv": "斯洛文尼亚", "ben": "孟加拉", "tam": "泰米尔",
    "tel": "泰卢固", "urd": "乌尔都", "gle": "爱尔兰", "glg": "加利西亚",
    # 639-1 (两字母)
    "en": "英", "de": "德", "fr": "法", "es": "西", "it": "意", "pt": "葡",
    "ru": "俄", "ja": "日", "ko": "韩", "ar": "阿", "nl": "荷", "sv": "瑞",
    "no": "挪", "da": "丹", "fi": "芬", "pl": "波", "tr": "土", "he": "希",
    "hu": "匈", "cs": "捷", "el": "希腊", "ro": "罗", "bg": "保", "th": "泰",
    "vi": "越", "id": "印尼", "uk": "乌", "hi": "印地", "ms": "马来", "tl": "菲",
    "is": "冰岛", "et": "爱沙尼亚", "lv": "拉脱维亚", "lt": "立陶宛",
    "sr": "塞尔维亚", "ca": "加泰", "gl": "加利西亚", "eu": "巴斯克",
    "cy": "威尔士", "ga": "爱尔兰", "mk": "马其顿", "sl": "斯洛文尼亚",
    "bn": "孟加拉", "ta": "泰米尔", "te": "泰卢固", "ur": "乌尔都",
}

# 归一之后算中文的名字 (粤/港/台 也算中文字幕)
_CHI_NAMES = {"中", "粤", "港", "台"}
# 只单独列出中/英/日/韩, 其余归到 "+N": 整盘资源一条能有几十条字幕轨,
# 全列出来是噪音, 真正有用的信息是"有没有中文字幕"。
_PREFERRED = ("中", "英", "日", "韩")


def canon_lang(code) -> str:
    """把任意写法的语言码归一到中文短名; 认不出就原样返回"""
    key = str(code or "").strip().lower()
    return _LANG_ZH.get(key, key)

# 音频编码的展示名
_AUDIO_NAME = {
    "dts": "DTS", "dts-hd": "DTS-HD", "dts-hd ma": "DTS-HD MA", "truehd": "TrueHD",
    "eac3": "E-AC-3", "ac3": "AC-3", "aac": "AAC", "flac": "FLAC",
    "opus": "Opus", "vorbis": "Vorbis", "mp3": "MP3", "pcm": "PCM",
    "dts-es": "DTS-ES", "dtshd_ma": "DTS-HD MA",
}

# 视频编码的展示名
_VIDEO_NAME = {"h264": "H.264", "avc": "H.264", "hevc": "HEVC", "h265": "HEVC",
               "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG-2"}


# ----------------------------------------------------------------------
# 基础判定
# ----------------------------------------------------------------------
def _by_height(h: int) -> str:
    for threshold, label in _RESOLUTIONS:
        if h >= threshold:
            return label
    return f"{h}p" if h else ""


def resolution_label(width=None, height=None) -> str:
    """
    分辨率标签, 认不出返回空串。
    要同时看宽高: 变形宽银幕 (如 3840x1608) 是 4K 母版裁出来的, 只看高度会落到
    1440 档误报成 "2K", 所以宽度达标时直接按 4K 判。
    """
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return ""
    if not w and not h:
        return ""
    if w >= 7000:
        return "8K"
    if w >= 3000:
        return "4K"
    if w >= 1800:
        # 1920 宽这一档再按高度细分 (1080p / 偶尔的 720p 拉伸源)
        return _by_height(h) or "1080p"
    return _by_height(h)


def hdr_rank(hdr_type: Optional[str]) -> int:
    """HDR 等级, 未知/空按 SDR(0) 处理"""
    key = str(hdr_type or "").strip().lower()
    return _HDR_RANK.get(key, 0)


def hdr_label(hdr_type: Optional[str]) -> str:
    """HDR 等级转展示名。SDR 也返回 "SDR" 而不是空, 好让界面能区分"没 HDR"和"没读到"。"""
    return _HDR_LABEL.get(hdr_rank(hdr_type), "SDR")


def best_hdr(hdr_types) -> str:
    """
    一部作品的多个文件里挑 HDR 等级最高的那个, 返回原始值。
    数据层不做展示转换 (标签交给 hdr_label), 否则统计层的字段里会混进 UI 文案。
    """
    best_raw, best_rank = "", -1
    for h in (hdr_types or []):
        r = hdr_rank(h)
        if r > best_rank:
            best_raw, best_rank = h, r
    return best_raw


def video_label(codec: Optional[str]) -> str:
    key = str(codec or "").strip().lower()
    return _VIDEO_NAME.get(key, key.upper() if key else "")


def audio_label(codec: Optional[str]) -> str:
    key = str(codec or "").strip().lower()
    return _AUDIO_NAME.get(key, key.upper() if key else "")


# ----------------------------------------------------------------------
# 轨道 JSON
# ----------------------------------------------------------------------
def parse_tracks(raw) -> list:
    """audio_tracks / subtitle_tracks 存的是 JSON 字符串; 坏数据一律返回 [],
    不能因为一条脏 JSON 就崩页面。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    return []


def _track_lang(track: dict) -> str:
    """轨道语言码转归一后的中文短名 (认不出就原样返回小写码)"""
    return canon_lang(track.get("lang") or track.get("language"))


def has_chinese_subtitle(raw) -> bool:
    """有没有中文字幕轨 (chi/zho/zh/zh-Hans/yue/cant 等写法都算)"""
    for t in parse_tracks(raw):
        code = str(t.get("lang") or t.get("language") or "").strip().lower()
        if canon_lang(code) in _CHI_NAMES or code in _CHI_LANGS:
            return True
    return False


def subtitle_summary(raw, brief: bool = False) -> str:
    """
    字幕概览。整盘资源可能有几十条字幕轨, 全列是噪音, 所以只单独列出中/英/日/韩,
    其余归到 "+N"。brief=True 给详情页行内用 (要短), False 给 tooltip 用 (带轨数)。
    """
    tracks = parse_tracks(raw)
    if not tracks:
        return "无字幕轨"

    # 先归一再比对: 同一门语言在不同压制里写法不一 (jpn / ja), 拿原始码匹配会漏
    names = [_track_lang(t) for t in tracks]
    uniq = list(dict.fromkeys(n for n in names if n))
    chi = any(n in _CHI_NAMES for n in uniq)
    found = [n for n in _PREFERRED if n in uniq]
    rest = max(0, len(uniq) - len(found))
    tail = f" +{rest}" if rest > 0 else ""
    joined = "·".join(found)

    if brief:
        if not chi:
            return f"无中字{(' · ' + joined + tail) if joined else ''}"
        return f"{joined}{tail}"
    if not chi:
        return f"字幕 无中文{(' · ' + joined + tail) if joined else ''}（共 {len(tracks)} 轨）"
    return f"字幕 {joined}{tail}（共 {len(tracks)} 轨）"


def audio_summary(raw, brief: bool = False) -> str:
    """
    音轨概览: brief=True 给行内短标签 (如 "DTS/FLAC"), False 给 tooltip
    (如 "音轨 DTS(日) / FLAC(英)", 带上语言方便挑播放配置)。
    """
    tracks = parse_tracks(raw)
    if not tracks:
        return ""

    if brief:
        names = [audio_label(t.get("codec")) for t in tracks]
        # dict.fromkeys 去重且保序: 同一编码的多条音轨不必重复占位
        return "/".join(dict.fromkeys(n for n in names if n))

    parts = []
    for t in tracks:
        codec = audio_label(t.get("codec"))
        if not codec:
            continue
        lang = _track_lang(t)
        parts.append(f"{codec}({lang})" if lang else codec)
    # 去重且保序: 同一语言同一编码常连着好几条, 重复列出没有信息量
    parts = list(dict.fromkeys(parts))
    return "音轨 " + " / ".join(parts) if parts else ""


# ----------------------------------------------------------------------
# 组合展示
# ----------------------------------------------------------------------
def badges_from_media(media: dict) -> list:
    """
    卡片角标, 如 ["4K", "DV", "中字"]。
    吃 StatsService.get_library_list 聚合出的字段 (best_width/best_height/best_hdr/
    has_chi_sub), 多文件时那边已取最高档。卡片窄, 标签要短, 且 SDR 不上角标。
    """
    badges = []
    res = resolution_label(media.get("best_width"), media.get("best_height"))
    if res:
        badges.append(res)
    hdr = hdr_label(media.get("best_hdr"))
    if hdr != "SDR":
        badges.append(hdr)
    if media.get("has_chi_sub"):
        badges.append("中字")
    return badges


def format_tech_line(f: dict) -> str:
    """
    详情页每个文件的行内技术信息 (要短, 完整信息见 tech_tooltip)。
    f 含 width/height/frame_rate/video_codec/hdr_type/duration/file_size/
    audio_tracks/subtitle_tracks, 缺哪个跳过哪个。
    """
    parts = []

    w, h = f.get("width"), f.get("height")
    if w and h:
        parts.append(f"{w}×{h}")
    res = resolution_label(w, h)
    if res:
        parts.append(res)
    if f.get("video_codec"):
        parts.append(video_label(f["video_codec"]))
    # HDR 一律显示, 含 SDR: 把 SDR 藏起来就分不清"这片子没 HDR"和"程序没读到"
    parts.append(hdr_label(f.get("hdr_type")))

    fps = f.get("frame_rate")
    if fps:
        try:
            parts.append(f"{float(fps):g}fps")
        except (TypeError, ValueError):
            parts.append(str(fps))

    audio = audio_summary(f.get("audio_tracks"), brief=True)
    if audio:
        parts.append(audio)
    elif f.get("audio_codec"):
        parts.append(audio_label(f["audio_codec"]))

    subs = subtitle_summary(f.get("subtitle_tracks"), brief=True)
    if subs:
        parts.append(subs)

    if f.get("duration"):
        parts.append(fmt_dur(f["duration"]))
    if f.get("file_size"):
        parts.append(fmt_size(f["file_size"]))

    return " · ".join(p for p in parts if p)


def tech_tooltip(f: dict) -> str:
    """完整技术信息放 tooltip: 带音轨语言、字幕轨数、容器格式"""
    lines = [f"文件: {f.get('file_name') or f.get('file_path') or '—'}"]

    w, h = f.get("width"), f.get("height")
    bits = []
    if w and h:
        bits.append(f"{w}×{h} {resolution_label(w, h)}".strip())
    if f.get("video_codec"):
        bits.append(video_label(f["video_codec"]))
    bits.append(hdr_label(f.get("hdr_type")))
    if f.get("frame_rate"):
        bits.append(f"{float(f['frame_rate']):g}fps")
    if bits:
        lines.append("视频: " + " · ".join(bits))

    audio = audio_summary(f.get("audio_tracks"))
    if audio:
        lines.append(audio)
    subs = subtitle_summary(f.get("subtitle_tracks"))
    if subs:
        lines.append(subs)
    if f.get("container_format"):
        lines.append(f"容器: {f['container_format']}")
    tail = []
    if f.get("duration"):
        tail.append(f"时长 {fmt_dur(f['duration'])}")
    if f.get("file_size"):
        tail.append(f"体积 {fmt_size(f['file_size'])}")
    if tail:
        lines.append(" · ".join(tail))

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 单位格式化
# ----------------------------------------------------------------------
def fmt_dur(seconds) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(num_bytes) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
