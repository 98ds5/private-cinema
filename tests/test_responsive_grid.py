"""
响应式网格 —— 「缩小界面和全屏界面的资源窗口一样大而且结构一样」的修复锁。

原来 `LibraryPage._render_cards` 把列数写死成 `i // 4, i % 4`:
窗口缩到最小和拉到全屏都是 4 列、卡片一样大 —— 全屏时右边空一大片,
缩小时横向又挤不下。现在列数由 `_cols_for(viewport 宽度)` 算出来,
`resizeEvent` 防抖触发 `_reflow()` 重摆。

设计上刻意把**建卡片**和**摆卡片**分开: 拖窗口只重摆, 不销毁重建 15 张卡片。

⚠️ 所有涉及尺寸的断言都必须**真实等待**(`_settle`): resizeEvent → 防抖 60ms →
重排, 光调 processEvents() 只过去几微秒, 什么都等不到。
"""
import time

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.database import get_session, init_database
from app.models.tables import Media, MediaFile
from app.services.stats import StatsService
from app.ui.main_window import MainWindow
from app.ui.pages.library_page import LibraryPage
from app.ui.widgets.media_card import MediaCard
from app.utils.config_utils import load_config

N_MEDIA = 15
_KEEPALIVE = []          # 留住 spy 的 Python 引用, 否则会被 GC 掉、过滤器静默失效


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _settle(app, page, width=None, seconds=0.4):
    """改宽度并**真实等过**防抖(60ms)+布局生效, 紧循环 processEvents 是等不到的"""
    if width is not None:
        page.resize(width, 700)
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _pos_of(grid, card):
    idx = grid.indexOf(card)
    assert idx >= 0, "卡片根本不在网格里"
    row, col, _rs, _cs = grid.getItemPosition(idx)
    return (row, col)


class _TopLevelShowSpy(QObject):
    """application 级 event filter, 抓"顶层窗口被 Show"(详见 test_library_page.py)"""

    def __init__(self, app, ignore=("MainWindow",)):
        super().__init__(app)
        self._app, self.ignore, self.shown = app, set(ignore), []
        _KEEPALIVE.append(self)
        app.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Show and isinstance(obj, QWidget)
                and obj.isWindow() and type(obj).__name__ not in self.ignore):
            self.shown.append((type(obj).__name__, obj.objectName(),
                               obj.width(), obj.height()))
        return False

    def stop(self):
        self._app.removeEventFilter(self)


@pytest.fixture
def spy(app):
    s = _TopLevelShowSpy(app)
    yield s
    s.stop()


def _seed(tmp_path, n=N_MEDIA):
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        for i in range(n):
            m = Media(title=f"作品{i:02d}", media_type="movie", year=2020,
                      status="unwatched")
            s.add(m)
            s.flush()
            s.add(MediaFile(media_id=m.id, file_path=f"g{i}.mkv",
                            file_name=f"g{i}.mkv", duration=100,
                            width=1920, height=1080, hdr_type="SDR",
                            parse_status="success"))
        s.commit()


@pytest.fixture
def page(app, tmp_path):
    _seed(tmp_path)
    p = LibraryPage(view="all", stats_service=StatsService())
    p.setAttribute(Qt.WA_DontShowOnScreen, True)
    p.resize(900, 700)
    p.show()
    _settle(app, p, None, 0.4)          # 等构造里那个 singleShot(100, refresh)
    assert p._cards, "卡片没建出来, 后面的断言全是空转"
    return p


# ==========================================================================
# 列数算术
# ==========================================================================
class TestColsMath:
    def test_narrowest_is_one_column(self, page):
        assert page._cols_for(1) == 1
        assert page._cols_for(100) == 1

    def test_exactly_one_card_width_is_one_column(self, page):
        assert page._cols_for(MediaCard.CARD_W) == 1

    def test_second_column_needs_width_plus_gap(self, page):
        need = 2 * MediaCard.CARD_W + LibraryPage.GRID_SPACING
        assert page._cols_for(need) == 2
        assert page._cols_for(need - 1) == 1, "差 1 像素就该只放得下一列"

    def test_zero_width_keeps_previous_cols(self, page):
        """还没显示过时 viewport 宽度是 0, 不该据此把列数打成 1"""
        page._cols = 5
        assert page._cols_for(0) == 5
        assert page._cols_for(-20) == 5

    def test_never_returns_below_one(self, page):
        page._cols = 0
        for w in (-100, 0, 1, 50):
            assert page._cols_for(w) >= 1

    def test_monotonic_in_width(self, page):
        widths = list(range(200, 2000, 37))
        cols = [page._cols_for(w) for w in widths]
        assert cols == sorted(cols), "宽度越大列数反而变少, 说明算式不是单调的"


