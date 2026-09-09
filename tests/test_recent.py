"""
「最近观看」可点 + 续播的回归锁。

用户报"最近观看也无法点进去具体看的哪个继续看"。取证结果两半都是真的:

1. **点不动**: `home_page._update_recent` 把裸 QLabel 塞进 QHBoxLayout 再包个
   QWidget —— 没有按钮、没有 mousePressEvent、没有手型光标、没有任何信号。
2. **看不出是哪个**: `stats.get_recently_watched` 只返回
   id/title/media_type/watched_at 和裸字符串 `progress = f"{pos}/{dur}s"`,
   于是首页显示 "1440/1440s" 这种东西; 而且它**没有 episode_id** ——
   动漫的进度写在 episodes 表 (见 player._save_progress), 不回查就不知道
   看的是第几集, 也就没法把 episode_id 传给 PlayerService.play()。

⚠️ 点击一律用 QTest.mouseClick 打真实控件, 不直接 emit 信号 ——
   直接 emit 会绕过 mousePressEvent / 按钮 enabled 这些真正会坏掉的环节。
"""
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.database import get_session, init_database
from app.models.tables import Episode, Media, MediaFile, Season
from app.services.player import _save_progress
from app.services.stats import StatsService, progress_text
from app.ui import main_window as mw_mod
from app.ui.main_window import MainWindow
from app.ui.pages.detail_page import DetailPage
from app.ui.pages.home_page import HomePage, RecentRow
from app.utils.config_utils import load_config


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _seed(tmp_path):
    """
    建一个三部作品的临时库:
      动漫 (3 集, 刚用 _save_progress 把进度写在第 3 集)   ← 最新
      电影 (1 个文件, 进度写在 media 表, 看到一半)          ← 一天前
      幽灵条目 (有 last_watched_at 但一个文件都没有)         ← 三天前
    返回 {名称: id} 与关键路径。
    """
    init_database(str(tmp_path / "cinema.db"))
    now = datetime.now()

    anime_file = tmp_path / "edgerunners_e03.mkv"
    movie_file = tmp_path / "inception.mkv"
    for p in (anime_file, movie_file):
        p.write_bytes(b"x")          # 续播那条路径要过 os.path.exists

    with get_session() as s:
        anime = Media(title="Cyberpunk Edgerunners", media_type="anime", year=2022)
        movie = Media(title="Inception", media_type="movie", year=2010,
                      watched_position=3000, watched_duration=6000,
                      last_watched_at=now - timedelta(days=1))
        ghost = Media(title="幽灵条目", media_type="movie", year=2000,
                      last_watched_at=now - timedelta(days=3))
        s.add_all([anime, movie, ghost])
        s.flush()

        season = Season(media_id=anime.id, season_number=1, title="第1季")
        s.add(season)
        s.flush()
        eps = []
        for n in (1, 2, 3):
            ep = Episode(season_id=season.id, episode_number=n,
                         title=f"EP{n}", status="unwatched")
            s.add(ep)
            s.flush()
            s.add(MediaFile(media_id=anime.id, episode_id=ep.id,
                            file_path=str(tmp_path / f"edgerunners_e0{n}.mkv"),
                            file_name=f"edgerunners_e0{n}.mkv",
                            duration=1440, width=1920, height=1080,
                            parse_status="success"))
            eps.append(ep)
        s.add(MediaFile(media_id=movie.id, file_path=str(movie_file),
                        file_name="inception.mkv", duration=6000,
                        width=3840, height=2160, hdr_type="Dolby Vision",
                        parse_status="success"))
        s.commit()
        ep3_id = eps[2].id
        anime_id, movie_id, ghost_id = anime.id, movie.id, ghost.id

    # 走真实的进度写入函数: 动漫的进度落在 episodes 表, media 表只是镜像
    _save_progress(anime_id, 754, 1440, episode_id=ep3_id)

    return {"anime": anime_id, "movie": movie_id, "ghost": ghost_id,
            "ep3": ep3_id, "anime_file": str(anime_file),
            "movie_file": str(movie_file)}


@pytest.fixture
def seeded(tmp_path):
    return _seed(tmp_path)


# ==========================================================================
class TestProgressText:
    @pytest.mark.parametrize("pos,dur,want", [
        (0, 0, "未记录进度"),
        (100, 0, "看到 1:40"),
        (0, 1440, "尚未开始"),
        (754, 1440, "看到 12:34 / 24:00"),
        (3000, 6000, "看到 50:00 / 1:40:00"),
        (1440, 1440, "已看完"),
        (1400, 1440, "已看完"),          # 95% 以上就算看完, 与 _save_progress 同阈值
        (1300, 1440, "看到 21:40 / 24:00"),
    ])
    def test_text(self, pos, dur, want):
        assert progress_text(pos, dur) == want

    def test_none_is_safe(self):
        assert progress_text(None, None) == "未记录进度"


