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
        # 没数据就整行藏掉, 别留一条空白占位。
        # ⚠️ 这里只能用 hide(), **绝不能写 setVisible(bool(badges))**:
        # setVisible(True) 作用在一个还没有父级的 widget 上, 会把它变成
        # **顶层窗口并立刻显示**, 而下一行 addWidget 才把它塞回卡片 → 每张卡片
        # 构造时都会在自己位置上闪出一个 15~94 x 18 的无字小窗再消失
        # (脱离 MainWindow 子树, QSS 不生效, 所以看着是空白的)。
        # 15 张卡 × 每次 refresh(切页 / 搜索防抖到期) = 用户看到的
        # "一串小窗口闪过去, 会自己关"。只有 LibraryPage 建卡片, 所以首页和设置不闪。
        # 有角标时什么都不用做: 加进布局后自然会跟着卡片显示。
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

    def mousePressEvent(self, event):
        if self._media_id:
            self.clicked.emit(self._media_id)
        super().mousePressEvent(event)