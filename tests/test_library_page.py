"""
「切页/搜索时屏幕闪一串无字小窗」的回归锁。

用户报"为什么每次操作都很多弹窗啊, 比如搜索和切换到别的界面", 追问后确认症状:
一串**无字小窗口**闪过去、会自己关, 只有 全部影视 / 电影 / 动漫 / 最近观看
这四个页面会 —— 它们全是 `LibraryPage`; 首页(HomePage)和设置(SettingsPage)不会。

== 根因 (不是我最初猜的那个) ==
`MediaCard.__init__` 里:

    self._badges = QLabel("  ".join(badges))   # 此刻还没有父级
    self._badges.setVisible(bool(badges))      # ← 致命的一行
    layout.addWidget(self._badges)             # ← 下一行才被塞回卡片

`setVisible(True)` 作用在一个**没有父级**的 widget 上, Qt 会把它当成顶层窗口
并**立刻显示**; 紧接着 addWidget 把它重新收进卡片子树, 窗口随即消失。
于是每张卡片构造时都会在自己位置上闪出一个 15~94 x 18 的小窗
(脱离 MainWindow 子树 → QSS 不生效 → 看着是**无字**的空白条)。
15 张卡 × 每次 refresh()(切页 / 搜索防抖到期都会调) = "一串小窗口闪过去"。
只有 LibraryPage 建 MediaCard, 所以首页和设置不闪。

== 走过的弯路 (都值得记) ==
1. 第一个假设是 `_render_cards` 的 `setParent(None)` 把旧卡片变成顶层窗口。
   **差分探针证伪了它**: 加不加 hide(), 那 15 张卡在 setParent(None) 之后
   `isHidden()` 都是 True、`isVisible()` 都是 False —— Qt6 自己就会藏起来。
2. 照着错误假设写的测试因此**没有牙**: 把 hide() 注释掉, 8 条测试照样全绿。
   → 写完回归测试必须**把 fix 撤掉验一次**, 看它真的变红。
3. 真正定位靠的是给**真 app** 装 application 级 event filter, 记录每一次
   "顶层窗口被 Show", 用户还没操作就已经刷出 90 条 cardBadges 的 SHOW/HIDE。
   离屏 + isVisible() 那套内省完全看不到这个 bug。

⚠️ 断言用的是 event filter 抓 Show 事件, 不是事后查 topLevelWidgets():
   那个小窗在构造函数里就生灭了, 构造返回之后再查什么都查不到。
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

N_MEDIA = 15        # 与用户真库同量级(15 部)
_KEEPALIVE = []     # 留住 spy 的 Python 引用, 见 _TopLevelShowSpy 文档


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _TopLevelShowSpy(QObject):
    """
    application 级 event filter: 记下每一次"顶层窗口被 show 出来"。

    ⚠️ 两个坑:
      - 必须留住 Python 引用。`installEventFilter` 只在 C++ 侧存指针,
        传一个没父对象的临时对象会被 Python 立刻回收 → 过滤器**静默失效**
        (诊断脚本第一版就是这么废的: 连 MainWindow 自己的 SHOW 都没记到)。
        这里既认 app 当父对象, 又往 _KEEPALIVE 里存一份, 双保险。
      - Show 事件是 send 而不是 post, 但 application 级过滤器照样能看到。
    """

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
        """
        这就是 bug 本体: setVisible(True) 打在一个还没父级的 QLabel 上,
        它会当场变成一个顶层窗口显示出来, 然后才被 addWidget 收走。
        """
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
        # 参数顺序有讲究: page 必须在 spy 前面, 否则 fixture 里那次离屏 p.show()
        # 会被 spy 当成"闪出的顶层窗口"记下来
        items = StatsService().get_library_list()["items"]
        assert len(items) == N_MEDIA
        for it in items:
            MediaCard(it)
        assert spy.shown == [], f"建 {len(items)} 张卡闪出 {len(spy.shown)} 个窗口"


# ==========================================================================
# 用户实际走的路径: refresh / 切页 / 打字搜索
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
        """
        setParent(None) 的原始动机也得守住: 不清父子关系, 旧卡片会留在子树里,
        搜索框每敲一个字符 refresh 一次就会成倍堆积 (原注释记着实测 15 部查出 30 张)。
        """
        for _ in range(3):
            page.refresh()
            app.processEvents()
        got = len(page.findChildren(MediaCard))
        assert got == N_MEDIA, f"{N_MEDIA} 部作品应恰好 {N_MEDIA} 张卡片, 实得 {got}"


def _pump_for(app, seconds):
    """
    带**真实 sleep** 地泵事件。

    ⚠️ 光调 processEvents() 等不到定时器: 紧循环里只过去几微秒, 100ms 的
    singleShot 根本没到期 (这条本项目已经栽过两次: 搜索防抖、卡片延迟加载)。
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def win(app, tmp_path):
    _seed(tmp_path)
    cfg = load_config()                      # 绝不传 {} (HANDOFF §4.1)
    cfg["system"]["db_path"] = str(tmp_path / "cinema.db")
    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    # 4 个 LibraryPage 各自在构造里排了 singleShot(100, refresh), 必须真的等过去,
    # 否则一张卡片都没建出来, 后面的断言全是空转
    _pump_for(app, 0.4)
    return w


class TestThroughMainWindow:
    def test_nav_switch_shows_no_toplevel_window(self, win, app, spy):
        """用户报的就是「左侧几个窗口切换都会有, 除了首页和设置」"""
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
        """
        把根因钉死在具体控件上: 角标 QLabel 必须始终待在卡片子树里,
        任何时刻都不是顶层窗口。
        """
        cards = win._home_page.findChildren(MediaCard)
        pages = [p for p in win.pages.children() if isinstance(p, LibraryPage)]
        for p in pages:
            cards += p.findChildren(MediaCard)
        assert cards, "一张卡片都没建出来, 这条断言等于没测"
        for c in cards:
            assert c._badges.parent() is not None, "角标 QLabel 没有父级"
            assert not c._badges.isWindow(), "角标 QLabel 成了顶层窗口"
