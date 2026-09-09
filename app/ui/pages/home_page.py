"""
首页 — 概览 (接入 StatsService 真实数据)

展示统计数字卡片 + 最近观看列表。
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QGridLayout,
)
from PySide6.QtCore import Qt

from app.services.stats import StatsService


def _fmt_size(bytes_val: int) -> str:
    """格式化文件大小"""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 ** 2:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 ** 3:
        return f"{bytes_val / 1024 ** 2:.1f} MB"
    else:
        return f"{bytes_val / 1024 ** 3:.2f} GB"


class StatCard(QFrame):
    """统计数字卡片"""
    def __init__(self, number: str, label: str, accent: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        self.num_label = QLabel(number)
        self.num_label.setObjectName("statNumber")
        if accent:
            self.num_label.setProperty("class", accent)
        layout.addWidget(self.num_label)

        lbl = QLabel(label)
        lbl.setObjectName("statLabel")
        layout.addWidget(lbl)

    def set_number(self, text: str):
        self.num_label.setText(text)


class HomePage(QWidget):
    def __init__(self, stats_service: Optional[StatsService] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("pageContent")
        self.stats = stats_service
        self._cards = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("首页")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self._subtitle = QLabel("影视库概览")
        self._subtitle.setObjectName("pageSubtitle")
        layout.addWidget(self._subtitle)

        # 统计卡片网格
        grid = QHBoxLayout()
        grid.setSpacing(16)

        card_defs = [
            ("total", "影视总数", "accent"),
            ("movies", "电影", ""),
            ("animes", "动漫", ""),
            ("watched", "已观看", "watched"),
            ("size", "占用空间", ""),
        ]
        for key, label, accent in card_defs:
            card = StatCard("—", label, accent)
            self._cards[key] = card
            grid.addWidget(card)

        layout.addLayout(grid)

        # 最近观看区域
        recent_title = QLabel("最近观看")
        recent_title.setObjectName("pageSubtitle")
        recent_title.setStyleSheet("font-size: 15px; font-weight: 600; padding-top: 16px;")
        layout.addWidget(recent_title)

        self._recent_container = QFrame()
        self._recent_container.setObjectName("mediaCard")
        self._recent_layout = QVBoxLayout(self._recent_container)
        self._recent_layout.setAlignment(Qt.AlignCenter)
        self._recent_placeholder = QLabel("暂无最近观看记录\n添加媒体库目录后自动扫描入库")
        self._recent_placeholder.setAlignment(Qt.AlignCenter)
        self._recent_placeholder.setStyleSheet("color: #636366; font-size: 12px;")
        self._recent_layout.addWidget(self._recent_placeholder)
        layout.addWidget(self._recent_container)

        layout.addStretch()

    def refresh(self):
        """从 StatsService 刷新数据"""
        if not self.stats:
            self._subtitle.setText("影视库概览 — 未接入数据服务")
            return

        try:
            overview = self.stats.get_overview()
            self._cards["total"].set_number(str(overview["total"]))
            self._cards["movies"].set_number(str(overview["movies"]))
            self._cards["animes"].set_number(str(overview["animes"]))
            self._cards["watched"].set_number(str(overview["watched"]))
            self._cards["size"].set_number(_fmt_size(overview["size_bytes"]))

            total = overview["total"]
            self._subtitle.setText(
                f"共 {total} 部影视  |  "
                f"{overview['watched']} 已观看  ·  "
                f"{overview['watching']} 观看中  ·  "
                f"{overview['unwatched']} 未观看"
            )

            # 最近观看
            recent = self.stats.get_recently_watched(5)
            self._update_recent(recent)

        except Exception as e:
            self._subtitle.setText(f"数据加载失败: {e}")

    def _update_recent(self, items: list):
        """更新最近观看列表"""
        # 清空旧内容
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not items:
            self._recent_layout.addWidget(self._recent_placeholder)
            return

        for item in items[:5]:
            row = QHBoxLayout()
            title = QLabel(item["title"])
            title.setStyleSheet("font-size: 12px; font-weight: 500;")
            row.addWidget(title)
            row.addStretch()
            progress = QLabel(item["progress"])
            progress.setStyleSheet("font-size: 10px; color: #636366;")
            row.addWidget(progress)

            container = QWidget()
            container.setLayout(row)
            self._recent_layout.addWidget(container)