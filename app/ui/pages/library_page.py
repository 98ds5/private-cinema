"""
影视库列表页 — 接入 StatsService 真实数据

展示媒体卡片网格 + 搜索/排序/分页。
view 参数: all / movie / anime / recent
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QGridLayout,
    QComboBox, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer

from app.services.stats import StatsService
from app.ui.widgets.media_card import MediaCard

_TITLES = {"all": "全部影视", "movie": "电影", "anime": "动漫", "recent": "最近观看"}


class LibraryPage(QWidget):
    def __init__(self, view: str = "all",
                 stats_service: Optional[StatsService] = None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("pageContent")
        self.view = view
        self.stats = stats_service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # 标题 + 筛选栏
        header = QHBoxLayout()
        header.setSpacing(16)

        title = QLabel(_TITLES.get(view, "影视库"))
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        # 页面内不再有搜索框。
        # 原先这里有一个**接好的** QLineEdit, 而 main_window 顶部还有一个
        # **没接线的**同名搜索框 —— 库页面上会同时出现两个搜索框, 而用户先看到、
        # 先去点的那个恰好是死的。现在搜索统一由顶部全局框驱动
        # (MainWindow._apply_search → LibraryPage.set_search)。
        self._search_text = ""

        self._sort = QComboBox()
        self._sort.addItems(["名称", "年份", "添加时间", "最近观看"])
        self._sort.setFixedWidth(120)
        self._sort.currentTextChanged.connect(self._on_sort)
        header.addWidget(self._sort)

        layout.addLayout(header)

        self._subtitle = QLabel("共 — 部影视")
        self._subtitle.setObjectName("pageSubtitle")
        layout.addWidget(self._subtitle)

        # 卡片网格 —— 必须放进滚动区。
        # 原来直接 addLayout(grid) + addStretch(): 作品超过两三行时,
        # 下面的卡片被窗口裁掉, 既看不到也点不到 (15 部作品 = 4 行就会触发)。
        #
        # 注意: 这里绝不能对 grid 调 setAlignment(Qt.AlignTop)。
        # 一旦顶层布局带 alignment, QScrollArea.setWidgetResizable(True) 就会
        # 按视口高度而不是 minimumSizeHint 给宿主定尺寸 → 滚动条上限恒为 0、
        # 内容被裁 (实测 host 490 vs 需要 848)。顶部对齐改用底部弹性行实现。
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(16)
        self._grid.setContentsMargins(0, 0, 8, 0)
        self._cards = []

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._grid_host)
        layout.addWidget(self._scroll, stretch=1)

        # 延迟加载（等窗口显示后再查数据）
        QTimer.singleShot(100, self.refresh)

    def refresh(self):
        if not self.stats:
            self._subtitle.setText("未接入数据服务")
            return
        try:
            media_type = self.view if self.view in ("movie", "anime") else None
            if self.view == "recent":
                sort = "最近观看"
            else:
                sort = self._sort.currentText()

            result = self.stats.get_library_list(
                media_type=media_type,
                search=self._search_text or None,
                sort=sort,
                limit=100,
            )
            self._subtitle.setText(f"共 {result['total']} 部影视")
            self._render_cards(result["items"])
        except Exception as e:
            # 带上异常类型: 只写 str(e) 会把 DetachedInstanceError 这类问题
            # 藏成一句看不懂的英文, 排查时定位不到根因。
            self._subtitle.setText(f"加载失败: {type(e).__name__}: {e}")

    def _render_cards(self, items: list):
        # 清空旧卡片。
        # 必须先 setParent(None) 再 deleteLater(): deleteLater 要等下一轮事件
        # 循环才真正销毁, 期间旧卡片仍挂在容器子树里。搜索框每敲一个字符就会
        # refresh 一次, 不清父子关系会让卡片成倍堆积 (实测 15 部查出 30 张)。
        for card in self._cards:
            self._grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        for i, item in enumerate(items):
            card = MediaCard(item)
            card.clicked.connect(self._on_card_clicked)
            # 卡片是固定尺寸, 指定对齐避免在单元格里被拉变形或错位。
            # 顶部对齐靠这个单元格级 AlignTop 实现 —— 不要给 grid 设
            # setAlignment(), 也不要加底部弹性行 setRowStretch():
            # 两者都会让 QScrollArea(widgetResizable) 改用视口高度而不是
            # minimumSizeHint, 滚动条上限恒为 0、下方卡片被裁掉点不到。
            self._grid.addWidget(card, i // 4, i % 4, Qt.AlignTop | Qt.AlignLeft)
            self._cards.append(card)

    def _on_card_clicked(self, media_id: int):
        """点击卡片 → 切换到详情页"""
        # 找到主窗口的 QStackedWidget 切换
        parent = self.parent()
        while parent and not hasattr(parent, 'pages'):
            parent = parent.parent()
        if not parent:
            return
        # 优先走 MainWindow._open_detail —— 它会销毁上一个详情页。
        # 原先这里每次点击都 pages.addWidget(DetailPage(...)), 旧的既不隐藏也不销毁,
        # 一直挂在 QStackedWidget 里: 点几十次就攒几十个孤儿页, 内存只增不减。
        if hasattr(parent, '_open_detail'):
            parent._open_detail(media_id)
            return
        from app.ui.pages.detail_page import DetailPage
        detail = DetailPage(media_id, stats_service=self.stats)
        parent.pages.addWidget(detail)
        parent.pages.setCurrentWidget(detail)

    def set_search(self, text: str, refresh: bool = True):
        """
        设置过滤关键词, 由 MainWindow 的顶部全局搜索框调用。

        refresh=False 用于"切页后紧接着会统一 refresh 一次"的场景,
        免得同一份数据查两遍。
        """
        text = (text or "").strip()
        if text == self._search_text:
            return
        self._search_text = text
        if refresh:
            self.refresh()

    def _on_sort(self, text):
        self.refresh()