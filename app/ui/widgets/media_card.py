"""
影视卡片组件 — 显示海报占位 + 画质角标 + 标题 + 年份 + 状态
"""
from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal

from app.utils.quality import badges_from_media


class MediaCard(QFrame):
    """影视卡片"""

    clicked = Signal(int)  # media_id

    # 卡片是固定尺寸, 加了一行角标就要从海报区借高度, 否则三个 QLabel 会被压扁
    POSTER_H = 132

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
        poster.setFixedHeight(self.POSTER_H)
        poster.setAlignment(Qt.AlignCenter)
        poster.setStyleSheet("background-color: #1a1d28; border-radius: 8px 8px 0 0; font-size: 32px;")
        poster.setText(media.get("title", "?")[:1] if media else "?")
        layout.addWidget(poster)

        # 画质角标 (4K / HDR10 / DV / 中字)
        # 原先卡片上完全看不出画质, 网格里分不清哪部是 4K Dolby Vision、
        # 哪部是 1080p SDR —— 数据其实 25/25 全在库里, 只是没往上传。
        badges = badges_from_media(media) if media else []
        self._badges = QLabel("  ".join(badges))
        self._badges.setObjectName("cardBadges")
        self._badges.setFixedHeight(18)
        self._badges.setAlignment(Qt.AlignCenter)
        self._badges.setToolTip(" · ".join(badges) if badges else "无画质元数据")
        # 没数据就整行藏掉, 别留一条空白占位
        self._badges.setVisible(bool(badges))
        layout.addWidget(self._badges)

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