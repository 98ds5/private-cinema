"""
影视库列表页 — 卡片网格 + 搜索/排序
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
    GRID_SPACING = 16          # 网格间距; 同时用于算"一行放得下几列"
    REFLOW_DEBOUNCE_MS = 60    # 拖窗口时重排的防抖: 每个像素都会来一次 resizeEvent

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

        # 页面内不放搜索框, 统一由主窗口顶部全局框驱动 (set_search)
        self._search_text = ""

        self._sort = QComboBox()
        self._sort.addItems(["名称", "年份", "添加时间", "最近观看"])
        self._sort.setFixedWidth(120)
        self._sort.currentTextChanged.connect(self._on_sort)
        # recent 视图排序在 refresh() 里写死, 下拉无意义 → 隐藏。
        # 只能用 hide(): 拿到父级前 setVisible(True) 会闪成顶层窗口
        if view == "recent":
            self._sort.hide()
        header.addWidget(self._sort)

        layout.addLayout(header)

        self._subtitle = QLabel("共 — 部影视")
        self._subtitle.setObjectName("pageSubtitle")
        layout.addWidget(self._subtitle)

        # 卡片网格必须放进滚动区, 否则超出一屏的行看不到也点不到。
        # 滚动宿主布局带 alignment 会让 widgetResizable 按视口高而不是
        # minimumSizeHint 定尺寸 → 滚动条失效, 所以这里绝不能设
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(self.GRID_SPACING)
        self._grid.setContentsMargins(0, 0, 8, 0)
        self._cards = []
        self._cols = 0                 # 当前列数; 0 = 还没排过

        # 外面套一层 QVBoxLayout 并在尾部 addStretch(): 否则 widgetResizable
        # 撑出的多余高度会被网格平摊到每一行, 行间距被拉大
        self._scroll_widget = QWidget()
        self._outer = QVBoxLayout(self._scroll_widget)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(self._grid_host)
        self._outer.addStretch(1)

        # 重排防抖定时器 (parent=self, 页面销毁自动停)
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(self.REFLOW_DEBOUNCE_MS)
        self._reflow_timer.timeout.connect(self._reflow)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._scroll_widget)
        layout.addWidget(self._scroll, stretch=1)

        # 滚动条出现/消失会改变视口宽但不触发 resizeEvent → 也要重排一次
        self._scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._reflow_timer.start())

        # 延迟加载（等窗口显示后再查数据）。
        # singleShot 必须带 self 作 context, 否则页面销毁后仍会触发已销毁对象
        QTimer.singleShot(100, self, self.refresh)

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
            # 带上异常类型, 方便定位
            self._subtitle.setText(f"加载失败: {type(e).__name__}: {e}")

    def _render_cards(self, items: list):
        # removeWidget → hide → setParent(None) → deleteLater:
        # hide 防残影; setParent(None) 防 deleteLater 生效前旧卡片成倍堆积
        for card in self._cards:
            self._grid.removeWidget(card)
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        for item in items:
            card = MediaCard(item)
            card.clicked.connect(self._on_card_clicked)
            self._cards.append(card)

        # "建卡片"和"摆卡片"分开: 重排只挪位置, 不重建卡片
        self._cols = self._cols_for(self._viewport_width())
        self._place_cards()

    # ------------------------------------------------------------------
    # 响应式网格: 列数按视口宽度算
    # ------------------------------------------------------------------
    def _viewport_width(self) -> int:
        """网格真正能用的宽度 = 滚动区视口宽 - 网格左右边距(右边距留给竖向滚动条)"""
        m = self._grid.contentsMargins()
        return self._scroll.viewport().width() - m.left() - m.right()

    def _cols_for(self, width: int) -> int:
        """按可用宽度算列数: n 个卡片占 n*W + (n-1)*S"""
        if width <= 0:
            # 未显示时视口宽为 0, 不能据此定列数
            return self._cols or 1
        step = MediaCard.CARD_W + self.GRID_SPACING
        return max(1, (width + self.GRID_SPACING) // step)

    def _place_cards(self):
        """按 self._cols 把卡片摆进网格。重排必须先 removeWidget, 否则 addWidget 会加第二份。"""
        cols = self._cols or 1
        for i, card in enumerate(self._cards):
            self._grid.removeWidget(card)
            # 顶部对齐用单元格级 AlignTop; 给 grid 设 setAlignment 会让滚动条失效
            self._grid.addWidget(card, i // cols, i % cols, Qt.AlignTop | Qt.AlignLeft)

    def _reflow(self):
        """重算列数, 仅当列数真的变了才重摆。
        这个守卫同时是收敛条件: 没有它, 滚动条出现/消失会让列数来回抖"""
        cols = self._cols_for(self._viewport_width())
        if cols == self._cols:
            return
        self._cols = cols
        self._place_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # resizeEvent 逐像素触发, 直接重排会卡 → 防抖
        self._reflow_timer.start()

    def _on_card_clicked(self, media_id: int):
        """点击卡片 → 切换到详情页"""
        # 找到主窗口的 QStackedWidget 切换
        parent = self.parent()
        while parent and not hasattr(parent, 'pages'):
            parent = parent.parent()
        if not parent:
            return
        # 优先走 MainWindow._open_detail: 它会销毁上一个详情页
        if hasattr(parent, '_open_detail'):
            parent._open_detail(media_id)
            return
        from app.ui.pages.detail_page import DetailPage
        detail = DetailPage(media_id, stats_service=self.stats)
        parent.pages.addWidget(detail)
        parent.pages.setCurrentWidget(detail)

    def set_search(self, text: str, refresh: bool = True):
        """设置过滤关键词 (由顶部全局搜索框调用);
        refresh=False 表示调用方随后会统一刷新, 避免查两遍"""
        text = (text or "").strip()
        if text == self._search_text:
            return
        self._search_text = text
        if refresh:
            self.refresh()

    def _on_sort(self, text):
        self.refresh()