"""
媒体库维护回归锁: 清空媒体库「先备份再删」、只删记录与海报缓存(绝不动视频文件)、
保留媒体库目录配置; 恢复能把数据库与海报一起还原。UI 层走真实按钮 + 替换确认框。

⚠️ 测试绝不碰真 `data/posters/`: 服务层显式传临时海报目录, UI 层把
`maintenance.default_posters_dir` 打桩到临时目录 (脚本里还会核对真目录没被动)。
"""
import pytest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.database import get_session, init_database
from app.models.tables import Episode, Library, Media, MediaFile, Season
from app.services import maintenance
from app.services import posters as posters_mod
from app.services.maintenance import (
    backup_now, clear_library, list_backups, restore_latest, snapshot,
)
from app.ui.main_window import MainWindow
from app.utils.config_utils import load_config


@pytest.fixture
def env(tmp_path):
    """独立数据库 + 独立海报目录"""
    db = tmp_path / "data" / "cinema.db"
    pdir = tmp_path / "data" / "posters"
    pdir.mkdir(parents=True)

    init_database(str(db))
    with get_session() as s:
        s.add(Library(path=str(tmp_path / "movies"), name="movies",
                      scan_enabled=True))
        m1 = Media(title="星际穿越", media_type="movie", year=2014)
        m2 = Media(title="Cyberpunk Edgerunners", media_type="anime", year=2022)
        s.add_all([m1, m2])
        s.flush()
        s.add_all([
            MediaFile(media_id=m1.id, file_path=str(tmp_path / "m" / "a.mkv"),
                      file_name="a.mkv", file_size=1),
            MediaFile(media_id=m1.id, file_path=str(tmp_path / "m" / "b.mkv"),
                      file_name="b.mkv", file_size=2),
            MediaFile(media_id=m2.id, file_path=str(tmp_path / "m" / "c.mkv"),
                      file_name="c.mkv", file_size=3),
        ])
        season = Season(media_id=m2.id, season_number=1, title="第1季")
        s.add(season)
        s.flush()
        s.add(Episode(season_id=season.id, episode_number=1, title="EP1"))
        s.commit()

    (pdir / "1.jpg").write_bytes(b"poster-1")
    (pdir / "2.jpg").write_bytes(b"poster-2")
    return {"db": db, "posters": pdir, "tmp": tmp_path}


def _counts():
    """(media, files, seasons, episodes, libraries)"""
    with get_session() as s:
        return (s.query(Media).count(), s.query(MediaFile).count(),
                s.query(Season).count(), s.query(Episode).count(),
                s.query(Library).count())


class TestServiceLayer:

    def test_snapshot_reports_scale(self, env):
        assert snapshot(db=str(env["db"]), posters=env["posters"]) == {
            "media": 2, "files": 3, "episodes": 1, "libraries": 1, "posters": 2,
        }

    def test_clear_backs_up_before_removing(self, env):
        res = clear_library(db=str(env["db"]), posters=env["posters"])

        assert _counts() == (0, 0, 0, 0, 1), "作品/文件/季集要清掉, 媒体库目录要保留"
        assert list(env["posters"].glob("*.jpg")) == [], "海报缓存要清掉"

        backup = Path(res["backup"])
        assert (backup / "cinema.db").is_file(), "备份里必须有数据库"
        assert len(list((backup / "posters").glob("*.jpg"))) == 2, \
            "备份里必须连海报一起存, 否则恢复后卡片全变占位"

    def test_clear_never_touches_video_files(self, env):
        video = env["tmp"] / "m" / "a.mkv"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"raw-video")

        clear_library(db=str(env["db"]), posters=env["posters"])

        assert video.read_bytes() == b"raw-video", "硬盘上的视频文件绝不能被删"

    def test_restore_brings_db_and_posters_back(self, env):
        clear_library(db=str(env["db"]), posters=env["posters"])
        assert _counts() == (0, 0, 0, 0, 1)

        res = restore_latest(db=str(env["db"]), posters=env["posters"])

        assert res["media"] == 2 and res["files"] == 3
        assert _counts() == (2, 3, 1, 1, 1)
        assert sorted(p.name for p in env["posters"].glob("*.jpg")) == \
            ["1.jpg", "2.jpg"]

    def test_restore_without_backup_raises(self, env):
        with pytest.raises(RuntimeError):
            restore_latest(db=str(env["db"]), posters=env["posters"])

    def test_list_backups_newest_first(self, env):
        backup_now(db=str(env["db"]), posters=env["posters"],
                   stamp="20260101-000000")
        backup_now(db=str(env["db"]), posters=env["posters"],
                   stamp="20260202-000000")
        assert [p.name for p in list_backups(db=str(env["db"]))] == \
            ["20260202-000000", "20260101-000000"]

    def test_clearing_twice_is_safe(self, env):
        clear_library(db=str(env["db"]), posters=env["posters"])
        res = clear_library(db=str(env["db"]), posters=env["posters"])
        assert res["media"] == 0
        assert _counts()[4] == 1, "第二次清空也不能把媒体库目录删掉"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def win(app, env, monkeypatch):
    """真 MainWindow + 临时库; 海报目录打桩到临时目录"""
    monkeypatch.setattr(maintenance, "default_posters_dir",
                        lambda: env["posters"])
    cfg = load_config()
    cfg["system"]["db_path"] = str(env["db"])
    init_database(str(env["db"]))

    w = MainWindow(cfg, ffprobe_path=None, mpv_path=None)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    for _ in range(4):
        app.processEvents()
    yield w
    w.close()
    w.deleteLater()


class TestSettingsUi:

    def test_buttons_and_counts_are_wired(self, win):
        page = win._settings_page
        assert page._clear_btn.text() == "清空媒体库"
        assert page._restore_btn.text() == "恢复最近备份"
        assert "2 部作品" in page._maint_status.text(), page._maint_status.text()

    def test_cancel_keeps_everything(self, win):
        page = win._settings_page
        page._confirm = lambda *a, **kw: False        # 用户点「取消」

        page._clear_btn.click()

        assert _counts()[0] == 2, "取消后一部都不许少"
        assert "已取消" in page._maint_status.text()
        assert list_backups(db=str(page.config["system"]["db_path"])) == [], \
            "取消时不该产生备份"

    def test_confirmed_clear_then_restore_round_trip(self, win):
        page = win._settings_page
        win._nav.setCurrentRow(5)                     # 站到「设置」页
        page._confirm = lambda *a, **kw: True

        page._clear_btn.click()

        assert _counts() == (0, 0, 0, 0, 1), "确认后应清空记录、保留媒体库目录"
        assert "已清空" in page._maint_status.text()
        assert len(list_backups(db=str(page.config["system"]["db_path"]))) == 1
        # 清空后主窗口要回首页 (详情页可能已经在看一部被清掉的作品)
        assert win._nav.currentRow() == 0

        page._restore_btn.click()

        assert _counts() == (2, 3, 1, 1, 1), "恢复后数据要回来"
        assert "已从备份恢复" in page._maint_status.text()

    def test_real_posters_dir_untouched(self, env):
        real = posters_mod.posters_dir()
        assert Path(real) != Path(env["posters"])
