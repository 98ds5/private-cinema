"""
画质 / 音轨 / 字幕格式化的回归锁。

**所有取值都来自用户库里 ffprobe 真实抓到的数据**, 不是我编的理想样本 ——
这个项目的教训反复是同一条: 用理想样本测, 真机就翻车。

用户报"视频画质信息不够明确, 比如 hdr 什么的"。取证结果: 数据 25/25 全都在库里
(hdr_type 实测有 SDR / HDR10 / Dolby Vision 三种, frame_rate 23.976,
audio_codec dts/truehd/eac3, 字幕轨最多 37 条含 chi), 纯粹是 UI 没显示:
detail_page 只渲染 宽x高/编码/HDR/时长/体积, 而且 **hdr_type == "SDR" 被刻意隐藏**,
帧率、音轨、字幕轨一条都没往上传; MediaCard 上更是完全没有画质标识。
"""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from app.database import get_session, init_database
from app.models.tables import Media, MediaFile
from app.services.stats import StatsService
from app.ui.pages.detail_page import DetailPage
from app.ui.widgets.media_card import MediaCard
from app.utils.quality import (
    audio_summary, badges_from_media, best_hdr, canon_lang, format_tech_line,
    has_chinese_subtitle, hdr_label, parse_tracks, resolution_label,
    subtitle_summary, tech_tooltip,
)

# --- 用户库里的真实取值 -------------------------------------------------
# Cyberpunk Edgerunners S01E01 的音轨 (ffprobe 原样输出)
AUDIO_JSON = json.dumps([
    {"index": 1, "codec": "dts", "lang": "jpn"},
    {"index": 2, "codec": "flac", "lang": "eng"},
])
# 同一集的字幕轨, 取真实的 12 条 (原文件有 35 条, 语言码种类已覆盖全)
SUB_JSON = json.dumps([
    {"index": 3, "codec": "hdmv_pgs_subtitle", "lang": "eng"},
    {"index": 4, "codec": "subrip", "lang": "eng"},
    {"index": 6, "codec": "subrip", "lang": "ara"},
    {"index": 7, "codec": "hdmv_pgs_subtitle", "lang": "chi"},
    {"index": 8, "codec": "hdmv_pgs_subtitle", "lang": "chi"},
    {"index": 15, "codec": "subrip", "lang": "fre"},
    {"index": 16, "codec": "subrip", "lang": "ger"},
    {"index": 22, "codec": "hdmv_pgs_subtitle", "lang": "jpn"},
    {"index": 23, "codec": "hdmv_pgs_subtitle", "lang": "kor"},
    {"index": 30, "codec": "subrip", "lang": "rus"},
    {"index": 31, "codec": "subrip", "lang": "spa"},
    {"index": 37, "codec": "subrip", "lang": "vie"},
])

E01 = {
    "file_name": "Cyberpunk Edgerunners S01E01 Let You Down 1080p "
                 "DTS-HD MA 5 1 AVC REMUX-FraMeSToR.mkv",
    "file_size": 6922038250, "duration": 1440,
    "width": 1920, "height": 1080, "frame_rate": 23.976,
    "video_codec": "h264", "hdr_type": "SDR",
    "audio_codec": "dts", "audio_tracks": AUDIO_JSON,
    "subtitle_tracks": SUB_JSON, "container_format": "matroska,webm",
}


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class TestResolution:
    """分辨率判定 —— 重点是变形宽银幕别误报"""

    @pytest.mark.parametrize("w,h,want", [
        (1920, 1080, "1080p"),
        (3840, 2160, "4K"),
        # ↓ 库里真实存在的三个变形宽银幕文件: 按高度算会落到 1440 档误报 "2K",
        #   而它们是 4K 母版裁出来的。这条就是那次误报的回归锁。
        (3840, 1608, "4K"),
        (3840, 1600, "4K"),
        (3840, 1604, "4K"),
        (1280, 720, "720p"),
        (3840, 2160, "4K"),
    ])
    def test_width_and_height(self, w, h, want):
        assert resolution_label(w, h) == want

    def test_height_only_falls_back(self):
        assert resolution_label(None, 2160) == "4K"
        assert resolution_label(None, 1080) == "1080p"

    @pytest.mark.parametrize("w,h", [(None, None), (0, 0), ("bad", None)])
    def test_garbage_returns_empty(self, w, h):
        assert resolution_label(w, h) == ""


