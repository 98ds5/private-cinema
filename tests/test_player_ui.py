"""
播放服务引擎切换 + 「正在播放」状态条 测试

覆盖:
  1. PlayerService.set_engine 运行时热切换 (原先只写配置, 必须重启才生效)
  2. PlayerBar 显隐与进度格式化
  3. 进度守卫用 isHidden() 而非 isVisible() —— 后者依赖祖先可见性,
     窗口未 show / 最小化时会让进度更新被静默丢弃
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
        """
        守卫必须用 isHidden() 而非 isVisible():
        父窗口未 show 时 isVisible() 为 False, 若用它做守卫,
        进度更新会被静默丢弃。
        """
        parent = PlayerBar()                     # 充当未显示的祖先容器
        parent.setAttribute(Qt.WA_DontShowOnScreen, True)
        child = PlayerBar(parent)
        child.show_playing("Y")
        assert child.isVisible() is False, "祖先未显示, 有效可见性应为 False"
        child.update_position(120, 8000)
        assert child._time.text() == "02:00 / 2:13:20", \
            "isVisible() 守卫会导致进度被丢弃"
