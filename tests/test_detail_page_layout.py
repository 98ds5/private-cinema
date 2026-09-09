"""
详情页布局回归锁 —— 小窗口下选集与播放按钮不能被压成 0 高度。

2026-09-09 用户手点报的三个现象是同一个根因:
  「动漫详情页没有选集」「播放键不对」「全屏正常, 小窗口有问题」

DetailPage 原先是全站唯一没有 QScrollArea 的页面。本页 minimumSizeHint 实测
1002x996 (200x280 海报 + 信息区 + 10 集列表), 而小窗口下页面只拿到 798x524,
Qt 只能硬压: 10 个选集行按钮**全部塌成高度 0** (实测 y=358..358, 49x0),
主播放按钮被压成 97x11。全屏 2409x1352 则一切正常 —— 所以看起来像"小窗口才有的 bug"。

修法: 整页放进 QScrollArea (照 SettingsPage 已验证的形状), 并给长文本 QLabel 设
显式最小宽度, 免得一个长文件名把整页最小宽度顶到 1000px 以上 (横向滚动条是关的)。
红线 (HANDOFF §4.7): 不要给滚动宿主的布局设 setAlignment(), 也不要加 setRowStretch()。
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from app.database import get_session, init_database
from app.models.tables import Episode, Media, MediaFile, Season
from app.ui.pages.detail_page import DetailPage
from app.ui.styles import get_qss

# 实测值: 949x612 的窗口里详情页拿到 798x524, 全屏 2560x1440 里拿到 2409x1352
SMALL = (798, 524)
LARGE = (2409, 1352)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def anime(tmp_path):
    """一部动漫: 1 季 10 集, 每集一个长文件名 (复刻 Cyberpunk Edgerunners 的形状)"""
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        m = Media(title="Cyberpunk Edgerunners", media_type="anime", year=2022)
        s.add(m)
        s.flush()
        season = Season(media_id=m.id, season_number=1, title="第1季")
        s.add(season)
        s.flush()
        for n in range(1, 11):
            ep = Episode(season_id=season.id, episode_number=n,
                         title=f"第{n}集", status="unwatched")
            s.add(ep)
            s.flush()
            f = tmp_path / f"Edgerunners.S01E{n:02d}.1080p.WEB-DL.x264.AAC.mkv"
            f.write_bytes(b"\0" * 1024)
            s.add(MediaFile(media_id=m.id, episode_id=ep.id,
                            file_path=str(f), file_name=f.name, file_size=1024,
                            duration=1444, width=1920, height=1080,
                            video_codec="h264", hdr_type="SDR",
                            parse_status="success"))
        s.commit()
        return m.id


def _render(page, app, size):
    page.setAttribute(Qt.WA_DontShowOnScreen, True)
    # 独立构造的 DetailPage 吃不到 MainWindow 的 setStyleSheet, 而按钮高度是
    # QSS 里的 padding 撑出来的 (primaryAction 有样式 97x37, 没样式 81x26)。
    # 不套 QSS 的话测的就不是用户看到的那个控件。
    page.setStyleSheet(get_qss("echo", "dark"))
    page.resize(*size)
    page.show()
    for _ in range(4):
        app.processEvents()


@pytest.fixture
def page(app, anime):
    p = DetailPage(anime, stats_service=None)
    yield p
    p.deleteLater()


class TestDetailPageSmallWindow:

    def test_has_scroll_area(self, page):
        assert len(page.findChildren(QScrollArea)) == 1, \
            "详情页必须自带滚动区: 它的 minimumSizeHint 比小窗口还大"

    @pytest.mark.parametrize("size", [SMALL, LARGE],
                             ids=["小窗798x524", "全屏2409x1352"])
    def test_no_button_collapses_to_zero_height(self, page, app, size):
        _render(page, app, size)
        zero = [b.text() for b in page.findChildren(QPushButton) if b.height() == 0]
        assert not zero, f"{size} 下有 {len(zero)} 个按钮被压成 0 高度: {zero[:4]}"

    def test_all_ten_episodes_are_reachable(self, page, app):
        """10 集的播放按钮都要在, 且小窗下能滚动到"""
        _render(page, app, SMALL)
        plays = [b for b in page.findChildren(QPushButton) if b.text() == "▶ 播放"]
        assert len(plays) == 10, f"选集行数不对: {len(plays)}"
        assert all(b.height() > 0 for b in plays), "有选集按钮高度为 0"

        scroll = page.findChild(QScrollArea)
        assert scroll.verticalScrollBar().maximum() > 0, \
            "内容超出视口时必须能滚动, 否则最后几集永远点不到"

    def test_main_play_button_keeps_its_height(self, page, app):
        """
        真正的不变量是"不低于自身 sizeHint", 而不是某个写死的像素值:
        写死 37 会在没套 QSS 时假失败 (默认按钮只有 26 高),
        而 bug 版是 97x11 —— 远低于 sizeHint, 一样抓得住。
        """
        _render(page, app, SMALL)
        main = [b for b in page.findChildren(QPushButton)
                if b.objectName() == "primaryAction"]
        assert len(main) == 1
        assert main[0].height() >= main[0].sizeHint().height() - 1, \
            (f"主播放按钮被压扁: 实得 {main[0].width()}x{main[0].height()}, "
             f"自身要求 {main[0].sizeHint().width()}x{main[0].sizeHint().height()}")

    def test_content_fits_horizontally(self, page, app):
        """横向滚动条是关的, 所以内容必须能缩进视口宽度, 否则右侧被裁掉"""
        _render(page, app, SMALL)
        scroll = page.findChild(QScrollArea)
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        host = scroll.widget()
        assert host.width() <= scroll.viewport().width() + 1, \
            (f"内容宽 {host.width()} > 视口宽 {scroll.viewport().width()}, "
             f"右侧会被裁掉 —— 长文件名把最小宽度顶上去了")

    def test_anime_section_title_says_episodes(self, page):
        """动漫页应写"选集", 不是"视频文件" —— 用户报"没有选集"时这也占一份"""
        assert "选集" in page._files_title.text(), page._files_title.text()
        assert "10" in page._files_title.text(), page._files_title.text()

    def test_scroll_host_has_no_layout_alignment(self, page):
        """
        HANDOFF §4.7 红线: 滚动宿主的布局一旦带 alignment, widgetResizable 就会
        按视口而不是 minimumSizeHint 定尺寸 → 内容被压扁。
        (末尾的 addStretch() 不算 alignment, SettingsPage 就是这么做的且可用)
        """
        lay = page.findChild(QScrollArea).widget().layout()
        assert lay is not None
        assert int(lay.alignment()) == 0, \
            f"滚动宿主布局被设了 alignment={lay.alignment()}, 会重现压扁 bug"

    def test_host_is_never_smaller_than_its_minimum_size_hint(self, page, app):
        """这才是控件塌成 0 高度的直接机制: 宿主被压到 minimumSizeHint 以下"""
        _render(page, app, SMALL)
        host = page.findChild(QScrollArea).widget()
        assert host.height() >= host.minimumSizeHint().height() - 1, \
            (f"宿主被压到 minimumSizeHint 以下: 高 {host.height()} < "
             f"{host.minimumSizeHint().height()}")