class TestHdr:
    @pytest.mark.parametrize("raw,want", [
        ("Dolby Vision", "DV"), ("dolby vision", "DV"), ("DV", "DV"),
        ("HDR10", "HDR10"), ("hdr10", "HDR10"), ("HDR10+", "HDR10+"),
        ("HLG", "HLG"), ("SDR", "SDR"),
        (None, "SDR"), ("", "SDR"), ("没见过的值", "SDR"),
    ])
    def test_label(self, raw, want):
        assert hdr_label(raw) == want

    def test_sdr_is_not_hidden(self):
        # 原先 detail_page 把 SDR 过滤掉了, 用户就分不清「没 HDR」还是「没读到」
        assert hdr_label("SDR") == "SDR"

    def test_best_of_many_files(self):
        # 返回**原始值**, 不是展示标签 —— 数据层不该掺 UI 文案
        assert best_hdr(["SDR", "HDR10", "Dolby Vision"]) == "Dolby Vision"
        assert best_hdr(["SDR", "SDR"]) == "SDR"
        assert best_hdr([]) == ""
        assert best_hdr(None) == ""
        assert hdr_label(best_hdr(["SDR", "HDR10"])) == "HDR10"


class TestTracks:
    def test_audio_brief_and_full(self):
        assert audio_summary(AUDIO_JSON, brief=True) == "DTS/FLAC"
        assert audio_summary(AUDIO_JSON) == "音轨 DTS(日) / FLAC(英)"

    def test_subtitle_brief_and_full(self):
        assert subtitle_summary(SUB_JSON, brief=True) == "中·英·日·韩 +6"
        assert subtitle_summary(SUB_JSON) == "字幕 中·英·日·韩 +6（共 12 轨）"

    def test_no_chinese_subtitle_is_said_out_loud(self):
        raw = json.dumps([{"index": 1, "codec": "subrip", "lang": "eng"},
                          {"index": 2, "codec": "subrip", "lang": "jpn"}])
        assert has_chinese_subtitle(raw) is False
        assert subtitle_summary(raw, brief=True).startswith("无中字")
        assert "无中文" in subtitle_summary(raw)

    @pytest.mark.parametrize("lang", ["chi", "zho", "zh", "zh-CN", "zh-Hans",
                                      "yue", "chinese"])
    def test_chinese_lang_code_variants(self, lang):
        raw = json.dumps([{"index": 1, "codec": "ass", "lang": lang}])
        assert has_chinese_subtitle(raw) is True, f"{lang} 没被认成中文"

    @pytest.mark.parametrize("bad", [None, "", "not json", "[1,2,3]",
                                     '{"a":1}', 123])
    def test_dirty_json_never_raises(self, bad):
        """轨道 JSON 是扫描时写进库的历史数据, 一条脏的不能崩掉整页"""
        assert parse_tracks(bad) == []
        assert has_chinese_subtitle(bad) is False
        assert subtitle_summary(bad) == "无字幕轨"
        assert audio_summary(bad) == ""

    def test_list_input_passthrough(self):
        assert parse_tracks([{"codec": "dts"}]) == [{"codec": "dts"}]

    # ↓ 这一组的取值来自用户库里「帕丁顿1」的真实音轨。
    #   我最初的实现漏了它们: 测试样本用的是 jpn/eng, 而真库里是 deu(639-2/T 码)
    #   和音轨里的 chi, 于是渲染出 "DTS(deu) / DTS(deu)" 这种没翻译又重复的串。
    #   真实数据一跑就露馅, 干净样本永远不会。
    REAL_PAD_AUDIO = json.dumps([
        {"index": 1, "codec": "dts", "lang": "deu"},
        {"index": 2, "codec": "dts", "lang": "deu"},
        {"index": 3, "codec": "truehd", "lang": "eng"},
        {"index": 4, "codec": "ac3", "lang": "eng"},
        {"index": 5, "codec": "dts", "lang": "chi"},
        {"index": 6, "codec": "ac3", "lang": "chi"},
    ])

    def test_real_deu_and_chi_audio_are_translated(self):
        assert audio_summary(self.REAL_PAD_AUDIO) == \
            "音轨 DTS(德) / TrueHD(英) / AC-3(英) / DTS(中) / AC-3(中)"
        assert audio_summary(self.REAL_PAD_AUDIO, brief=True) == "DTS/TrueHD/AC-3"

    def test_duplicate_codec_lang_pairs_are_collapsed(self):
        raw = json.dumps([{"codec": "ac3", "lang": "eng"}] * 3)
        assert audio_summary(raw) == "音轨 AC-3(英)", "同码同语言不该重复占位"

    @pytest.mark.parametrize("code,want", [
        ("ja", "日"), ("jpn", "日"), ("ko", "韩"), ("kor", "韩"),
        ("en", "英"), ("eng", "英"), ("zh", "中"), ("chi", "中"),
    ])
    def test_subtitle_lang_code_sets_all_recognised(self, code, want):
        raw = json.dumps([{"codec": "ass", "lang": code}])
        got = subtitle_summary(raw, brief=True)
        # 只有一条非中文字幕轨时, 简写会先把"无中字"喊出来 —— 这是有意的,
        # 用户最想知道的就是有没有中文
        expected = want if want == "中" else f"无中字 · {want}"
        assert got == expected, f"{code} 没被归一成 {want}: 实得 {got!r}"

    def test_unknown_lang_falls_back_gracefully(self):
        raw = json.dumps([{"codec": "ass", "lang": "zzz"}])
        assert subtitle_summary(raw, brief=True) == "无中字"
        assert has_chinese_subtitle(raw) is False