# ==========================================================================
# 重排行为
# ==========================================================================
class TestReflow:
    def test_wide_gets_more_columns_than_narrow(self, app, page):
        _settle(app, page, 1600)
        wide = page._cols
        _settle(app, page, 400)
        narrow = page._cols
        assert wide > narrow, f"全屏 {wide} 列 vs 缩小 {narrow} 列, 没有响应式效果"

    def test_fullscreen_uses_the_width(self, app, page):
        """用户原话: 全屏时"一样大而且结构一样不合适" —— 右边不该空一大片"""
        _settle(app, page, 1600)
        assert page._cols >= 6, f"1600 逻辑像素只排了 {page._cols} 列"

    def test_minimised_window_falls_back_to_few_columns(self, app, page):
        _settle(app, page, 400)
        assert page._cols <= 2, f"400 像素宽还排 {page._cols} 列, 卡片会横向溢出"

    def test_cards_are_not_rebuilt_on_resize(self, app, page):
        before = list(page._cards)
        for w in (1600, 500, 1200, 380):
            _settle(app, page, w)
        assert list(page._cards) == before, "拖窗口不该销毁重建卡片"
        assert all(c.parent() is not None for c in before), "重排把卡片的父级弄丢了"
        assert all(not c.isWindow() for c in before), "重排把卡片变成了顶层窗口"

    def test_exactly_one_slot_per_card(self, app, page):
        """重排必须先 removeWidget, 漏了就会一张卡占两个格子 → count 翻倍"""
        for w in (1600, 900, 500, 380):
            _settle(app, page, w)
            assert page._grid.count() == len(page._cards), (
                f"宽度 {w}: 网格 {page._grid.count()} 个格子 vs "
                f"{len(page._cards)} 张卡片")

    def test_positions_follow_the_column_count(self, app, page):
        for w in (1600, 900, 500):
            _settle(app, page, w)
            cols = page._cols
            for i, card in enumerate(page._cards):
                assert _pos_of(page._grid, card) == (i // cols, i % cols), (
                    f"宽度 {w} 第 {i} 张卡片位置不对 (cols={cols})")

    def test_no_card_lands_beyond_the_last_column(self, app, page):
        for w in (1600, 900, 500, 380):
            _settle(app, page, w)
            used = max(_pos_of(page._grid, c)[1] for c in page._cards) + 1
            assert used <= page._cols, (
                f"宽度 {w}: 卡片排到了第 {used} 列, 但只算了 {page._cols} 列")

    def test_cards_stay_inside_the_viewport(self, app, page):
        """最右一列的卡片右边缘不能超出网格可用宽度, 否则就是横向溢出被裁"""
        for w in (1600, 900, 500, 380):
            _settle(app, page, w)
            avail = page._viewport_width()
            right = page._cols * MediaCard.CARD_W + \
                (page._cols - 1) * LibraryPage.GRID_SPACING
            assert right <= avail + LibraryPage.GRID_SPACING, (
                f"宽度 {w}: {page._cols} 列要 {right}px, 只有 {avail}px")

    def test_reflow_spawns_no_toplevel_window(self, app, page, spy):
        for w in (1600, 400, 1200, 600):
            _settle(app, page, w)
        assert spy.shown == [], f"重排闪出了顶层窗口: {spy.shown[:3]}"

    def test_refresh_after_resize_keeps_the_new_column_count(self, app, page):
        """refresh() 会重建卡片, 重摆时必须沿用当前列数而不是退回写死的 4"""
        _settle(app, page, 1600)
        cols = page._cols
        assert cols != 4, "这条测试需要列数不是 4 才有意义"
        page.refresh()
        assert page._cols == cols
        for i, card in enumerate(page._cards):
            assert _pos_of(page._grid, card) == (i // cols, i % cols)


# ==========================================================================
# 防抖与收敛守卫
# ==========================================================================
class TestDebounce:
    def test_timer_is_single_shot_with_the_documented_interval(self, page):
        assert page._reflow_timer.isSingleShot(), "防抖定时器必须是 single-shot"
        assert page._reflow_timer.interval() == LibraryPage.REFLOW_DEBOUNCE_MS
        assert page._reflow_timer.parent() is page, \
            "定时器要认页面当爹, 否则页面销毁后它还会触发"

    def test_resize_starts_the_debounce(self, app, page):
        page._reflow_timer.stop()
        page.resize(page.width() + 37, page.height())
        app.processEvents()
        assert page._reflow_timer.isActive(), "resizeEvent 没有启动防抖定时器"

    def test_rapid_resizes_collapse_into_one_reflow(self, app, page):
        """拖窗口时每个像素都来一次 resizeEvent, 防抖的意义就是只重排一次"""
        calls = []
        orig = page._place_cards
        page._place_cards = lambda *a, **k: (calls.append(1), orig())[1]
        try:
            base = page.width()
            for d in range(0, 60):          # 模拟连续拖动 60 像素
                page.resize(base + d, 700)
                app.processEvents()         # 只派发事件, 不 sleep → 定时器不该到期
            assert calls == [], "还没到防抖时间就重排了, 拖窗口会卡"
            _settle(app, page, None, 0.3)
            assert len(calls) <= 1, f"一次拖动重排了 {len(calls)} 次"
        finally:
            page._place_cards = orig

    def test_same_column_count_does_not_relayout(self, app, page):
        """
        收敛守卫: 列数没变就不该动网格。
        没有它, 滚动条出现/消失会改变视口宽度 → 列数在两个值之间来回抖。
        """
        _settle(app, page, 900)
        calls = []
        page._place_cards = lambda *a, **k: calls.append(1)
        try:
            page._reflow()                  # 宽度没变
            assert calls == [], "列数没变却重排了 → 滚动条会来回震荡"
        finally:
            del page._place_cards           # 删掉实例属性, 恢复类方法

    def test_changed_column_count_does_relayout(self, app, page):
        calls = []
        orig = page._place_cards
        page._place_cards = lambda *a, **k: calls.append(1)
        try:
            page._cols = 99                 # 伪造一个和实际不符的列数
            page._reflow()
            assert calls == [1], "列数变了却没重排"
            assert page._cols != 99
        finally:
            page._place_cards = orig


# ==========================================================================
# QScrollArea 红线 (HANDOFF §4.7)
# ==========================================================================
class TestScrollAreaRedLines:
    def test_grid_has_no_alignment(self, page):
        """给顶层布局设 alignment 会让滚动条上限恒为 0、下方卡片被裁"""
        assert int(page._grid.alignment()) == 0

    def test_grid_has_no_row_stretch(self, page):
        assert page._grid.rowStretch(0) == 0

    def test_scrollbar_range_is_not_pinned_to_zero(self, app, page):
        """15 部作品在窄窗口下必然超过一屏, 滚动条上限必须 > 0 才滚得动"""
        _settle(app, page, 400)
        sb = page._scroll.verticalScrollBar()
        assert sb.maximum() > 0, \
            f"滚动条上限是 0 (max={sb.maximum()}), 下面的卡片看不到也点不到"


# ==========================================================================
# 真实 seam: MainWindow 里四个 LibraryPage 都要响应式
# ==========================================================================
@pytest.fixture
def win(app, tmp_path):
    _seed(tmp_path)
    cfg = load_config()                      # 绝不传 {} (HANDOFF §4.1)
    cfg["system"]["db_path"] = str(tmp_path / "cinema.db")
    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1400, 800)
    w.show()
    _settle(app, w, None, 0.5)
    return w


