"""
底部「正在播放」状态条, 由 PlayerService 的 playback_* 信号驱动。
仅 MPV 模式显示: VividPlayer 纯协议拉起、无 IPC, 进度和停止都做不到。
"""
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt, Signal


def fmt_time(seconds: int) -> str:
    """秒 → h:mm:ss / m:ss"""
    seconds = int(seconds or 0)
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class PlayerBar(QWidget):
    """细状态条: 片名 + 进度 + 停止"""

    stop_requested = Signal()

    BAR_HEIGHT = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("nowPlayingBar")
        # 纯 QWidget 默认不绘制 QSS 背景, 必须显式打开
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.BAR_HEIGHT)
        self.setVisible(False)          # 默认隐藏, 有播放才出现

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        self._indicator = QLabel("▶")
        self._indicator.setObjectName("nowPlayingIndicator")
        layout.addWidget(self._indicator)

        self._title = QLabel("")
        self._title.setObjectName("nowPlayingTitle")
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._title, stretch=1)

        self._time = QLabel("")
        self._time.setObjectName("nowPlayingTime")
        layout.addWidget(self._time)

        self._stop_btn = QPushButton("■  停止")
        self._stop_btn.setObjectName("rowAction")
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self._stop_btn)

    # ---- 对外接口 ----
    def show_playing(self, title: str):
        """开始播放: 显示状态条并填入片名"""
        self._title.setText(title or "未知标题")
        self._time.setText("")
        self.setVisible(True)

    def update_position(self, position: int, duration: int):
        """MPV 上报进度时刷新时间显示"""
        # isHidden() 而非 isVisible(): 后者依赖祖先可见性,
        # 窗口最小化时进度更新会被静默丢掉
        if self.isHidden():
            return
        if duration and duration > 0:
            self._time.setText(f"{fmt_time(position)} / {fmt_time(duration)}")
        else:
            self._time.setText(fmt_time(position))

    def clear(self):
        """播放结束: 隐藏状态条"""
        self._title.setText("")
        self._time.setText("")
        self.setVisible(False)
