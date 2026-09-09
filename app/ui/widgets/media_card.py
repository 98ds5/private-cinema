"""
影视卡片组件 — 显示海报占位 + 标题 + 年份 + 状态
"""
from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal


class MediaCard(QFrame):
    """影视卡片"""

    clicked = Signal(int)  # media_id

    def __init__(self, media: dict = None, parent=None):
        super().__init__(parent)
        self.setObjectName("mediaCard")
        self.setFixedSize(160, 200)
        self._media_id = media.get("id") if media else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 海报区
        poster = QLabel()
        poster.setFixedHeight(140)
        poster.setAlignment(Qt.AlignCenter)
        poster.setStyleSheet("background-color: #1a1d28; border-radius: 8px 8px 0 0; font-size: 32px;")
        poster.setText(media.get("title", "?")[:1] if media else "?")
        layout.addWidget(poster)

        # 标题
        c_title = QLabel(media.get("title", "未知") if media else "未知")
        c_title.setObjectName("cardTitle")
        layout.addWidget(c_title)

        # 副标题
        parts = []
        if media and media.get("year"):
            parts.append(str(media["year"]))
        if media and media.get("status"):
            status_map = {"watched": "✓", "watching": "▶", "unwatched": ""}
            parts.append(status_map.get(media["status"], ""))
        c_sub = QLabel("  ·  ".join(filter(None, parts)))
        c_sub.setObjectName("cardSubtitle")
        layout.addWidget(c_sub)

    def set_media(self, media: dict):
        self._media_id = media.get("id")

    def mousePressEvent(self, event):
        if self._media_id:
            self.clicked.emit(self._media_id)
        super().mousePressEvent(event)