"""
首页 — 统计数字卡片 + 最近观看列表, 数据来自 StatsService
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QGridLayout, QPushButton,
)
from PySide6.QtCore import Qt, Signal

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


class RecentRow(QFrame):
    """「最近观看」的一行: 整行可点续播, 「详情」进详情页"""
    resume_clicked = Signal(dict)
    detail_clicked = Signal(int)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("recentRow")
        self._item = item
        self._media_id = item.get("id")
        self._can_resume = bool(item.get("file_path"))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)

        title = QLabel(item.get("title") or "未知")
        title.setObjectName("recentTitle")
        # QLabel 的 minimumSizeHint 等于整行文字宽, 长标题会把行撑爆
        title.setMinimumWidth(60)
        row.addWidget(title, stretch=1)

        # 动漫要显式说出是第几集
        ep_no = item.get("episode_number")
        if ep_no:
            ep = QLabel(f"第{ep_no}集")
            ep.setObjectName("recentMeta")
            row.addWidget(ep)

        prog = QLabel(item.get("progress_text") or "")
        prog.setObjectName("recentMeta")
        row.addWidget(prog)

        # 进度为 0 时按钮写「播放」而不是「继续观看」
        pos = int(item.get("position") or 0)
        self._resume_btn = QPushButton("▶ 继续观看" if pos > 0 else "▶ 播放")
        self._resume_btn.setObjectName("recentResume")
        self._resume_btn.setCursor(Qt.PointingHandCursor)
        self._resume_btn.setEnabled(self._can_resume)
        if not self._can_resume:
            self._resume_btn.setText("文件缺失")
            self._resume_btn.setToolTip("记录里没有这个作品的视频文件路径")
        self._resume_btn.clicked.connect(
            lambda: self.resume_clicked.emit(self._item))
        row.addWidget(self._resume_btn)

        detail_btn = QPushButton("详情")
        detail_btn.setObjectName("rowAction")     # 复用详情页已有的按钮样式
        detail_btn.setCursor(Qt.PointingHandCursor)
        detail_btn.clicked.connect(
            lambda: self.detail_clicked.emit(self._media_id))
        row.addWidget(detail_btn)

        # 整行点击 = 续播
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            f"{item.get('title') or ''}\n{item.get('file_path') or '（没有记录到视频文件）'}"
        )

    def mousePressEvent(self, ev):
        # 点按钮时 QPushButton 会吃掉事件, 不会走到这里, 所以不会重复触发
        if ev.button() == Qt.LeftButton and self._can_resume:
            self.resume_clicked.emit(self._item)
        super().mousePressEvent(ev)


class HomePage(QWidget):
    # HomePage 不自己碰播放器和导航, 只把意图抛给 MainWindow
    resume_requested = Signal(dict)    # 续播: 带 file_path/episode_id/position
    detail_requested = Signal(int)     # 进详情页: 带 media_id

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
        # 不设 setAlignment(AlignCenter): 会把行压到 sizeHint 宽, stretch 失效
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
        # 清空旧行, 但保留占位符实例 (空列表时还要塞回来)
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._recent_placeholder:
                # takeAt 后控件仍是子级, deleteLater 生效前会画在老位置 → 先 hide()
                w.hide()
                w.deleteLater()

        if not items:
            self._recent_layout.addWidget(self._recent_placeholder)
            return

        for item in items[:5]:
            row = RecentRow(item)
            # 信号转发信号: 播放和导航都由 MainWindow 负责
            row.resume_clicked.connect(self.resume_requested)
            row.detail_clicked.connect(self.detail_requested)
            self._recent_layout.addWidget(row)