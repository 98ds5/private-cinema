"""
主窗口 — 无边框 + 交通灯右上角 + 双版本双主题

标题栏:
  "私人影院" （居中）    [Echo/Prism] [☀/🌙]  ● ● ●
                                             关 缩 放
"""
import ctypes
from ctypes import wintypes

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QListWidget, QListWidgetItem,
    QStackedWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpacerItem, QSizePolicy,
    QMessageBox,
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent, QCursor

from app.ui.styles import (
    load_version, load_theme, get_qss,
    toggle_theme, toggle_version, get_version_name,
    save_config,
)
from app.ui.pages.home_page import HomePage
from app.ui.pages.library_page import LibraryPage
from app.ui.widgets.player_bar import PlayerBar
from app.ui.pages.detail_page import DetailPage
from app.ui.pages.settings_page import SettingsPage
from app.services.player import PlayerService
from app.services.stats import StatsService

WM_NCHITTEST = 0x0084
WM_NCCALCSIZE = 0x0083
HT_CAPTION = 2
HT_CLIENT = 1
HT_LEFT = 10
HT_RIGHT = 11
HT_TOP = 12
HT_TOPLEFT = 13
HT_TOPRIGHT = 14
HT_BOTTOM = 15
HT_BOTTOMLEFT = 16
HT_BOTTOMRIGHT = 17

NAV_ITEMS = ["首页", "全部影视", "电影", "动漫", "最近观看", "设置"]


