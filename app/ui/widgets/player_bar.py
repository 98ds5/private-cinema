"""
底部「正在播放」状态条

为什么只在 MPV 模式显示:
  VividPlayer 是纯协议拉起, 没有 IPC —— 拿不到播放位置, 也发不了停止命令。
  在这种模式下显示"正在播放"是一条无法验证的假信息 (我们并不知道它是否还在播),
  所以直接不显示, 而不是显示一个空壳。
  MPV 通过 JSON IPC 上报 position/duration 并接受命令, 状态条才有真实数据可填。

由 PlayerService 的信号驱动:
  playback_started   → show_playing(title)
  playback_position  → update_position(pos, dur)
  playback_finished  → clear()
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
        # 用 isHidden() 而不是 isVisible(): 后者是「有效可见性」, 依赖所有
        # 祖先都可见 (窗口最小化/尚未 show 时为 False), 会让进度更新被静默丢弃。
        # 这里只想在状态条被显式 clear() 隐藏时跳过。
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
