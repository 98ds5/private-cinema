"""
详情页布局回归锁: 小窗口下选集与播放按钮不能被压成 0 高度。
整页放进 QScrollArea 后, 宿主高度不得低于自身 minimumSizeHint,
内容必须能横向缩进视口。红线: 滚动宿主的布局不设 alignment, 不加行 stretch。
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from app.database import get_session, init_database
from app.models.tables import Episode, Media, MediaFile, Season
from app.ui.pages.detail_page import DetailPage
from app.ui.styles import get_qss

# 小窗口与全屏两种页面尺寸
SMALL = (798, 524)
LARGE = (2409, 1352)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def anime(tmp_path):
    """一部动漫: 1 季 10 集, 每集一个长文件名"""
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
    # 按钮高度由 QSS 的 padding 撑出来, 不套 QSS 测的就不是真实控件尺寸
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
        """主播放按钮高度不低于自身 sizeHint, 而不是某个写死的像素值"""
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
        """动漫页分组标题应写「选集」, 不是「视频文件」"""
        assert "选集" in page._files_title.text(), page._files_title.text()
        assert "10" in page._files_title.text(), page._files_title.text()

    def test_scroll_host_has_no_layout_alignment(self, page):
        """滚动宿主的布局不得设置 alignment (否则按视口而不是 minimumSizeHint 定尺寸)"""
        lay = page.findChild(QScrollArea).widget().layout()
        assert lay is not None
        assert int(lay.alignment()) == 0, \
            f"滚动宿主布局被设了 alignment={lay.alignment()}, 会重现压扁 bug"

    def test_host_is_never_smaller_than_its_minimum_size_hint(self, page, app):
        """宿主被压到 minimumSizeHint 以下正是控件塌成 0 高度的机制"""
        _render(page, app, SMALL)
        host = page.findChild(QScrollArea).widget()
        assert host.height() >= host.minimumSizeHint().height() - 1, \
            (f"宿主被压到 minimumSizeHint 以下: 高 {host.height()} < "
             f"{host.minimumSizeHint().height()}")