class TrafficLightButtons(QFrame):
    """右上角交通灯 — 关闭/最小化/最大化"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(68, 22)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(6)

        self.btn_minimize = QPushButton()
        self.btn_minimize.setObjectName("trafficLight")
        self.btn_minimize.setStyleSheet(
            "background-color:#ffbd2e;border:0.5px solid #e0a020;border-radius:5px;"
            "min-width:10px;max-width:10px;min-height:10px;max-height:10px;"
        )
        self.btn_maximize = QPushButton()
        self.btn_maximize.setObjectName("trafficLight")
        self.btn_maximize.setStyleSheet(
            "background-color:#28c840;border:0.5px solid #1aab30;border-radius:5px;"
            "min-width:10px;max-width:10px;min-height:10px;max-height:10px;"
        )
        self.btn_close = QPushButton()
        self.btn_close.setObjectName("trafficLight")
        self.btn_close.setStyleSheet(
            "background-color:#ff5f57;border:0.5px solid #e04740;border-radius:5px;"
            "min-width:10px;max-width:10px;min-height:10px;max-height:10px;"
        )

        layout.addWidget(self.btn_minimize)
        layout.addWidget(self.btn_maximize)
        layout.addWidget(self.btn_close)


class MainWindow(QMainWindow):
    def __init__(self, config: dict,
                 ffprobe_path=None, mpv_path=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.ffprobe_path = ffprobe_path
        self.mpv_path = mpv_path

        self._version = load_version(config)
        self._theme = load_theme(config)
        self._drag_pos = QPoint()
        self._is_dragging = False

        self.player_service = PlayerService(config, mpv_path, self)
        self.stats_service = StatsService()

        # 播放错误必须在界面上可见。
        # 原先 playback_error 全项目无人接收, 引擎启动失败时完全静默,
        # 用户看到的就是"点了播放没反应", 排查时也没有任何线索。
        self.player_service.playback_error.connect(self._on_playback_error)

        # 「正在播放」状态条: 仅 MPV 模式有真实数据可填
        self.player_service.playback_started.connect(self._on_playback_started)
        self.player_service.playback_position.connect(self._on_playback_position)
        self.player_service.playback_finished.connect(self._on_playback_finished)

        self.setWindowTitle("私人影院")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(1100, 700)
        self.setMinimumSize(750, 450)

        self._build_ui()
        self._apply_qss()

    def _apply_qss(self):
        self.setStyleSheet(get_qss(self._version, self._theme))
        if hasattr(self, '_theme_btn'):
            self._theme_btn.setText("☀" if self._theme == "dark" else "☾")
        if hasattr(self, '_version_btn'):
            self._version_btn.setText(get_version_name(self._version))

    def _toggle_theme(self):
        self._theme = toggle_theme(self._theme)
        self._apply_qss()
        self.config.setdefault("system", {})["theme"] = self._theme
        save_config(self.config)

    def _toggle_version(self):
        self._version = toggle_version(self._version)
        self._apply_qss()
        self.config.setdefault("ui", {})["version"] = self._version
        save_config(self.config)

    def _build_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- 标题栏 ----
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        self._title_bar = title_bar
        title_bar.mousePressEvent = self._title_press
        title_bar.mouseMoveEvent = self._title_move
        title_bar.mouseDoubleClickEvent = self._title_dblclick

        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(16, 0, 0, 0)

        # 左侧：标题
        lbl = QLabel("私人影院")
        lbl.setObjectName("titleLabel")
        tb.addWidget(lbl)

        # 中间：弹性空间
        tb.addStretch()

        # 右侧：版本切换
        self._version_btn = QPushButton()
        self._version_btn.setObjectName("themeToggle")
        self._version_btn.clicked.connect(self._toggle_version)
        self._version_btn.setFixedHeight(22)
        tb.addWidget(self._version_btn)

        tb.addSpacing(6)

        # 主题切换
        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("themeToggle")
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._theme_btn.setFixedHeight(22)
        tb.addWidget(self._theme_btn)

        tb.addSpacing(10)

        # 交通灯（右上角）
        traffic = TrafficLightButtons()
        traffic.btn_close.clicked.connect(self.close)
        traffic.btn_minimize.clicked.connect(self.showMinimized)
        traffic.btn_maximize.clicked.connect(self._toggle_max)
        tb.addWidget(traffic)

        main_layout.addWidget(title_bar)

        # ---- 搜索栏 ----
        header = QFrame()
        header.setObjectName("headerBar")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(16, 0, 16, 0)

        search = QLineEdit()
        search.setObjectName("searchBox")
        search.setPlaceholderText("搜索影视名称...")
        hh.addWidget(search)
        hh.addStretch()
        main_layout.addWidget(header)

        # ---- 主体 ----
        body = QWidget()
        b_layout = QHBoxLayout(body)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(0)

        nav = QListWidget()
        nav.setObjectName("sidebar")
        for label in NAV_ITEMS:
            nav.addItem(QListWidgetItem(label))

        self.pages = QStackedWidget()
        self._home_page = HomePage(stats_service=self.stats_service)
        self.pages.addWidget(self._home_page)
        self.pages.addWidget(LibraryPage(view="all", stats_service=self.stats_service))
        self.pages.addWidget(LibraryPage(view="movie", stats_service=self.stats_service))
        self.pages.addWidget(LibraryPage(view="anime", stats_service=self.stats_service))
        self.pages.addWidget(LibraryPage(view="recent", stats_service=self.stats_service))
        self.pages.addWidget(SettingsPage(
            config=self.config, ffprobe_path=self.ffprobe_path,
            mpv_path=self.mpv_path, player_service=self.player_service,
        ))

        nav.currentRowChanged.connect(self._on_page_changed)
        nav.setCurrentRow(0)

        b_layout.addWidget(nav)
        b_layout.addWidget(self.pages, stretch=1)
        main_layout.addWidget(body, stretch=1)

        # 底部「正在播放」状态条 (默认隐藏, 播放时才出现)
        self._now_bar = PlayerBar()
        self._now_bar.stop_requested.connect(self._on_stop_requested)
        main_layout.addWidget(self._now_bar)

        self.setCentralWidget(central)

    # ---- 页面切换 ----
    def _on_page_changed(self, idx: int):
        """
        页面切换时刷新目标页面数据。

        用通用的 refresh() 探测而不是只刷新首页:
        LibraryPage 原先只在构造后 100ms 刷新一次, 那时数据库还是空的,
        扫描完成后再切过去也不会重查 → 影视库永远显示 0 张卡片。
        """
        self.pages.setCurrentIndex(idx)
        page = self.pages.widget(idx)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    # ---- 播放错误 ----
    def _on_playback_error(self, message: str):
        """播放失败 → 弹窗告知, 绝不静默"""
        QMessageBox.warning(self, "播放失败", str(message))

    # ---- 正在播放状态条 ----
    def _on_playback_started(self, media_id: int):
        """
        仅 MPV 模式显示状态条。
        VividPlayer 是纯协议拉起、无 IPC, 拿不到位置也停不下来,
        显示"正在播放"是无法验证的假信息, 所以不显示。
        """
        bar = getattr(self, "_now_bar", None)
        if bar is None or self.player_service.engine_type != "mpv":
            return
        title = self.stats_service.get_media_title(media_id)
        bar.show_playing(title or f"作品 #{media_id}")

    def _on_playback_position(self, position: int, duration: int):
        bar = getattr(self, "_now_bar", None)
        if bar is not None:
            bar.update_position(position, duration)

    def _on_playback_finished(self, media_id: int):
        bar = getattr(self, "_now_bar", None)
        if bar is not None:
            bar.clear()

    def _on_stop_requested(self):
        self.player_service.stop()
        bar = getattr(self, "_now_bar", None)
        if bar is not None:
            bar.clear()

    # ---- 窗口操作 ----
    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _title_press(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def _title_move(self, e: QMouseEvent):
        if self._is_dragging and e.buttons() == Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def _title_dblclick(self, e: QMouseEvent):
        self._toggle_max()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._is_dragging = False
        super().mouseReleaseEvent(e)

    # ---- Windows 原生消息 ----
    def nativeEvent(self, eventType, message):
        if eventType == "windows_generic_MSG":
            msg_ptr = int(message)
            msg = ctypes.wintypes.MSG.from_address(msg_ptr)
            if msg.message == WM_NCCALCSIZE:
                return True, 0
            if msg.message == WM_NCHITTEST:
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                return True, self._hit_test(x, y)
        return super().nativeEvent(eventType, message)

    def _hit_test(self, x: int, y: int) -> int:
        # lParam 是物理像素，缩放>100%时与 Qt 逻辑坐标不一致，
        # 统一用 QCursor.pos()（逻辑坐标）计算，避免整窗被误判成边缘。
        pt = self.mapFromGlobal(QCursor.pos())
        m = 5
        r = self.rect()
        w, h = r.width(), r.height()

        on_l = pt.x() <= m
        on_r = pt.x() >= w - m
        on_t = pt.y() <= m
        on_b = pt.y() >= h - m

        # 边缘优先命中：仅当不在交互控件上时才作为缩放边
        if on_t or on_r or on_b or on_l:
            child = self.childAt(pt)
            if not self._is_interactive(child):
                if on_t and on_l: return HT_TOPLEFT
                if on_t and on_r: return HT_TOPRIGHT
                if on_b and on_l: return HT_BOTTOMLEFT
                if on_b and on_r: return HT_BOTTOMRIGHT
                if on_t: return HT_TOP
                if on_b: return HT_BOTTOM
                if on_l: return HT_LEFT
                if on_r: return HT_RIGHT

        # 标题栏区域：可拖动；但按钮等交互控件放行
        tb = getattr(self, "_title_bar", None)
        if tb is not None and tb.geometry().contains(pt):
            child = self.childAt(pt)
            if self._is_interactive(child):
                return HT_CLIENT
            return HT_CAPTION

        return HT_CLIENT

    @staticmethod
    def _is_interactive(widget) -> bool:
        """是否为需要接收点击的控件"""
        return isinstance(
            widget,
            (QPushButton, QComboBox, QLineEdit, QListWidget),
        )