# ==========================================================================
class TestRecentlyWatchedQuery:
    def test_order_and_anime_episode_resolution(self, seeded):
        items = StatsService().get_recently_watched(5)
        assert [i["title"] for i in items] == \
            ["Cyberpunk Edgerunners", "Inception", "幽灵条目"]

        anime = items[0]
        # 关键: 必须认出看的是第 3 集, 并且给出**第 3 集**的文件路径
        assert anime["episode_number"] == 3
        assert anime["episode_id"] == seeded["ep3"]
        assert anime["file_path"] == seeded["anime_file"], \
            "给的不是最近那一集的文件, 续播会从头放第 1 集"
        assert anime["position"] == 754
        assert anime["duration"] == 1440
        assert anime["progress_text"] == "看到 12:34 / 24:00"

    def test_movie_reads_media_progress(self, seeded):
        movie = StatsService().get_recently_watched(5)[1]
        assert movie["episode_number"] is None and movie["episode_id"] is None
        assert movie["file_path"] == seeded["movie_file"]
        assert movie["progress_text"] == "看到 50:00 / 1:40:00"

    def test_media_without_files_is_still_listed(self, seeded):
        ghost = StatsService().get_recently_watched(5)[2]
        assert ghost["title"] == "幽灵条目"
        assert ghost["file_path"] is None, "没文件就不该编一个路径出来"

    def test_raw_progress_string_is_gone(self, seeded):
        """原先甩给 UI 的是 '1440/1440s' 这种裸串"""
        for item in StatsService().get_recently_watched(5):
            assert "progress" not in item
            assert "progress_text" in item

    def test_limit_respected(self, seeded):
        assert len(StatsService().get_recently_watched(2)) == 2

    def test_empty_library(self, tmp_path):
        init_database(str(tmp_path / "empty.db"))
        assert StatsService().get_recently_watched(5) == []


# ==========================================================================
@pytest.fixture
def home(app, seeded):
    page = HomePage(stats_service=StatsService())
    page.setAttribute(Qt.WA_DontShowOnScreen, True)
    page.refresh()
    page.show()
    for _ in range(3):
        app.processEvents()
    return page


