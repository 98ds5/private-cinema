"""播放服务引擎热切换 + 「正在播放」状态条的行为锁。

覆盖 set_engine 的切换/拒绝/去重语义、PlayerBar 显隐与进度格式化,
以及进度守卫必须用 isHidden() 而非 isVisible() (后者依赖祖先可见性)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.services.player import (
    PlayerService, _MpvEngine, _VividPlayerEngine, _vividplayer_url,
)
from app.ui.pages import settings_page
from app.ui.pages.settings_page import SettingsPage
from app.ui.widgets.player_bar import PlayerBar, fmt_time

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def service():
    return PlayerService({"player": {"engine": "mpv"}}, mpv_path=None)


class TestSetEngine:

    def test_initial_engine_from_config(self, service):
        assert service.engine_type == "mpv"
        assert isinstance(service._engine, _MpvEngine)

    def test_switch_to_vividplayer(self, service):
        assert service.set_engine("vividplayer") is True
        assert service.engine_type == "vividplayer"
        assert isinstance(service._engine, _VividPlayerEngine)

    def test_switch_back_to_mpv(self, service):
        service.set_engine("vividplayer")
        assert service.set_engine("mpv") is True
        assert isinstance(service._engine, _MpvEngine)

    def test_unknown_engine_rejected_and_keeps_current(self, service):
        before = service._engine
        assert service.set_engine("vlc") is False
        assert service.engine_type == "mpv"
        assert service._engine is before, "非法引擎不应重建引擎对象"

    def test_same_engine_does_not_rebuild(self, service):
        before = service._engine
        assert service.set_engine("mpv") is True
        assert service._engine is before, "同一引擎重复设置不应重建"

    def test_config_dict_is_updated(self, service):
        cfg = {"player": {"engine": "mpv"}}
        svc = PlayerService(cfg, mpv_path=None)
        svc.set_engine("vividplayer")
        assert cfg["player"]["engine"] == "vividplayer"


class TestVividPlayerUrl:

    def test_protocol_form(self):
        url = _vividplayer_url(r"D:\Movies\A.mkv")
        assert url.startswith("vividplayer://openfile?path="), \
            "协议形式必须是双斜杠 + openfile?path= (实测单冒号/裸路径不可用)"

    def test_path_fully_encoded(self):
        url = _vividplayer_url(r"D:\<媒体库>\My File.mkv")
        payload = url.split("path=", 1)[1]
        assert "D%3A" in payload, "盘符冒号必须编码"
        assert "%5C" in payload, "反斜杠必须编码"
        assert "%20" in payload, "空格必须编码"
        assert " " not in payload, "不能留裸空格"
        assert "影视资源" not in payload, "中文必须百分号编码"


class TestFmtTime:

    @pytest.mark.parametrize("secs,expect", [
        (0, "00:00"), (9, "00:09"), (65, "01:05"), (3599, "59:59"),
        (3600, "1:00:00"), (3725, "1:02:05"), (-5, "00:00"), (None, "00:00"),
    ])
    def test_formatting(self, secs, expect):
        assert fmt_time(secs) == expect


class TestPlayerBar:

    @pytest.fixture
    def bar(self):
        b = PlayerBar()
        b.setAttribute(Qt.WA_DontShowOnScreen, True)
        return b

    def test_hidden_by_default(self, bar):
        assert bar.isHidden() is True, "无播放时状态条必须隐藏"

    def test_show_playing_sets_title(self, bar):
        bar.show_playing("Pacific Rim")
        assert bar.isHidden() is False
        assert bar._title.text() == "Pacific Rim"

    def test_show_playing_handles_empty_title(self, bar):
        bar.show_playing("")
        assert bar._title.text() == "未知标题"

    def test_update_position_formats(self, bar):
        bar.show_playing("X")
        bar.update_position(754, 7876)
        assert bar._time.text() == "12:34 / 2:11:16"

    def test_update_position_without_duration(self, bar):
        bar.show_playing("X")
        bar.update_position(65, 0)
        assert bar._time.text() == "01:05"

    def test_update_skipped_while_hidden(self, bar):
        """clear() 之后不应再刷新时间"""
        bar.show_playing("X")
        bar.clear()
        bar.update_position(500, 8000)
        assert bar._time.text() == ""

    def test_clear_hides_and_resets(self, bar):
        bar.show_playing("X")
        bar.update_position(10, 100)
        bar.clear()
        assert bar.isHidden() is True
        assert bar._title.text() == ""
        assert bar._time.text() == ""

    def test_updates_work_without_ancestor_shown(self, bar):
        """守卫用 isHidden() 而非 isVisible(): 父窗口未 show 时后者会让进度被静默丢弃"""
        parent = PlayerBar()                     # 充当未显示的祖先容器
        parent.setAttribute(Qt.WA_DontShowOnScreen, True)
        child = PlayerBar(parent)
        child.show_playing("Y")
        assert child.isVisible() is False, "祖先未显示, 有效可见性应为 False"
        child.update_position(120, 8000)
        assert child._time.text() == "02:00 / 2:13:20", \
            "isVisible() 守卫会导致进度被丢弃"


class TestEngineSwitchRealSeam:
    """必须复刻设置页真实顺序: 先改共享 config 再调 set_engine, 直接调会绕过 bug 前提"""

    @staticmethod
    def _cfg(engine):
        return {
            "player": {"engine": engine, "progress_interval": 10,
                       "hw_decode": "auto"},
            "libraries": [],
            "system": {"theme": "light", "db_path": "./data/cinema.db"},
            "ffmpeg": {"ffprobe_path": "auto"},
            "ui": {"version": "echo"},
        }

    @pytest.mark.parametrize("start,target", [
        ("mpv", "vividplayer"), ("vividplayer", "mpv"),
    ])
    def test_pre_mutated_config_still_rebuilds(self, start, target):
        """设置页的真实顺序: config 先被改成目标值, 引擎仍必须重建"""
        cfg = self._cfg(start)
        svc = PlayerService(cfg, mpv_path=None)
        assert svc.engine_type == start

        cfg.setdefault("player", {})["engine"] = target
        assert svc.set_engine(target) is True

        expected = _VividPlayerEngine if target == "vividplayer" else _MpvEngine
        assert isinstance(svc._engine, expected), \
            f"config 被提前改写时引擎仍须重建, 实际是 {type(svc._engine).__name__}"
        assert svc.engine_type == target

    def test_engine_type_does_not_follow_config(self):
        """engine_type 必须反映实际构建出的引擎, 而不是配置文件里的值"""
        cfg = self._cfg("mpv")
        svc = PlayerService(cfg, mpv_path=None)
        cfg["player"]["engine"] = "vividplayer"      # 只改配置, 不切换
        assert svc.engine_type == "mpv", \
            "engine_type 跟着 config 走会让 set_engine 的守卫永远命中"

    @pytest.mark.parametrize("start,target,idx", [
        ("mpv", "vividplayer", 1), ("vividplayer", "mpv", 0),
    ])
    def test_settings_page_combo_actually_switches(self, start, target, idx,
                                                   monkeypatch):
        """走真实 seam: SettingsPage 的下拉框 (并且不写真实 config.json)"""
        monkeypatch.setattr(settings_page, "save_config", lambda c: None)

        cfg = self._cfg(start)
        svc = PlayerService(cfg, mpv_path=None)
        page = SettingsPage(config=cfg, ffprobe_path=None, mpv_path=None,
                            player_service=svc)
        page.setAttribute(Qt.WA_DontShowOnScreen, True)

        page._engine_combo.setCurrentIndex(idx)
        _app.processEvents()

        expected = _VividPlayerEngine if target == "vividplayer" else _MpvEngine
        assert isinstance(svc._engine, expected), \
            f"下拉框切到 {target} 后引擎未重建, 实际 {type(svc._engine).__name__}"
        assert cfg["player"]["engine"] == target, "配置也应同步"
        assert "已切换" in page._scan_status.text(), \
            f"状态栏未给出反馈: {page._scan_status.text()!r}"
        page.deleteLater()

    def test_old_engine_is_not_accumulated(self):
        """反复切换不应把旧引擎堆积成 PlayerService 的子对象"""
        cfg = self._cfg("mpv")
        svc = PlayerService(cfg, mpv_path=None)
        before = len(svc.children())

        for _ in range(6):
            cfg["player"]["engine"] = "vividplayer"
            svc.set_engine("vividplayer")
            cfg["player"]["engine"] = "mpv"
            svc.set_engine("mpv")
        for _ in range(4):
            _app.processEvents()          # 让 deleteLater 真正执行

        assert len(svc.children()) <= before + 1, \
            f"旧引擎在累积: {before} -> {len(svc.children())}"