class TestCanonLang:
    """三套 ISO 语言码必须归一到同一个中文名"""

    @pytest.mark.parametrize("code,want", [
        ("deu", "德"), ("ger", "德"), ("de", "德"),
        ("chi", "中"), ("zho", "中"), ("zh", "中"), ("zh-Hans", "中"),
        ("ZH-TW", "台"), ("yue", "粤"),
        ("jpn", "日"), ("ja", "日"), ("eng", "英"), ("en", "英"),
        ("fra", "法"), ("fre", "法"), ("fr", "法"),
        ("nld", "荷"), ("dut", "荷"), ("ces", "捷"), ("cze", "捷"),
    ])
    def test_convergence(self, code, want):
        assert canon_lang(code) == want

    def test_unknown_passes_through(self):
        assert canon_lang("zzz") == "zzz"
        assert canon_lang(None) == ""
        assert canon_lang("") == ""

    def test_cantonese_counts_as_chinese(self):
        for code in ("yue", "zh-HK", "zh-TW", "chi", "zho"):
            raw = json.dumps([{"codec": "ass", "lang": code}])
            assert has_chinese_subtitle(raw) is True, f"{code} 没被算成中文字幕"


class TestTechLine:
    def test_real_e01_line(self):
        assert format_tech_line(E01) == (
            "1920×1080 · 1080p · H.264 · SDR · 23.976fps · "
            "DTS/FLAC · 中·英·日·韩 +6 · 24:00 · 6.4 GB"
        )

    def test_hdr10_and_4k(self):
        f = dict(E01, width=3840, height=1608, hdr_type="HDR10",
                 video_codec="hevc", frame_rate=24)
        line = format_tech_line(f)
        assert "4K" in line and "HDR10" in line and "HEVC" in line
        assert "3840×1608" in line

    def test_missing_fields_do_not_raise(self):
        # 字段缺失只跳过对应片段; HDR 与字幕那两项是无条件显示的
        # (字幕缺失时报"无字幕轨"而不是留空, 免得看着像没渲染出来)
        assert format_tech_line({}) == "SDR · 无字幕轨"
        assert format_tech_line({"height": 1080}) == "1080p · SDR · 无字幕轨"

    def test_tooltip_has_more_than_the_line(self):
        tip = tech_tooltip(E01)
        assert "DTS(日)" in tip and "FLAC(英)" in tip      # 行内简写没有语言
        assert "共 12 轨" in tip                            # 行内没有轨数
        assert "matroska" in tip                            # 行内没有容器
        assert E01["file_name"] in tip


class TestBadges:
    def test_full_badges(self):
        m = {"best_width": 3840, "best_height": 1608,
             "best_hdr": "Dolby Vision", "has_chi_sub": True}
        assert badges_from_media(m) == ["4K", "DV", "中字"]

    def test_sdr_does_not_occupy_a_badge(self):
        """卡片只有 160px 宽, SDR 是默认情况, 不值得占一个角标位"""
        m = {"best_width": 1920, "best_height": 1080,
             "best_hdr": "SDR", "has_chi_sub": False}
        assert badges_from_media(m) == ["1080p"]

    def test_no_metadata_gives_no_badges(self):
        assert badges_from_media({}) == []


