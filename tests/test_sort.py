"""
排序逻辑回归锁: 中英文排序标签统一映射、名称/年份的语言学排序 (拼音序、
大小写不敏感)、时间类排序的确定性次级键, 以及下拉框到卡片重排的 UI seam。
语言学断言依赖系统 locale, 先探针再决定 skip (NOCASE_OK / PINYIN_OK)。
"""
import locale
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from app.database import get_session, init_database
from app.models.tables import Media, MediaFile
from app.services.stats import StatsService
from app.ui.pages.library_page import LibraryPage


def _collates(a, b):
    try:
        return locale.strxfrm(a) < locale.strxfrm(b)
    except Exception:
        return False


NOCASE_OK = _collates("apple", "Zebra")     # 大小写不敏感: a < Z
PINYIN_OK = _collates("痴迷", "海绵宝宝")      # 拼音序: chi < hai

_T0 = datetime(2026, 9, 1, 12, 0, 0)

# (id, title, year, created_at 偏移分钟, last_watched_at 偏移分钟/None)
_SEED = [
    (1, "apple",       2020, 10, None),
    (2, "Zebra",       2021, 20, 130),      # 看得较早
    (3, "Pacific Rim", 2019, 30, 150),      # 看得最近
    (4, "痴迷",         2021, 40, None),
    (5, "海绵宝宝",      2020, 50, None),
    (6, "帕丁顿1",      None, 60, None),    # 年份没解析出来
    (7, "头号玩家",      2018, 70, None),
    (8, "时间并列A",     2017, 80, None),    # 与 id9 添加时间完全相同
    (9, "时间并列B",     2017, 80, None),
]

# 名称: 英文大小写不敏感 A-Z, 中文按拼音 (chi<hai<pa<shi<tou)
WANT_TITLE = ["apple", "Pacific Rim", "Zebra",
              "痴迷", "海绵宝宝", "帕丁顿1", "时间并列A", "时间并列B", "头号玩家"]
# 年份降序, NULL 垫底; 同年内按名称 (2021: Zebra<痴迷, 2020: apple<海绵宝宝)
WANT_YEAR = ["Zebra", "痴迷",
             "apple", "海绵宝宝",
             "Pacific Rim",
             "头号玩家",
             "时间并列A", "时间并列B",
             "帕丁顿1"]
# 添加时间降序 (最新在前); 并列时 id 降序
WANT_ADDED = ["时间并列B", "时间并列A",
              "头号玩家", "帕丁顿1", "海绵宝宝", "痴迷",
              "Pacific Rim", "Zebra", "apple"]
# 最近观看: 看过的按时间降序在前; 没看过的 (NULL) 按添加时间降序垫底
WANT_RECENT = ["Pacific Rim", "Zebra",
               "时间并列B", "时间并列A", "头号玩家", "帕丁顿1",
               "海绵宝宝", "痴迷", "apple"]


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def seeded(tmp_path):
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        for mid, title, year, c_off, w_off in _SEED:
            s.add(Media(id=mid, title=title, media_type="movie", year=year,
                        status="unwatched",
                        created_at=_T0 + timedelta(minutes=c_off),
                        last_watched_at=(_T0 + timedelta(minutes=w_off))
                        if w_off is not None else None))
            s.flush()
            f = tmp_path / f"m{mid}.mkv"
            f.write_bytes(b"\0" * 32)
            s.add(MediaFile(media_id=mid, file_path=str(f), file_name=f.name,
                            duration=100, parse_status="success"))
        s.commit()
    return StatsService()


def _titles(result):
    return [it["title"] for it in result["items"]]