def _library_pages(win):
    out = []
    for i in range(win.pages.count()):
        p = win.pages.widget(i)
        if isinstance(p, LibraryPage):
            out.append(p)
    return out


class TestThroughMainWindow:
    def test_all_four_library_pages_are_present(self, win):
        assert len(_library_pages(win)) == 4

    def test_widen_then_narrow_changes_columns(self, app, win):
        """
        ⚠️ 必须先把页面设成**当前页**再量: QStackedWidget 里非当前页是隐藏的,
        隐藏页收不到 resizeEvent, 量到的会是构造时的旧宽度
        (第一版就是这么写的, 1600 和 700 都读出 3 列, 看着像响应式失效)。
        """
        page = next(p for p in _library_pages(win) if p._cards)
        win.pages.setCurrentWidget(page)
        _settle(app, win, None, 0.3)

        win.resize(1600, 900)
        _settle(app, win, None, 0.4)
        wide = page._cols

        win.resize(700, 600)
        _settle(app, win, None, 0.4)
        narrow = page._cols

        assert wide >= 6, f"1600x900 下只排了 {wide} 列, 全屏右边会空一大片"
        assert wide > narrow, f"拉宽 {wide} 列 / 缩小 {narrow} 列, 没有响应式效果"

    def test_page_hidden_during_resize_catches_up_when_shown(self, app, win):
        """
        真实操作顺序: 在首页把窗口拉宽 → 再点"全部影视"。
        那个页面在拉宽时是隐藏的, 切过来时必须按**新**宽度重排, 不能停在旧列数。
        """
        page = next(p for p in _library_pages(win) if p._cards)
        win.pages.setCurrentWidget(win._home_page)
        _settle(app, win, None, 0.3)
        stale = page._cols

        win.resize(1600, 900)
        _settle(app, win, None, 0.4)
        win.pages.setCurrentWidget(page)
        _settle(app, win, None, 0.4)

        assert page._cols >= 6, (
            f"切过来后只有 {page._cols} 列 (切换前 {stale}), 没跟上窗口宽度")
        assert page._grid.count() == len(page._cards)
        for i, card in enumerate(page._cards):
            assert _pos_of(page._grid, card) == (i // page._cols, i % page._cols)

    def test_hidden_page_still_reflows_when_shown(self, app, win):
        """非当前页在 QStackedWidget 里是隐藏的, 切过去时列数必须已经对"""
        win._nav.setCurrentRow(2)            # 电影
        _settle(app, win, None, 0.4)
        cur = win.pages.currentWidget()
        assert isinstance(cur, LibraryPage)
        assert cur._cols >= 1
        assert cur._grid.count() == len(cur._cards)