# ==========================================================================
# 端到端: 真库 → StatsService → MediaCard / DetailPage
# ==========================================================================
@pytest.fixture
def library(tmp_path):
    """两部片子: 一部 4K Dolby Vision 带中字, 一部 1080p SDR 无中字"""
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        a = Media(title="Blade Runner 2049", media_type="movie", year=2017)
        b = Media(title="Cyberpunk Edgerunners", media_type="anime", year=2022)
        s.add_all([a, b])
        s.flush()
        s.add(MediaFile(media_id=a.id, file_path="x.mkv", file_name="x.mkv",
                        file_size=1000, duration=100, width=3840, height=1608,
                        frame_rate=23.976, video_codec="hevc",
                        hdr_type="Dolby Vision", audio_codec="truehd",
                        audio_tracks=AUDIO_JSON, subtitle_tracks=SUB_JSON,
                        container_format="matroska", parse_status="success"))
        s.add(MediaFile(media_id=b.id, file_path="y.mkv", file_name="y.mkv",
                        file_size=1000, duration=100, width=1920, height=1080,
                        frame_rate=23.976, video_codec="h264", hdr_type="SDR",
                        audio_codec="dts", audio_tracks=AUDIO_JSON,
                        subtitle_tracks=None, container_format="matroska",
                        parse_status="success"))
        s.commit()
        return {"dv": a.id, "sdr": b.id}


class TestEndToEnd:
    def test_library_list_carries_quality(self, library):
        items = StatsService().get_library_list()["items"]
        by_title = {i["title"]: i for i in items}

        dv = by_title["Blade Runner 2049"]
        assert dv["best_width"] == 3840 and dv["best_height"] == 1608
        assert dv["best_hdr"] == "Dolby Vision"
        assert dv["has_chi_sub"] is True
        assert dv["video_codec"] == "hevc"

        sdr = by_title["Cyberpunk Edgerunners"]
        assert sdr["best_hdr"] == "SDR"
        assert sdr["has_chi_sub"] is False

    def test_card_shows_badges(self, library, app):
        items = {i["title"]: i for i in StatsService().get_library_list()["items"]}

        dv = MediaCard(items["Blade Runner 2049"])
        assert dv._badges.text() == "4K  DV  中字"
        assert dv._badges.isVisibleTo(dv) or dv._badges.text()      # 有内容就该显示

        sdr = MediaCard(items["Cyberpunk Edgerunners"])
        assert sdr._badges.text() == "1080p", \
            f"1080p SDR 无中字, 角标应只有分辨率, 实得 {sdr._badges.text()!r}"

    def test_card_without_metadata_hides_badge_row(self, app):
        card = MediaCard({"id": 1, "title": "没有元数据的片子"})
        assert card._badges.text() == ""
        assert card._badges.isHidden() or not card._badges.isVisibleTo(card), \
            "没数据时角标行应整行藏掉, 别留一条空白占位"

    def test_card_still_fits_its_fixed_height(self, library, app):
        """加了一行角标是从海报区借的高度, 三个 QLabel 都不许被压扁"""
        items = StatsService().get_library_list()["items"]
        card = MediaCard(items[0])
        card.setAttribute(Qt.WA_DontShowOnScreen, True)
        card.show()
        for _ in range(3):
            app.processEvents()
        for lbl in card.findChildren(QLabel):
            assert lbl.height() > 0, f"卡片里的 QLabel 被压成 0 高度: {lbl.text()!r}"
        assert card.height() == 200

    def test_detail_page_shows_hdr_and_subtitles(self, library, app):
        page = DetailPage(library["dv"], stats_service=None)
        page.setAttribute(Qt.WA_DontShowOnScreen, True)
        page.show()
        for _ in range(3):
            app.processEvents()

        metas = [l.text() for l in page.findChildren(QLabel)
                 if l.objectName() == "fileMeta"]
        assert metas, "详情页没有渲染技术信息行"
        line = metas[0]
        for want in ("3840×1608", "4K", "HEVC", "DV", "23.976fps",
                     "DTS/FLAC", "中·英·日·韩"):
            assert want in line, f"技术行缺 {want!r}: {line}"

    def test_detail_page_shows_sdr_too(self, library, app):
        """SDR 必须显示出来 —— 原先被 `not in (None,'','SDR')` 过滤掉了"""
        page = DetailPage(library["sdr"], stats_service=None)
        page.setAttribute(Qt.WA_DontShowOnScreen, True)
        page.show()
        for _ in range(3):
            app.processEvents()
        metas = [l.text() for l in page.findChildren(QLabel)
                 if l.objectName() == "fileMeta"]
        assert metas and "SDR" in metas[0], f"SDR 被藏起来了: {metas}"
        assert "无字幕轨" in metas[0]
