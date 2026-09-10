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
        # recent 视图的排序在 refresh() 里写死"最近观看" (下拉值被无视),
        # 留一个看着能切、点了没反应的下拉是误导 → 藏掉。只能用 hide():
        # widget 拿到父级前调 show()/setVisible(True) 会闪成顶层窗口 (HANDOFF §4.26)。
        if view == "recent":
            self._sort.hide()
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
        self._grid.setSpacing(self.GRID_SPACING)
        self._grid.setContentsMargins(0, 0, 8, 0)
        self._cards = []
        self._cols = 0                 # 当前列数; 0 = 还没排过

        # 行间距会被窗口高度拉伸的根因: QScrollArea(widgetResizable=True) 把宿主
        # 撑到 max(视口尺寸, minimumSizeHint), 窗口一高, 多出来的高度就被 QGridLayout
        # **平摊到每一行** → 卡片之间的竖间距跟着变大(实测 9 列时行间距 447px, 应为 16px)。
        # 列数变多后行数变少(15 部从 4 行变 2 行), 同样的多余高度摊到更少的行上,
        # 缝隙格外明显 —— 所以这毛病是响应式网格上线后才被看见的。
        #
        # 修法: 宿主外面再套一层 QVBoxLayout, 网格拿自己的 sizeHint 高度,
        # **尾部 addStretch() 把剩下的高度全吃掉**。
        # 不要用 grid.setRowStretch() —— 那是 §4.7 的红线; 尾部 addStretch() 才是许可写法。
        self._scroll_widget = QWidget()
        self._outer = QVBoxLayout(self._scroll_widget)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(self._grid_host)
        self._outer.addStretch(1)

        # 拖窗口时重排的防抖定时器 (认 self 当爹, 页面销毁自动停)
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

        # 竖向滚动条出现/消失会改变视口宽度, 但**不会**给 LibraryPage 发 resizeEvent,
        # 所以这里也得触发一次重排, 否则列数按"没有滚动条"的宽度算, 卡片会被滚动条压住。
        self._scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._reflow_timer.start())

        # 延迟加载（等窗口显示后再查数据）
        # ⚠️ 必须传 self 作为 context 对象: 不带 context 的 singleShot 在页面销毁后
        # 照样会触发, 打进已销毁的 C++ 对象 → 刷一屏
        # "RuntimeError: libshiboken: Internal C++ object already deleted"。
        # 带上 self, 页面一销毁这个定时器就自动取消。
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
            # 带上异常类型: 只写 str(e) 会把 DetachedInstanceError 这类问题
            # 藏成一句看不懂的英文, 排查时定位不到根因。
            self._subtitle.setText(f"加载失败: {type(e).__name__}: {e}")

    def _render_cards(self, items: list):
        # 清空旧卡片: removeWidget → hide → setParent(None) → deleteLater, 四步各有用途:
        #   - setParent(None): deleteLater 要等下一轮事件循环才真销毁, 期间旧卡片仍挂在
        #     容器子树里, 搜索框每敲一个字符 refresh 一次就会成倍堆积 (实测 15 部查出 30 张)。
        #   - hide(): removeWidget 只是把它从布局里摘出来, 控件仍是子级,
        #     在真正销毁前会继续画在老位置上 → 留残影。
        # 注: 曾怀疑 setParent(None) 会让旧卡片闪成一串顶层小窗, **实测证伪** ——
        #     Qt6 在 setParent(None) 时自己就把控件藏了 (isHidden() 恒 True, isVisible() 恒 False)。
        #     真正闪窗的是 media_card.py 里那个还没拿到父级就被 setVisible(True) 的角标 QLabel。
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

        # "建卡片"和"摆卡片"分开: 摆的位置只由当前列数决定, 窗口一改就能重摆,
        # 不必把 15 张卡片全销毁重建 (那样每拖一下窗口都要重跑一遍数据渲染)。
        self._cols = self._cols_for(self._viewport_width())
        self._place_cards()

    # ------------------------------------------------------------------
    # 响应式网格
    #
    # 原来列数是写死的 `i // 4, i % 4`: 窗口缩到最小和拉到全屏都是 4 列,
    # 卡片一样大、结构一样 —— 全屏时右边一大片空白, 缩小时横向挤不下。
    # ------------------------------------------------------------------
    def _viewport_width(self) -> int:
        """网格真正能用的宽度 = 滚动区视口宽 - 网格左右边距(右边距留给竖向滚动条)"""
        m = self._grid.contentsMargins()
        return self._scroll.viewport().width() - m.left() - m.right()

    def _cols_for(self, width: int) -> int:
        """
        按可用宽度算一行放几列。卡片是固定宽, 所以就是"塞得下几个"。
        先加一个间距再整除是标准 grid 算法: n 个卡片占 n*W + (n-1)*S。
        """
        if width <= 0:
            # 还没显示过时 viewport 宽度是 0, 别据此把列数打成 1 (之后又要抖回去)
            return self._cols or 1
        step = MediaCard.CARD_W + self.GRID_SPACING
        return max(1, (width + self.GRID_SPACING) // step)

    def _place_cards(self):
        """按 self._cols 把卡片摆进网格。重排必须先 removeWidget, 否则 addWidget 会加第二份。"""
        cols = self._cols or 1
        for i, card in enumerate(self._cards):
            self._grid.removeWidget(card)
            # 卡片是固定尺寸, 指定对齐避免在单元格里被拉变形或错位。
            # 顶部对齐靠这个单元格级 AlignTop 实现 —— 不要给 grid 设
            # setAlignment(), 也不要加底部弹性行 setRowStretch():
            # 两者都会让 QScrollArea(widgetResizable) 改用视口高度而不是
            # minimumSizeHint, 滚动条上限恒为 0、下方卡片被裁掉点不到。
            self._grid.addWidget(card, i // cols, i % cols, Qt.AlignTop | Qt.AlignLeft)

    def _reflow(self):
        """
        窗口宽度变了 → 重算列数 → 只在**列数真的变了**时重摆。

        这个守卫同时是滚动条震荡的收敛条件: 列数变少 → 内容变高 → 滚动条出现 →
        视口变窄 → 可能再少一列。每轮列数严格变化, 几轮就到不动点;
        没有守卫就会在两个列数之间来回抖。
        """
        cols = self._cols_for(self._viewport_width())
        if cols == self._cols:
            return
        self._cols = cols
        self._place_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 拖动窗口时每个像素都会来一次 resizeEvent, 直接重排会卡 → 防抖
        self._reflow_timer.start()

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