class TestHomePageRows:
    def test_rows_are_real_widgets_not_bare_layouts(self, home):
        rows = home.findChildren(RecentRow)
        assert len(rows) == 3, f"应渲染 3 行, 实得 {len(rows)}"

    def test_rows_are_clickable(self, home):
        for row in home.findChildren(RecentRow):
            assert row.cursor().shape() == Qt.PointingHandCursor, \
                "没有手型光标, 用户看不出这行能点"
            assert row._resume_btn.text() in ("▶ 继续观看", "▶ 播放", "文件缺失")

    def test_anime_row_says_which_episode(self, home):
        rows = home.findChildren(RecentRow)
        labels = [l.text() for l in rows[0].findChildren(QLabel)]
        assert "第3集" in labels, f"动漫行没写第几集: {labels}"
        assert "看到 12:34 / 24:00" in labels

    def test_movie_row_has_no_episode_label(self, home):
        labels = [l.text() for l in home.findChildren(RecentRow)[1].findChildren(QLabel)]
        assert not any(t.startswith("第") and t.endswith("集") for t in labels)

    def test_missing_file_disables_resume(self, home):
        ghost = home.findChildren(RecentRow)[2]
        assert ghost._can_resume is False
        assert ghost._resume_btn.isEnabled() is False
        assert ghost._resume_btn.text() == "文件缺失"

    def test_clicking_row_emits_resume(self, home, app, seeded):
        got = []
        home.resume_requested.connect(got.append)
        QTest.mouseClick(home.findChildren(RecentRow)[0], Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        assert len(got) == 1, f"整行点击应触发一次续播, 实得 {len(got)}"
        assert got[0]["file_path"] == seeded["anime_file"]
        assert got[0]["episode_id"] == seeded["ep3"]
        assert got[0]["position"] == 754

    def test_clicking_resume_button_emits_once(self, home, app):
        got = []
        home.resume_requested.connect(got.append)
        QTest.mouseClick(home.findChildren(RecentRow)[0]._resume_btn, Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        assert len(got) == 1, "按钮点击不该连带触发整行的 mousePressEvent"

    def test_clicking_detail_emits_media_id(self, home, app, seeded):
        got = []
        home.detail_requested.connect(got.append)
        row = home.findChildren(RecentRow)[0]
        detail_btn = [b for b in row.findChildren(QPushButton) if b.text() == "详情"][0]
        QTest.mouseClick(detail_btn, Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        assert got == [seeded["anime"]]

    def test_clicking_dead_row_emits_nothing(self, home, app):
        got = []
        home.resume_requested.connect(got.append)
        QTest.mouseClick(home.findChildren(RecentRow)[2], Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        assert got == [], "文件都不存在了还发续播信号, 只会换来一个报错弹窗"

    def test_placeholder_survives_repeated_refresh(self, app, tmp_path):
        """
        空库时占位符要能反复塞回去。原先清空逻辑无差别 deleteLater, 把构造函数里
        建好的那个占位符也销毁了 —— 空库刷新两次就 RuntimeError。
        """
        init_database(str(tmp_path / "empty.db"))
        page = HomePage(stats_service=StatsService())
        page.setAttribute(Qt.WA_DontShowOnScreen, True)
        for _ in range(3):
            page.refresh()
            app.processEvents()          # deleteLater 要泵事件才真的销毁
        assert page._recent_placeholder.parent() is not None, "占位符被销毁了"
        assert page._recent_layout.count() == 1
        assert page.findChildren(RecentRow) == []


# ==========================================================================
@pytest.fixture
def win(app, tmp_path, monkeypatch):
    """真 MainWindow + 同一个临时库; 播放器和模态框都被换成记录器"""
    seeded = _seed(tmp_path)

    cfg = load_config()                      # 绝不传 {} (HANDOFF §4.1)
    cfg["system"]["db_path"] = str(tmp_path / "cinema.db")

    # 播放失败会走 QMessageBox.warning(modal) → 测试里必须顶掉, 否则永久阻塞
    dialogs = []
    monkeypatch.setattr(mw_mod, "QMessageBox",
                        type("NoModal", (), {
                            "warning": staticmethod(
                                lambda p, t, x, *a, **k: dialogs.append((t, x)) or 0),
                        }))

    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)

    calls = []
    monkeypatch.setattr(w.player_service, "play",
                        lambda **kw: calls.append(kw))
    w.show()
    for _ in range(3):
        app.processEvents()
    w._calls, w._dialogs = calls, dialogs
    # ⚠️ 种子数据挂在 window 上, 测试**不要**再另外要 `seeded` fixture:
    #    win 和 seeded 都请求 tmp_path, 同一个测试里 pytest 给的是同一个目录,
    #    于是 _seed 跑两遍 → media_files.file_path UNIQUE 冲突。
    w._seeded = seeded
    return w


class TestMainWindowSeam:
    def test_row_click_reaches_player_service(self, win, app):
        seeded = win._seeded
        rows = win._home_page.findChildren(RecentRow)
        assert rows, "首页没渲染出最近观看行"
        QTest.mouseClick(rows[0], Qt.LeftButton)
        for _ in range(3):
            app.processEvents()

        assert len(win._calls) == 1, f"应恰好调用一次 play, 实得 {win._calls}"
        call = win._calls[0]
        assert call["file_path"] == seeded["anime_file"]
        assert call["media_id"] == seeded["anime"]
        # episode_id 必须传: 不传的话 _save_progress 会把动漫进度写到 media 表,
        # 看完也永远不进"已观看"统计
        assert call["episode_id"] == seeded["ep3"]
        assert call["start_pos"] == 754, "没从上次的进度续播"
        assert win._dialogs == []

    def test_movie_row_resumes_without_episode(self, win, app):
        QTest.mouseClick(win._home_page.findChildren(RecentRow)[1], Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        call = win._calls[0]
        assert call["episode_id"] is None
        assert call["start_pos"] == 3000

    def test_deleted_file_warns_instead_of_playing(self, win, app):
        """文件被移走之后必须明确告知, 不能静默无反应"""
        import os
        os.remove(win._seeded["movie_file"])
        QTest.mouseClick(win._home_page.findChildren(RecentRow)[1], Qt.LeftButton)
        for _ in range(3):
            app.processEvents()
        assert win._calls == [], "文件都没了还去调 play"
        assert win._dialogs and "无法续播" in win._dialogs[0][0]

    def test_detail_click_opens_detail_page(self, win, app):
        before = win.pages.count()
        win._home_page.detail_requested.emit(win._seeded["anime"])
        for _ in range(3):
            app.processEvents()
        assert win.pages.count() == before + 1
        assert isinstance(win.pages.currentWidget(), DetailPage)

    def test_open_detail_does_not_leak_pages(self, win, app):
        """
        原先 LibraryPage 每点一张卡片就 addWidget 一个新 DetailPage, 旧的既不隐藏
        也不销毁, 点几十次攒几十个孤儿页。收拢到 _open_detail 后必须只有一个。
        """
        before = win.pages.count()
        for _ in range(5):
            win._open_detail(win._seeded["anime"])
        for _ in range(3):
            app.processEvents()
        assert win.pages.count() == before + 1, \
            f"连开 5 次详情页应只留 1 个, 实得 {win.pages.count() - before}"

    def test_library_card_also_routes_through_open_detail(self, win, app):
        from app.ui.pages.library_page import LibraryPage
        lib = [p for p in win.pages.children() if isinstance(p, LibraryPage)][0]
        before = win.pages.count()
        lib._on_card_clicked(win._seeded["movie"])
        for _ in range(3):
            app.processEvents()
        assert win.pages.count() == before + 1
        assert isinstance(win.pages.currentWidget(), DetailPage)
