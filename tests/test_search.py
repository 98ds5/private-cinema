"""
全局搜索回归锁: 顶部搜索框全站唯一且真实接线、输入即过滤、
回车跳「全部影视」再过滤、关键词跨页面保持。
测试必须真的往 QLineEdit 打字并等防抖定时器, 不直接调 refresh()。
"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from app.database import get_session, init_database
from app.models.tables import Media
from app.ui.main_window import NAV_ALL_ROW, MainWindow
from app.ui.pages.library_page import LibraryPage
from app.utils.config_utils import load_config


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _pump(app, pred, timeout=4.0):
    """泵事件直到 pred() 为真 (防抖是 QTimer, 不泵就永远不触发)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def win(app, tmp_path):
    """真 MainWindow + 一个临时库 (3 部片子, 标题互不包含)"""
    cfg = load_config()                      # 必须用真实配置, 不能传 {}
    cfg["system"]["db_path"] = str(tmp_path / "cinema.db")
    init_database(cfg["system"]["db_path"])

    with get_session() as s:
        for title, kind in (("Cyberpunk Edgerunners", "anime"),
                            ("Inception", "movie"),
                            ("Interstellar", "movie")):
            s.add(Media(title=title, media_type=kind, year=2020,
                        status="unwatched"))
        s.commit()

    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    # 防抖调到 10ms: 测试不该等 200ms × N 条
    w._search_debounce.setInterval(10)
    for _ in range(4):
        app.processEvents()
    yield w
    w.close()
    w.deleteLater()


def _cards(w):
    page = w.pages.currentWidget()
    return len(page._cards) if isinstance(page, LibraryPage) else -1


def _type(w, app, text):
    """
    往顶部搜索框打字, 并确定性地等防抖定时器到期。
    紧循环 processEvents 时间没真正流逝, 判据用 QTimer.isActive(): fire 后变 False。
    """
    w._search.setText(text)
    budget = w._search_debounce.interval() / 1000.0 + 2.0
    deadline = time.time() + budget
    while time.time() < deadline:
        app.processEvents()
        if not w._search_debounce.isActive():
            break
        time.sleep(0.02)
    for _ in range(4):
        app.processEvents()          # 让 deleteLater 的旧卡片真正消失


class TestGlobalSearch:

    def test_there_is_exactly_one_search_box(self, win):
        """全站只能有一个搜索框"""
        boxes = win.findChildren(QLineEdit)
        assert len(boxes) == 1, f"窗口里有 {len(boxes)} 个 QLineEdit"
        assert boxes[0] is win._search
        for i in range(win.pages.count()):
            page = win.pages.widget(i)
            if isinstance(page, LibraryPage):
                assert not page.findChildren(QLineEdit), \
                    "库页面里又出现了自己的搜索框"

    def test_top_search_box_is_actually_wired(self, win, app):
        """顶部框打字必须真的过滤"""
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        assert _cards(win) == 3, "进「全部影视」应看到 3 张卡片"

        _type(win, app, "cyber")
        assert _cards(win) == 1, f"搜 'cyber' 应剩 1 张, 实得 {_cards(win)}"
        assert win.pages.currentWidget()._cards[0]._media_id is not None

    def test_search_is_case_insensitive_and_matches_subtitle(self, win, app):
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        _type(win, app, "INTER")
        assert _cards(win) == 1
        assert "共 1 部" in win.pages.currentWidget()._subtitle.text()

    def test_clearing_search_restores_everything(self, win, app):
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        _type(win, app, "inception")
        assert _cards(win) == 1
        _type(win, app, "")
        assert _pump(app, lambda: _cards(win) == 3), \
            f"清空关键词后应恢复 3 张, 实得 {_cards(win)}"

    def test_no_match_shows_zero_not_stale_cards(self, win, app):
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        _type(win, app, "zzz-不存在的片子")
        assert _pump(app, lambda: _cards(win) == 0), \
            f"无匹配时应清空, 实得 {_cards(win)} (旧卡片没被摘掉)"

    def test_enter_from_home_jumps_to_all_and_filters(self, win, app):
        """回车: 不在库页面时跳到「全部影视」再过滤 (首页也能搜)"""
        win._nav.setCurrentRow(0)
        _pump(app, lambda: win.pages.currentIndex() == 0)
        win._search.setText("cyber")
        win._on_search_enter()          # 等价于按下回车
        assert _pump(app, lambda: isinstance(win.pages.currentWidget(), LibraryPage)), \
            "回车后没跳到库页面"
        assert win._nav.currentRow() == NAV_ALL_ROW
        assert _pump(app, lambda: _cards(win) == 1), \
            f"跳过去后应带着关键词过滤, 实得 {_cards(win)} 张"

    def test_keyword_survives_page_switch(self, win, app):
        """搜索框是全站的: 切到「电影」页关键词要带过去"""
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        _type(win, app, "in")           # Inception + Interstellar
        assert _cards(win) == 2

        win._nav.setCurrentRow(2)       # 电影
        assert _pump(app, lambda: _cards(win) == 2), \
            f"切到电影页应保留关键词 (2 部都是电影), 实得 {_cards(win)}"

        win._nav.setCurrentRow(3)       # 动漫
        assert _pump(app, lambda: _cards(win) == 0), \
            f"动漫页没有匹配 'in' 的, 实得 {_cards(win)}"

    def test_library_page_set_search_api(self, win, app):
        """set_search 的 refresh=False 分支: 不查库, 只记住关键词"""
        win._nav.setCurrentRow(NAV_ALL_ROW)
        _pump(app, lambda: _cards(win) == 3)
        page = win.pages.currentWidget()
        page.set_search("cyber", refresh=False)
        assert page._search_text == "cyber"
        assert _cards(win) == 3, "refresh=False 时不该重查"
        page.refresh()
        assert _cards(win) == 1