# ==========================================================================
# 数据层: StatsService.get_library_list (LibraryPage.refresh 调的就是它)
# ==========================================================================
class TestSortDataLayer:

    def test_ui_label_year_is_recognized(self, seeded):
        """UI 下拉传的是中文"年份", 服务层必须识别"""
        got = _titles(seeded.get_library_list(sort="年份"))
        assert got == WANT_YEAR, (
            f"选『年份』没有按年份排 (旧 bug: 静默退回名称序)\n实得: {got}\n期望: {WANT_YEAR}")

    def test_canonical_year_key_still_works(self, seeded):
        """旧默认值/英文键不能改坏 (向后兼容)"""
        assert _titles(seeded.get_library_list(sort="year")) == WANT_YEAR

    @pytest.mark.skipif(not (NOCASE_OK and PINYIN_OK),
                        reason="系统 locale 不提供中文语言学排序")
    def test_name_sort_full_order(self, seeded):
        got = _titles(seeded.get_library_list(sort="名称"))
        assert got == WANT_TITLE, (
            f"名称排序不对 (BINARY 序会把 Zebra 排 apple 前、中文按码点)\n"
            f"实得: {got}\n期望: {WANT_TITLE}")

    @pytest.mark.skipif(not NOCASE_OK, reason="系统 locale 大小写敏感")
    def test_name_sort_is_case_insensitive(self, seeded):
        got = _titles(seeded.get_library_list(sort="名称"))
        assert got.index("apple") < got.index("Zebra"), \
            "BINARY 序 'Z'(90) < 'a'(97) 会把 Zebra 排在 apple 前面"

    @pytest.mark.skipif(not PINYIN_OK, reason="系统 locale 非拼音序")
    def test_name_sort_uses_pinyin_for_chinese(self, seeded):
        got = _titles(seeded.get_library_list(sort="名称"))
        cn = [t for t in got if not t.isascii()]
        assert cn == ["痴迷", "海绵宝宝", "帕丁顿1", "时间并列A", "时间并列B", "头号玩家"], \
            f"中文该按拼音 (chi<hai<pa<shi<tou), 实得 {cn}"

    def test_default_sort_is_by_name(self, seeded):
        assert (_titles(seeded.get_library_list())
                == _titles(seeded.get_library_list(sort="名称")))

    def test_added_time_desc_with_deterministic_ties(self, seeded):
        got = _titles(seeded.get_library_list(sort="添加时间"))
        assert got == WANT_ADDED, (
            f"添加时间该降序、并列 (同批扫描) 按 id 降序\n实得: {got}\n期望: {WANT_ADDED}")

    def test_recent_watched_desc_with_deterministic_tail(self, seeded):
        got = _titles(seeded.get_library_list(sort="最近观看"))
        assert got == WANT_RECENT, (
            f"看过的该按最近观看降序在前, 没看过的按添加时间降序垫底 "
            f"(旧实现 NULL 段按 rowid 裸奔)\n实得: {got}\n期望: {WANT_RECENT}")

    def test_recent_via_canonical_key(self, seeded):
        assert _titles(seeded.get_library_list(sort="recent")) == WANT_RECENT

    def test_unknown_sort_falls_back_to_name(self, seeded):
        assert (_titles(seeded.get_library_list(sort="乱写的"))
                == _titles(seeded.get_library_list(sort="名称"))), \
            "未知排序键该退回名称序而不是崩"

    def test_title_pagination_slices_the_sorted_list(self, seeded):
        """offset/limit 切的是排好序之后的列表"""
        full = _titles(seeded.get_library_list(sort="名称"))
        p1 = _titles(seeded.get_library_list(sort="名称", limit=3))
        p2 = _titles(seeded.get_library_list(sort="名称", limit=3, offset=3))
        assert p1 + p2 == full[:6], f"分页切片错位: {p1} + {p2} vs {full[:6]}"
        assert seeded.get_library_list(sort="名称", limit=3)["total"] == len(_SEED), \
            "total 是过滤后的总数, 不该被分页影响"

    def test_year_pagination_slices_the_sorted_list(self, seeded):
        p1 = _titles(seeded.get_library_list(sort="年份", limit=4))
        assert p1 == WANT_YEAR[:4]


# ==========================================================================
# UI 真实 seam: 下拉框 → _on_sort → refresh → 卡片顺序
# ==========================================================================
class TestSortUiSeam:

    @pytest.fixture
    def page(self, app, seeded):
        p = LibraryPage(view="all", stats_service=seeded)
        p.setAttribute(Qt.WA_DontShowOnScreen, True)
        p.show()
        p.refresh()
        app.processEvents()
        yield p
        p.deleteLater()

    @staticmethod
    def _card_titles(page):
        return [c.findChild(QLabel, "cardTitle").text() for c in page._cards]

    def test_combo_year_reorders_cards(self, page, app):
        """下拉切到『年份』后卡片必须重排成年份序"""
        assert self._card_titles(page) != WANT_YEAR, "初始就该是名称序, 不是年份序"
        page._sort.setCurrentIndex(1)          # ["名称","年份",...] → 年份
        app.processEvents()
        got = self._card_titles(page)
        assert got == WANT_YEAR, f"下拉切『年份』后卡片没重排对\n实得: {got}\n期望: {WANT_YEAR}"

    def test_recent_view_hides_the_combo(self, app, seeded):
        """recent 视图排序固定, 下拉框应隐藏"""
        p = LibraryPage(view="recent", stats_service=seeded)
        p.setAttribute(Qt.WA_DontShowOnScreen, True)
        p.show()
        app.processEvents()
        assert not p._sort.isVisible(), "recent 视图的排序下拉该隐藏"
        p.deleteLater()

    def test_all_view_keeps_the_combo_visible(self, page):
        assert page._sort.isVisible(), "普通视图的排序下拉不能误藏"
