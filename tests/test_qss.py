"""QSS 对账回归锁: objectName 全覆盖、标题栏三件套、无 Qt 不认识的属性。

对账必须扫 get_qss() 的最终产物 (含 _extra_qss 拼接), 只扫主题常量会误报。
"""
import re
from pathlib import Path

import pytest
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from app.ui.styles import get_qss

APP_DIR = Path(__file__).resolve().parent.parent / "app"
COMBOS = [("echo", "dark"), ("echo", "light"), ("prism", "dark"), ("prism", "light")]


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _used_objectnames() -> dict:
    """扫全项目 setObjectName("X"), 返回 {objectName: [文件名, ...]}"""
    used = {}
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(r'setObjectName\(\s*["\']([^"\']+)["\']', text):
            used.setdefault(m.group(1), set()).add(py.name)
    return used


@pytest.mark.parametrize("version,theme", COMBOS)
def test_every_objectname_is_styled(version, theme):
    """每个 setObjectName 都必须在当前这套 QSS 里有选择器, 否则控件走 Qt 默认外观"""
    qss = get_qss(version, theme)          # 必须是最终产物, 含 _extra_qss
    styled = set(re.findall(r'#([A-Za-z_]\w*)', qss))
    missing = {on: sorted(f) for on, f in _used_objectnames().items()
               if on not in styled}
    assert not missing, f"{version}/{theme} 缺样式的 objectName: {missing}"


@pytest.mark.parametrize("version,theme", COMBOS)
def test_titlebar_is_styled_in_every_theme(version, theme):
    """无边框窗口的标题栏三件套在每个主题里都必须有样式"""
    qss = get_qss(version, theme)
    for name in ("titleBar", "titleLabel", "trafficLight"):
        assert f"#{name}" in qss, f"{version}/{theme} 的 QSS 里没有 #{name}"


def test_no_unknown_qss_properties(app):
    """Qt 不认识的 QSS 属性只会刷警告不会报错, 但说明样式没生效, 必须锁住"""
    caught = []

    def handler(mode, ctx, msg):
        caught.append(msg)

    prev = qInstallMessageHandler(handler)
    try:
        for version, theme in COMBOS:
            app.setStyleSheet(get_qss(version, theme))
            app.processEvents()
        app.setStyleSheet("")
        app.processEvents()
    finally:
        qInstallMessageHandler(prev)

    bad = [m for m in caught
           if "Unknown property" in m or "Could not parse" in m]
    assert not bad, f"QSS 里有 Qt 不认识的写法: {bad[:5]}"
