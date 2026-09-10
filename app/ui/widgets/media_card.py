"""
影视卡片组件 — 显示海报 + 画质角标 + 标题 + 年份 + 状态
"""
import os

from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal

from app.utils.quality import badges_from_media


class MediaCard(QFrame):
    """影视卡片"""

    clicked = Signal(int)  # media_id

    # 加了一行角标就从海报区借高度, 否则三个 QLabel 会被压扁
    POSTER_H = 132
    # LibraryPage 按 CARD_W 算一行放几列
    CARD_W = 160
    CARD_H = 200

    def __init__(self, media: dict = None, parent=None):
        super().__init__(parent)
        self.setObjectName("mediaCard")
        self.setFixedSize(self.CARD_W, self.CARD_H)
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
        self._poster = poster
        layout.addWidget(poster)
        self._apply_poster(media)

        # 画质角标 (4K / HDR10 / DV / 中字)
        badges = badges_from_media(media) if media else []
        self._badges = QLabel("  ".join(badges))
        self._badges.setObjectName("cardBadges")
        self._badges.setFixedHeight(18)
        self._badges.setAlignment(Qt.AlignCenter)
        self._badges.setToolTip(" · ".join(badges) if badges else "无画质元数据")
        # 没数据就整行藏掉。只能 hide(): 拿到父级前 setVisible(True) 会闪成顶层小窗
        if not badges:
            self._badges.hide()
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

    def _apply_poster(self, media: dict):
        """有海报显示图, 否则保持首字母占位。
        竖版封面填满横向海报区: KeepAspectRatioByExpanding + 居中裁切"""
        src = (media or {}).get("poster")
        if not src or not os.path.isfile(src):
            return
        pm = QPixmap(src)
        if pm.isNull():
            return                        # 解不出来: 继续用占位
        pm = pm.scaled(self.CARD_W, self.POSTER_H,
                       Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (pm.width() - self.CARD_W) // 2)
        y = max(0, (pm.height() - self.POSTER_H) // 2)
        self._poster.setPixmap(pm.copy(x, y, self.CARD_W, self.POSTER_H))
        self._poster.setText("")

    def mousePressEvent(self, event):
        if self._media_id:
            self.clicked.emit(self._media_id)
        super().mousePressEvent(event)