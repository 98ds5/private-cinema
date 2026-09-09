"""
QSS 对账回归锁。

两个真实踩过的坑:

1. 2026-09-09 用户手点发现 Echo 版标题栏是 Qt 默认外观。静态对账确认:
   `titleBar` / `titleLabel` / `trafficLight` 在 PRISM_DARK/PRISM_LIGHT 里有,
   在 ECHO_DARK/ECHO_LIGHT 里**完全没有** —— 两个版本的标题栏长得不一样不是设计,
   是漏写。

2. 我自己做这次对账时先误报了一轮: 只扫了四个主题常量, 报出 12 个 objectName
   "完全没样式"。实际 styles.py 还有第 5 个来源 `_extra_qss()`, 由 `get_qss()`
   拼接, 那 12 个全在里面。**对账必须扫 `get_qss()` 的最终产物**, 不能扫常量。

顺带锁住 HANDOFF §4.16: QSS 不支持的属性 (box-shadow / filter / letter-spacing)
会让 Qt 刷 "Unknown property" 警告 —— 外部建议里就出现过 `letter-spacing: 2px`。
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
    """
    每个 setObjectName 都必须在**当前这套** QSS 里有对应选择器,
    否则那个控件走 Qt 默认外观, 在深色主题里会是一块突兀的系统灰。
    """
    qss = get_qss(version, theme)          # 必须是最终产物, 含 _extra_qss
    styled = set(re.findall(r'#([A-Za-z_]\w*)', qss))
    missing = {on: sorted(f) for on, f in _used_objectnames().items()
               if on not in styled}
    assert not missing, f"{version}/{theme} 缺样式的 objectName: {missing}"


@pytest.mark.parametrize("version,theme", COMBOS)
def test_titlebar_is_styled_in_every_theme(version, theme):
    """专门锁 2026-09-09 那个漏写: 无边框窗口的标题栏三件套"""
    qss = get_qss(version, theme)
    for name in ("titleBar", "titleLabel", "trafficLight"):
        assert f"#{name}" in qss, f"{version}/{theme} 的 QSS 里没有 #{name}"


def test_no_unknown_qss_properties(app):
    """
    Qt 解析 QSS 时会把不认识的属性打到 stderr ("Unknown property xxx")。
    这类警告不会让测试失败, 但会刷屏并且说明样式没生效 —— 必须锁住。
    """
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
