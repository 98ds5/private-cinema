"""
双版本双主题 — 两个完全不同的设计语言

Version A: "Echo" — 编辑/建筑风格
  DESIGN_VARIANCE: 8, VISUAL_DENSITY: 4
  暖色调, 极简, 留白, 不对称, Deep Rose 强调

Version B: "Prism" — 专业工具风格
  VISUAL_DENSITY: 6
  冷色调, 玻璃质感, 系统化, Teal 强调
"""

# ========================================================================
# Version A: "Echo" — 编辑/建筑风格
# ========================================================================
ECHO_DARK = """
QMainWindow, QWidget#pageContent {
    background-color: #121212;
    color: #e8e4df;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QFrame#titleBar {
    background-color: #121212;
    border-bottom: 1px solid #1a1a1a;
    min-height: 38px; max-height: 38px;
}
QLabel#titleLabel {
    font-size: 11px; font-weight: 300;
    color: #6b6b6b; padding: 0;
}
QPushButton#trafficLight {
    border: none; border-radius: 5px;
    min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
    padding: 0; margin: 0 3px;
}
QFrame#headerBar {
    background-color: #121212;
    border: none;
    min-height: 56px; max-height: 56px;
}
QLineEdit#searchBox {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #2a2a2a;
    border-radius: 0;
    padding: 8px 4px;
    color: #e8e4df;
    font-size: 12px;
    min-width: 160px; max-height: 32px;
}
QLineEdit#searchBox:focus { border-bottom-color: #c94b4b; }
QLineEdit#searchBox::placeholder { color: #4a4a4a; }
QPushButton#themeToggle {
    background: none; border: none;
    color: #6b6b6b; font-size: 13px; padding: 4px 8px;
}
QPushButton#themeToggle:hover { color: #e8e4df; }
QListWidget#sidebar {
    background-color: #121212;
    border: none; border-right: 1px solid #1a1a1a;
    outline: none; padding: 24px 0;
    min-width: 120px; max-width: 120px;
    font-size: 11px; letter-spacing: 1px;
}
QListWidget#sidebar::item {
    padding: 10px 20px;
    color: #4a4a4a;
}
QListWidget#sidebar::item:selected {
    background: none;
    color: #e8e4df; font-weight: 500;
}
QListWidget#sidebar::item:hover:!selected {
    background: none; color: #8a8a8a;
}
QLabel#pageTitle {
    font-size: 36px; font-weight: 200;
    color: #e8e4df; letter-spacing: -1px;
    padding: 0; margin: 0;
}
QLabel#pageSubtitle {
    font-size: 11px; color: #4a4a4a;
    letter-spacing: 1px; padding: 0 0 20px 0;
}
QFrame#statCard {
    background: none;
    border: none; border-top: 1px solid #2a2a2a;
    border-radius: 0; padding: 16px 0;
}
QLabel#statNumber {
    font-size: 32px; font-weight: 200;
    color: #e8e4df;
}
QLabel#statLabel {
    font-size: 10px; color: #4a4a4a;
    letter-spacing: 1px; padding-top: 4px;
}
QLabel#statNumber.accent { color: #c94b4b; }
QLabel#statNumber.watched { color: #4a7c6f; }
QLabel#statNumber.watching { color: #c9a96e; }
QLabel#statNumber.unwatched { color: #4a4a4a; }
QFrame#mediaCard {
    background: none;
    border: 1px solid #2a2a2a; border-radius: 0;
}
QFrame#mediaCard:hover { border-color: #c94b4b; }
QLabel#cardTitle {
    font-size: 11px; font-weight: 400;
    color: #e8e4df; padding: 8px 10px 2px;
}
QLabel#cardSubtitle {
    font-size: 9px; color: #4a4a4a;
    padding: 0 10px 8px;
}
QFrame#settingGroup {
    background: none;
    border: none; border-top: 1px solid #2a2a2a;
    border-radius: 0; padding: 20px 0;
}
QLabel#settingLabel {
    font-size: 11px; font-weight: 500;
    color: #e8e4df; letter-spacing: 1px;
    padding-bottom: 8px;
}
QLabel#settingValue { font-size: 11px; color: #6b6b6b; }
QComboBox {
    background: none; border: 1px solid #2a2a2a;
    border-radius: 0; padding: 6px 12px;
    color: #e8e4df; min-height: 28px;
}
QComboBox:hover { border-color: #c94b4b; }
QComboBox QAbstractItemView {
    background-color: #1a1a1a; border: 1px solid #2a2a2a;
    color: #e8e4df;
    selection-background-color: #c94b4b;
    selection-color: #ffffff;
}
QScrollBar:vertical {
    background: none; width: 4px; border: none;
}
QScrollBar::handle:vertical {
    background: #2a2a2a; border-radius: 2px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #4a4a4a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

ECHO_LIGHT = """
QMainWindow, QWidget#pageContent {
    background-color: #f5f2ed;
    color: #1a1a1a;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QFrame#titleBar {
    background-color: #f5f2ed;
    border-bottom: 1px solid #d4d0c8;
    min-height: 38px; max-height: 38px;
}
QLabel#titleLabel {
    font-size: 11px; font-weight: 300;
    color: #8a8a8a; padding: 0;
}
QPushButton#trafficLight {
    border: none; border-radius: 5px;
    min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
    padding: 0; margin: 0 3px;
}
QFrame#headerBar {
    background-color: #f5f2ed; border: none;
    min-height: 56px; max-height: 56px;
}
QLineEdit#searchBox {
    background: none; border: none;
    border-bottom: 1px solid #d4d0c8;
    border-radius: 0; padding: 8px 4px;
    color: #1a1a1a; font-size: 12px;
    min-width: 160px; max-height: 32px;
}
QLineEdit#searchBox:focus { border-bottom-color: #c94b4b; }
QLineEdit#searchBox::placeholder { color: #b0aca4; }
QPushButton#themeToggle {
    background: none; border: none;
    color: #8a8a8a; font-size: 13px; padding: 4px 8px;
}
QPushButton#themeToggle:hover { color: #1a1a1a; }
QListWidget#sidebar {
    background-color: #f5f2ed;
    border: none; border-right: 1px solid #e8e4dc;
    outline: none; padding: 24px 0;
    min-width: 120px; max-width: 120px;
    font-size: 11px; letter-spacing: 1px;
}
QListWidget#sidebar::item {
    padding: 10px 20px; color: #b0aca4;
}
QListWidget#sidebar::item:selected {
    background: none; color: #1a1a1a; font-weight: 500;
}
QListWidget#sidebar::item:hover:!selected {
    background: none; color: #6b6b6b;
}
QLabel#pageTitle {
    font-size: 36px; font-weight: 200;
    color: #1a1a1a; letter-spacing: -1px; padding: 0;
}
QLabel#pageSubtitle {
    font-size: 11px; color: #b0aca4;
    letter-spacing: 1px; padding: 0 0 20px 0;
}
QFrame#statCard {
    background: none; border: none;
    border-top: 1px solid #e8e4dc;
    border-radius: 0; padding: 16px 0;
}
QLabel#statNumber {
    font-size: 32px; font-weight: 200; color: #1a1a1a;
}
QLabel#statLabel {
    font-size: 10px; color: #b0aca4;
    letter-spacing: 1px; padding-top: 4px;
}
QLabel#statNumber.accent { color: #c94b4b; }
QLabel#statNumber.watched { color: #4a7c6f; }
QLabel#statNumber.watching { color: #c9a96e; }
QLabel#statNumber.unwatched { color: #b0aca4; }
QFrame#mediaCard {
    background: none; border: 1px solid #e8e4dc; border-radius: 0;
}
QFrame#mediaCard:hover { border-color: #c94b4b; }
QLabel#cardTitle {
    font-size: 11px; font-weight: 400;
    color: #1a1a1a; padding: 8px 10px 2px;
}
QLabel#cardSubtitle {
    font-size: 9px; color: #b0aca4; padding: 0 10px 8px;
}
QFrame#settingGroup {
    background: none; border: none;
    border-top: 1px solid #e8e4dc;
    border-radius: 0; padding: 20px 0;
}
QLabel#settingLabel {
    font-size: 11px; font-weight: 500;
    color: #1a1a1a; letter-spacing: 1px; padding-bottom: 8px;
}
QLabel#settingValue { font-size: 11px; color: #8a8a8a; }
QComboBox {
    background: none; border: 1px solid #e8e4dc;
    border-radius: 0; padding: 6px 12px;
    color: #1a1a1a; min-height: 28px;
}
QComboBox:hover { border-color: #c94b4b; }
QComboBox QAbstractItemView {
    background-color: #ffffff; border: 1px solid #e8e4dc;
    color: #1a1a1a;
    selection-background-color: #c94b4b;
    selection-color: #ffffff;
}
QScrollBar:vertical {
    background: none; width: 4px; border: none;
}
QScrollBar::handle:vertical {
    background: #d4d0c8; border-radius: 2px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b0aca4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

# ========================================================================
# Version B: "Prism" — 专业工具风格
# ========================================================================
PRISM_DARK = """
QMainWindow, QWidget#pageContent {
    background-color: #0f1117;
    color: #e8eaed;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QFrame#titleBar {
    background-color: #0f1117;
    border-bottom: 1px solid rgba(78, 205, 196, 0.15);
    min-height: 38px; max-height: 38px;
}
QLabel#titleLabel {
    font-size: 11px; font-weight: 500;
    color: #7a7f8a; padding: 0;
}
QPushButton#trafficLight {
    border: none; border-radius: 5px;
    min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
    padding: 0; margin: 0 3px;
}
QFrame#headerBar {
    background-color: #0f1117;
    border-bottom: 1px solid rgba(78, 205, 196, 0.1);
    min-height: 48px; max-height: 48px;
}
QLineEdit#searchBox {
    background-color: rgba(26, 29, 40, 0.8);
    border: 1px solid #2a2d3a;
    border-radius: 8px; padding: 6px 14px;
    color: #e8eaed; font-size: 12px;
    min-width: 200px; max-height: 30px;
}
QLineEdit#searchBox:focus { border-color: #4ecdc4; }
QLineEdit#searchBox::placeholder { color: #5a5f6a; }
QPushButton#themeToggle {
    background: rgba(26, 29, 40, 0.6);
    border: 1px solid #2a2d3a; border-radius: 6px;
    color: #7a7f8a; font-size: 13px; padding: 4px 10px;
    min-height: 26px;
}
QPushButton#themeToggle:hover {
    background: #1a1d28; color: #e8eaed;
}
QListWidget#sidebar {
    background-color: rgba(15, 17, 23, 0.9);
    border: none; border-right: 1px solid #1a1d28;
    outline: none; padding: 16px 0;
    min-width: 150px; max-width: 150px;
    font-size: 12px;
}
QListWidget#sidebar::item {
    padding: 10px 20px;
    color: #5a5f6a;
}
QListWidget#sidebar::item:selected {
    background: rgba(78, 205, 196, 0.1);
    border-left: 2px solid #4ecdc4;
    color: #4ecdc4; font-weight: 500;
}
QListWidget#sidebar::item:hover:!selected {
    background: rgba(26, 29, 40, 0.5);
    color: #9a9fa8;
}
QLabel#pageTitle {
    font-size: 20px; font-weight: 600;
    color: #e8eaed; padding: 0;
}
QLabel#pageSubtitle {
    font-size: 11px; color: #5a5f6a;
    padding: 0 0 16px 0;
}
QFrame#statCard {
    background: rgba(26, 29, 40, 0.6);
    border: 1px solid #2a2d3a;
    border-radius: 12px; padding: 20px;
}
QLabel#statNumber {
    font-size: 28px; font-weight: 700;
    color: #e8eaed;
}
QLabel#statLabel {
    font-size: 10px; color: #5a5f6a;
    padding-top: 4px;
}
QLabel#statNumber.accent { color: #4ecdc4; }
QLabel#statNumber.watched { color: #4ecdc4; }
QLabel#statNumber.watching { color: #ffd93d; }
QLabel#statNumber.unwatched { color: #5a5f6a; }
QFrame#mediaCard {
    background: rgba(26, 29, 40, 0.6);
    border: 1px solid #2a2d3a;
    border-radius: 12px;
}
QFrame#mediaCard:hover {
    border-color: #4ecdc4;
    background: rgba(26, 29, 40, 0.9);
}
QLabel#cardTitle {
    font-size: 12px; font-weight: 500;
    color: #e8eaed; padding: 10px 12px 2px;
}
QLabel#cardSubtitle {
    font-size: 10px; color: #5a5f6a;
    padding: 0 12px 10px;
}
QFrame#settingGroup {
    background: rgba(26, 29, 40, 0.6);
    border: 1px solid #2a2d3a;
    border-radius: 12px; padding: 20px;
}
QLabel#settingLabel {
    font-size: 12px; font-weight: 600;
    color: #e8eaed; padding-bottom: 8px;
}
QLabel#settingValue { font-size: 11px; color: #7a7f8a; }
QComboBox {
    background: rgba(26, 29, 40, 0.8);
    border: 1px solid #2a2d3a; border-radius: 6px;
    padding: 6px 12px; color: #e8eaed; min-height: 28px;
}
QComboBox:hover { border-color: #4ecdc4; }
QComboBox QAbstractItemView {
    background: #1a1d28; border: 1px solid #2a2d3a;
    color: #e8eaed;
    selection-background-color: #4ecdc4;
    selection-color: #0f1117;
}
QScrollBar:vertical {
    background: none; width: 6px; border: none;
}
QScrollBar::handle:vertical {
    background: #2a2d3a; border-radius: 3px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #3a3d4a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

PRISM_LIGHT = """
QMainWindow, QWidget#pageContent {
    background-color: #f0f2f5;
    color: #1a1d28;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QFrame#titleBar {
    background-color: #f0f2f5;
    border-bottom: 1px solid #e0e2e8;
    min-height: 38px; max-height: 38px;
}
QLabel#titleLabel {
    font-size: 11px; font-weight: 500;
    color: #7a7f8a; padding: 0;
}
QPushButton#trafficLight {
    border: none; border-radius: 5px;
    min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
    padding: 0; margin: 0 3px;
}
QFrame#headerBar {
    background-color: #f0f2f5;
    border-bottom: 1px solid #e0e2e8;
    min-height: 48px; max-height: 48px;
}
QLineEdit#searchBox {
    background-color: #ffffff;
    border: 1px solid #d0d2d8;
    border-radius: 8px; padding: 6px 14px;
    color: #1a1d28; font-size: 12px;
    min-width: 200px; max-height: 30px;
}
QLineEdit#searchBox:focus { border-color: #4ecdc4; }
QLineEdit#searchBox::placeholder { color: #9a9fa8; }
QPushButton#themeToggle {
    background: #ffffff; border: 1px solid #d0d2d8;
    border-radius: 6px; color: #7a7f8a;
    font-size: 13px; padding: 4px 10px; min-height: 26px;
}
QPushButton#themeToggle:hover {
    background: #e8eaed; color: #1a1d28;
}
QListWidget#sidebar {
    background-color: #ffffff;
    border: none; border-right: 1px solid #e0e2e8;
    outline: none; padding: 16px 0;
    min-width: 150px; max-width: 150px;
    font-size: 12px;
}
QListWidget#sidebar::item {
    padding: 10px 20px; color: #9a9fa8;
}
QListWidget#sidebar::item:selected {
    background: rgba(78, 205, 196, 0.1);
    border-left: 2px solid #4ecdc4;
    color: #1a1d28; font-weight: 500;
}
QListWidget#sidebar::item:hover:!selected {
    background: #f5f6f8; color: #5a5f6a;
}
QLabel#pageTitle {
    font-size: 20px; font-weight: 600;
    color: #1a1d28; padding: 0;
}
QLabel#pageSubtitle {
    font-size: 11px; color: #9a9fa8;
    padding: 0 0 16px 0;
}
QFrame#statCard {
    background: #ffffff;
    border: 1px solid #e0e2e8;
    border-radius: 12px; padding: 20px;
}
QLabel#statNumber {
    font-size: 28px; font-weight: 700; color: #1a1d28;
}
QLabel#statLabel {
    font-size: 10px; color: #9a9fa8; padding-top: 4px;
}
QLabel#statNumber.accent { color: #4ecdc4; }
QLabel#statNumber.watched { color: #4ecdc4; }
QLabel#statNumber.watching { color: #ff9f43; }
QLabel#statNumber.unwatched { color: #9a9fa8; }
QFrame#mediaCard {
    background: #ffffff; border: 1px solid #e0e2e8;
    border-radius: 12px;
}
QFrame#mediaCard:hover {
    border-color: #4ecdc4;
}
QLabel#cardTitle {
    font-size: 12px; font-weight: 500;
    color: #1a1d28; padding: 10px 12px 2px;
}
QLabel#cardSubtitle {
    font-size: 10px; color: #9a9fa8;
    padding: 0 12px 10px;
}
QFrame#settingGroup {
    background: #ffffff; border: 1px solid #e0e2e8;
    border-radius: 12px; padding: 20px;
}
QLabel#settingLabel {
    font-size: 12px; font-weight: 600;
    color: #1a1d28; padding-bottom: 8px;
}
QLabel#settingValue { font-size: 11px; color: #7a7f8a; }
QComboBox {
    background: #ffffff; border: 1px solid #d0d2d8;
    border-radius: 6px; padding: 6px 12px;
    color: #1a1d28; min-height: 28px;
}
QComboBox:hover { border-color: #4ecdc4; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #d0d2d8;
    color: #1a1d28;
    selection-background-color: #4ecdc4;
    selection-color: #ffffff;
}
QScrollBar:vertical {
    background: none; width: 6px; border: none;
}
QScrollBar::handle:vertical {
    background: #d0d2d8; border-radius: 3px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b0b2b8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ========================================================================
# 版本 + 主题管理器
# ========================================================================
VERSIONS = {
    "echo": {
        "name": "Echo (编辑风格)",
        "dark": ECHO_DARK,
        "light": ECHO_LIGHT,
    },
    "prism": {
        "name": "Prism (专业风格)",
        "dark": PRISM_DARK,
        "light": PRISM_LIGHT,
    },
}


def load_version(config: dict = None) -> str:
    """读取设计版本; 缺省为 prism"""
    if config:
        return config.get("ui", {}).get("version", "prism")
    return "prism"


def load_theme(config: dict = None) -> str:
    if config:
        return config.get("system", {}).get("theme", "dark")
    return "dark"


# ========================================================================
# 追加控件的设计 token
#
# 为什么不直接写进上面 4 份 QSS: 同一控件要在 echo/prism × dark/light 四种
# 组合里各写一遍, 4 处重复且容易漂移。这里集中一份 token 表, 由 get_qss 拼接。
# 元组含义: (强调色, 强调色上的文字, 强调色 hover, 表面色, 弱化文字色)
# ========================================================================
ACCENTS = {
    "echo": {
        "dark":  ("#c94b4b", "#ffffff", "#a83c3c", "#1e1e1e", "#8a8a8a"),
        "light": ("#c94b4b", "#ffffff", "#a83c3c", "#e8e4df", "#6b6b6b"),
    },
    "prism": {
        "dark":  ("#4ecdc4", "#0f1117", "#6ddbd4", "#1a1d28", "#7a7f8a"),
        "light": ("#4ecdc4", "#0f1117", "#3dbdb5", "#e4e7ec", "#7a7f8a"),
    },
}


def _extra_qss(version: str, theme: str) -> str:
    """生成详情页等新增控件的 QSS 片段 (跟随当前版本+主题取色)"""
    v = ACCENTS.get(version) or ACCENTS["echo"]
    accent, on_accent, hover, surface, muted = v.get(theme) or v["dark"]

    return f"""
QPushButton#primaryAction {{
    background-color: {accent}; color: {on_accent}; font-weight: 600;
    border: none; border-radius: 6px; padding: 10px 28px; font-size: 13px;
}}
QPushButton#primaryAction:hover {{ background-color: {hover}; }}
QPushButton#primaryAction:disabled {{
    background-color: {surface}; color: {muted};
}}

QPushButton#ghostAction {{
    background-color: transparent; color: {muted};
    border: 1px solid {muted}; border-radius: 6px;
    padding: 10px 28px; font-size: 13px;
}}
QPushButton#ghostAction:hover {{ color: {accent}; border-color: {accent}; }}

QPushButton#rowAction {{
    background-color: transparent; color: {muted};
    border: 1px solid {muted}; border-radius: 4px;
    font-size: 11px; padding: 2px 8px;
}}
QPushButton#rowAction:hover {{
    color: {on_accent}; background-color: {accent}; border-color: {accent};
}}

QLabel#posterPlaceholder {{
    background-color: {surface}; color: {muted};
    border-radius: 8px; font-size: 48px; font-weight: 600;
}}

QLabel#metaKey {{ font-size: 12px; color: {muted}; }}
QLabel#metaValue {{ font-size: 12px; color: {accent}; font-weight: 600; }}
QLabel#fileTitle {{ font-size: 11px; }}
QLabel#fileMeta {{ font-size: 10px; color: {muted}; }}
QLabel#cardBadges {{
    color: {accent}; font-size: 10px; font-weight: 700;
    padding: 2px 0; background: transparent;
}}

QFrame#recentRow {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame#recentRow:hover {{
    background-color: {surface};
    border: 1px solid {accent};
}}
QLabel#recentTitle {{ font-size: 12px; font-weight: 500; }}
QLabel#recentMeta {{ font-size: 10px; color: {muted}; }}
QPushButton#recentResume {{
    background-color: {accent}; color: {on_accent};
    border: none; border-radius: 4px;
    padding: 3px 10px; font-size: 10px; font-weight: 600;
}}
QPushButton#recentResume:hover {{ background-color: {hover}; }}
QPushButton#recentResume:disabled {{
    background-color: {surface}; color: {muted};
}}

QWidget#nowPlayingBar {{
    background-color: {surface};
    border-top: 1px solid {muted};
}}
QLabel#nowPlayingIndicator {{ color: {accent}; font-size: 11px; }}
QLabel#nowPlayingTitle {{ font-size: 12px; font-weight: 600; }}
QLabel#nowPlayingTime {{ font-size: 11px; color: {muted}; }}
"""


def get_qss(version: str, theme: str) -> str:
    """返回指定版本+主题的完整 QSS (基础样式 + 追加控件样式)"""
    key = version if version in VERSIONS else "echo"
    v = VERSIONS[key]
    base = v.get(theme, v["dark"])
    return base + _extra_qss(key, theme if theme in ("dark", "light") else "dark")


def toggle_theme(current: str) -> str:
    return "light" if current == "dark" else "dark"


def toggle_version(current: str) -> str:
    return "prism" if current == "echo" else "echo"


def get_version_name(version: str) -> str:
    return VERSIONS.get(version, VERSIONS["echo"])["name"]


# 配置持久化统一走 app.utils.config_utils（默认值 ← 磁盘 ← 本次修改 三层合并，
# 局部字典不会抹掉其他配置键）。此处 re-export 以保持既有调用点不变。
from app.utils.config_utils import save_config  # noqa: E402,F401