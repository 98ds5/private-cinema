"""影视库切页/搜索时闪出一串无字小窗的回归锁。

锁住构造 MediaCard / refresh / 切页 / 搜索期间不得 show 顶层窗口;
断言用 event filter 抓 Show 事件 —— 小窗生灭在构造函数里, 事后查不到。
"""
import time

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.database import get_session, init_database
from app.models.tables import Media, MediaFile
from app.services.stats import StatsService
from app.ui.main_window import MainWindow
from app.ui.pages.library_page import LibraryPage
from app.ui.widgets.media_card import MediaCard
from app.utils.config_utils import load_config

N_MEDIA = 15        # 接近真实库规模
_KEEPALIVE = []     # 留住 spy 的 Python 引用, 见 _TopLevelShowSpy 文档


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _TopLevelShowSpy(QObject):
    """application 级 event filter: 记下每一次顶层窗口 show; 必须留住 Python 引用, 否则被回收后过滤器静默失效"""

    def __init__(self, app, ignore=()):
        super().__init__(app)
        self._app = app
        self.ignore = set(ignore)
        self.shown = []
        _KEEPALIVE.append(self)
        app.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Show and isinstance(obj, QWidget)
                and obj.isWindow()
                and type(obj).__name__ not in self.ignore):
            self.shown.append((type(obj).__name__, obj.objectName(),
                               obj.width(), obj.height()))
        return False

    def stop(self):
        self._app.removeEventFilter(self)


@pytest.fixture
def spy(app):
    s = _TopLevelShowSpy(app, ignore=("MainWindow",))
    yield s
    s.stop()


def _seed(tmp_path, n=N_MEDIA):
    """种 15 部作品, 且每部都带画质元数据 —— 没有角标数据就不会构造那个 QLabel"""
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        for i in range(n):
            m = Media(title=f"作品{i:02d}",
                      media_type="movie" if i % 2 else "anime",
                      year=2020, status="unwatched")
            s.add(m)
            s.flush()
            s.add(MediaFile(media_id=m.id, file_path=f"x{i}.mkv",
                            file_name=f"x{i}.mkv", duration=100,
                            width=3840, height=2160, hdr_type="Dolby Vision",
                            subtitle_tracks='[{"index":1,"codec":"ass","lang":"chi"}]',
                            parse_status="success"))
        s.commit()


@pytest.fixture
def page(app, tmp_path):
    _seed(tmp_path)
    p = LibraryPage(view="all", stats_service=StatsService())
    p.setAttribute(Qt.WA_DontShowOnScreen, True)
    p.show()
    p.refresh()
    app.processEvents()
    return p


# ==========================================================================
# 最小 seam: 构造一张卡片
# ==========================================================================
class TestCardConstruction:
    def test_building_a_card_shows_no_toplevel_window(self, spy):
        """setVisible(True) 打在尚无父级的 QLabel 上会当场显示顶层窗口 —— 被锁的 bug 本体"""
        card = MediaCard({"id": 1, "title": "Pacific Rim", "year": 2013,
                          "status": "unwatched", "best_width": 3840,
                          "best_height": 2160, "best_hdr": "Dolby Vision",
                          "has_chi_sub": True})
        assert spy.shown == [], \
            f"构造一张卡片闪出了 {len(spy.shown)} 个顶层窗口: {spy.shown}"
        assert card._badges.text() == "4K  DV  中字"

    def test_building_a_card_without_badges_shows_nothing(self, spy):
        card = MediaCard({"id": 2, "title": "没有元数据"})
        assert spy.shown == []
        assert card._badges.isHidden(), "没角标时那一行该藏掉, 别留空白占位"

    def test_building_many_cards_shows_nothing(self, page, spy):
        # 参数顺序有讲究: page 必须在 spy 前, 否则 fixture 里那次离屏 p.show() 会被当成闪窗记下来
        items = StatsService().get_library_list()["items"]
        assert len(items) == N_MEDIA
        for it in items:
            MediaCard(it)
        assert spy.shown == [], f"建 {len(items)} 张卡闪出 {len(spy.shown)} 个窗口"


# ==========================================================================
# 实际操作路径: refresh / 切页 / 打字搜索
# ==========================================================================
class TestRefreshPath:
    def test_refresh_shows_no_toplevel_window(self, page, spy):
        page.refresh()
        assert spy.shown == [], f"refresh() 闪出了 {len(spy.shown)} 个顶层窗口"

    def test_repeated_refresh_shows_nothing(self, page, spy, app):
        for n in range(5):
            page.refresh()
            assert spy.shown == [], f"第 {n + 1} 次 refresh 闪出了 {spy.shown[:3]}"
            app.processEvents()

    def test_search_refresh_shows_nothing(self, page, spy):
        for text in ("作品01", "作品", "", "作品02"):
            page.set_search(text)
            assert spy.shown == [], f"搜索 {text!r} 闪出了 {spy.shown[:3]}"

    def test_cards_do_not_accumulate(self, page, app):
        """setParent(None) 的原始动机也要守住: 否则旧卡片留在子树里, 搜索时每敲一字成倍堆积"""
        for _ in range(3):
            page.refresh()
            app.processEvents()
        got = len(page.findChildren(MediaCard))
        assert got == N_MEDIA, f"{N_MEDIA} 部作品应恰好 {N_MEDIA} 张卡片, 实得 {got}"


def _pump_for(app, seconds):
    """带真实 sleep 地泵事件: 光调 processEvents() 等不到 100ms 的 singleShot 到期"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def win(app, tmp_path):
    _seed(tmp_path)
    cfg = load_config()                      # 绝不传 {}
    cfg["system"]["db_path"] = str(tmp_path / "cinema.db")
    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    # LibraryPage 构造里排了 singleShot(100, refresh), 必须真等过去, 否则断言全是空转
    _pump_for(app, 0.4)
    return w


class TestThroughMainWindow:
    def test_nav_switch_shows_no_toplevel_window(self, win, app, spy):
        """左侧导航逐页切换都不应闪出顶层窗口"""
        for row in range(win._nav.count()):
            win._nav.setCurrentRow(row)
            assert spy.shown == [], (
                f"切到第 {row} 页 ({win._nav.item(row).text()}) "
                f"闪出 {len(spy.shown)} 个顶层窗口: {spy.shown[:3]}")
            app.processEvents()

    def test_typing_in_search_shows_no_toplevel_window(self, win, app, spy):
        for ch in "作品0":
            win._search.setText(win._search.text() + ch)
            assert spy.shown == [], f"打 {ch!r} 时闪出了 {spy.shown[:3]}"
            app.processEvents()

    def test_the_flashing_widget_was_the_badge_label(self, win, app):
        """把根因钉死在控件上: 角标 QLabel 任何时刻都待在卡片子树里, 不是顶层窗口"""
        cards = win._home_page.findChildren(MediaCard)
        pages = [p for p in win.pages.children() if isinstance(p, LibraryPage)]
        for p in pages:
            cards += p.findChildren(MediaCard)
        assert cards, "一张卡片都没建出来, 这条断言等于没测"
        for c in cards:
            assert c._badges.parent() is not None, "角标 QLabel 没有父级"
            assert not c._badges.isWindow(), "角标 QLabel 成了顶层窗口"
