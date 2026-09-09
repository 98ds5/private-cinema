"""
设置页 — 播放引擎、路径检测、媒体库目录、扫描启动
"""
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
    QComboBox, QPushButton, QFileDialog, QMessageBox, QScrollArea,
)
from PySide6.QtCore import Qt

from app.services.player import PlayerService
from app.services.scanner import ScanWorker
from app.storage.local import LocalStorage
from app.services.parser import ParseWorker
from app.ui.styles import save_config


class SettingsPage(QWidget):
    def __init__(self, config: dict = None,
                 ffprobe_path=None, mpv_path=None,
                 player_service=None, parent=None):
        super().__init__(parent)
        self.setObjectName("pageContent")
        self.config = config or {}
        self.ffprobe_path = ffprobe_path
        self.mpv_path = mpv_path
        self.player_service = player_service

        self._scan_worker = None
        self._parse_worker = None

        self._build_ui()
        self._refresh_lib_list()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        container = QWidget()
        container.setObjectName("pageContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel("播放引擎、媒体库目录与工具路径配置")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        # ---- 播放引擎 ----
        eg = QFrame()
        eg.setObjectName("settingGroup")
        eg_v = QVBoxLayout(eg)
        eg_v.setSpacing(12)

        eg_title = QLabel("播放引擎")
        eg_title.setObjectName("settingLabel")
        eg_v.addWidget(eg_title)

        engine_row = QHBoxLayout()
        engine_row.setSpacing(12)
        engine_label = QLabel("当前引擎:")
        engine_label.setStyleSheet("font-size: 12px;")
        engine_row.addWidget(engine_label)

        self._engine_combo = QComboBox()
        self._engine_combo.addItem("MPV (推荐, 全功能)", "mpv")
        self._engine_combo.addItem("VividPlayer (简化版)", "vividplayer")
        engine = self.config.get("player", {}).get("engine", "mpv")
        self._engine_combo.setCurrentIndex(0 if engine == "mpv" else 1)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self._engine_combo)
        engine_row.addStretch()
        eg_v.addLayout(engine_row)
        layout.addWidget(eg)

        # ---- 工具路径 ----
        pg = QFrame()
        pg.setObjectName("settingGroup")
        pg_v = QVBoxLayout(pg)
        pg_v.setSpacing(8)

        pg_title = QLabel("工具路径")
        pg_title.setObjectName("settingLabel")
        pg_v.addWidget(pg_title)

        for name, path in [
            ("ffprobe", self.ffprobe_path or "未找到"),
            ("MPV", self.mpv_path or "未找到"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"{name}:")
            lbl.setStyleSheet("font-size: 12px; min-width: 60px;")
            row.addWidget(lbl)
            val = QLabel(str(path))
            val.setObjectName("settingValue")
            green = path and path != "未找到"
            val.setStyleSheet(
                "font-size: 12px; color: #00b894;" if green
                else "font-size: 12px; color: #d63031;"
            )
            row.addWidget(val, stretch=1)
            pg_v.addLayout(row)

        layout.addWidget(pg)

        # ---- 媒体库目录 ----
        self._lib_group = QFrame()
        self._lib_group.setObjectName("settingGroup")
        self._lg_layout = QVBoxLayout(self._lib_group)
        self._lg_layout.setSpacing(8)

        lg_title = QLabel("媒体库目录")
        lg_title.setObjectName("settingLabel")
        self._lg_layout.addWidget(lg_title)

        self._lib_hint = QLabel("暂无目录")
        self._lib_hint.setObjectName("settingValue")
        self._lg_layout.addWidget(self._lib_hint)

        # 动态目录条目专用容器（只清理这里，不动按钮行）
        self._lib_list_layout = QVBoxLayout()
        self._lib_list_layout.setSpacing(6)
        self._lg_layout.addLayout(self._lib_list_layout)

        # 添加目录按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton("+ 添加目录")
        add_btn.setObjectName("themeToggle")
        add_btn.clicked.connect(self._add_library)
        btn_row.addWidget(add_btn)

        btn_row.addStretch()
        self._lg_layout.addLayout(btn_row)

        layout.addWidget(self._lib_group)

        # ---- 扫描按钮 ----
        self._scan_btn = QPushButton("开始扫描")
        self._scan_btn.setObjectName("themeToggle")
        self._scan_btn.setStyleSheet(
            "background-color: #4ecdc4; color: #0f1117; font-weight: 600;"
            "padding: 8px 24px; border-radius: 8px; border: none;"
        )
        self._scan_btn.clicked.connect(self._start_scan)
        layout.addWidget(self._scan_btn)

        self._scan_status = QLabel("就绪")
        self._scan_status.setObjectName("settingValue")
        layout.addWidget(self._scan_status)

        layout.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _on_engine_changed(self, idx):
        engine = self._engine_combo.currentData()
        self.config.setdefault("player", {})["engine"] = engine
        save_config(self.config)

        # 立即切换运行中的引擎。
        # 原先只写配置文件, 必须重启 app 才生效 —— 用户从 VividPlayer 切到
        # MPV 想测进度保存时, 会以为设置项是坏的。
        if self.player_service is None:
            self._scan_status.setText(f"引擎已保存为 {engine} (未接入播放服务)")
            return

        try:
            ok = self.player_service.set_engine(engine)
        except Exception as e:
            self._scan_status.setText(
                f"切换引擎失败: {type(e).__name__}: {e}"
            )
            return

        if ok:
            name = ("MPV (全功能: 进度/续播)" if engine == "mpv"
                    else "VividPlayer (纯拉起: 不记进度)")
            self._scan_status.setText(f"已切换到 {name}, 立即生效")
        else:
            self._scan_status.setText(f"切换引擎被拒绝: {engine}")

    def _add_library(self):
        path = QFileDialog.getExistingDirectory(self, "选择媒体库目录")
        if not path:
            return
        libs = self.config.get("libraries", [])
        if path not in libs:
            libs.append(path)
            self.config["libraries"] = libs
            save_config(self.config)
            self._refresh_lib_list()

    def _refresh_lib_list(self):
        """刷新媒体库目录列表（只重建动态条目，不影响按钮行）"""
        self._clear_layout(self._lib_list_layout)

        libs = self.config.get("libraries", [])
        if not libs:
            self._lib_hint.show()
            return
        self._lib_hint.hide()

        for path in libs:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(path)
            lbl.setObjectName("settingValue")
            lbl.setStyleSheet("font-size: 11px;")
            row.addWidget(lbl, stretch=1)

            rm_btn = QPushButton("✕")
            rm_btn.setFixedSize(20, 20)
            rm_btn.setStyleSheet(
                "border: none; border-radius: 10px;"
                "background: #d63031; color: white; font-size: 10px;"
            )
            rm_btn.clicked.connect(lambda checked, p=path: self._remove_library(p))
            row.addWidget(rm_btn)

            self._lib_list_layout.addLayout(row)

    def _remove_library(self, path: str):
        libs = self.config.get("libraries", [])
        if path in libs:
            libs.remove(path)
            self.config["libraries"] = libs
            save_config(self.config)
            self._refresh_lib_list()

    def _clear_layout(self, layout):
        """递归清空布局：既删 widget 也删嵌套子布局"""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                self._clear_layout(sub)
                sub.deleteLater()

    def _start_scan(self):
        """启动扫描线程"""
        libs = self.config.get("libraries", [])
        if not libs:
            self._scan_status.setText("请先添加媒体库目录")
            return

        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("扫描中...")
        self._scan_status.setText("正在扫描...")

        # 先将 config 中的 libraries 写入数据库
        from app.database import get_session
        from app.models.tables import Library

        with get_session() as s:
            for path in libs:
                existing = s.query(Library).filter(Library.path == path).first()
                if not existing:
                    s.add(Library(path=path, name=Path(path).name, scan_enabled=True))
            s.commit()

        # 启动扫描线程
        storage = LocalStorage()
        self._scan_worker = ScanWorker(storage)
        self._scan_worker.scan_progress.connect(self._on_scan_progress)
        self._scan_worker.scan_finished.connect(self._on_scan_finished)
        self._scan_worker.scan_error.connect(self._on_scan_error)
        self._scan_worker.start()

        # 扫描完成后启动解析
        self._scan_worker.scan_finished.connect(self._start_parse)

    def _on_scan_progress(self, cur, total, name):
        self._scan_status.setText(f"扫描: {cur}/{total}  {name}")

    def _on_scan_finished(self, stats):
        self._scan_status.setText(
            f"扫描完成: 新增 {stats.get('new',0)}  |  "
            f"跳过 {stats.get('skipped',0)}  |  "
            f"丢失 {stats.get('missing',0)}  |  "
            f"错误 {stats.get('errors',0)}"
        )

    def _on_scan_error(self, msg):
        self._scan_status.setText(f"错误: {msg}")

    def _start_parse(self, stats):
        """扫描完成后自动开始解析"""
        if not self.ffprobe_path:
            self._scan_status.setText("ffprobe 未找到, 跳过解析")
            self._scan_btn.setEnabled(True)
            self._scan_btn.setText("开始扫描")
            return

        self._scan_status.setText("正在解析文件参数...")
        self._parse_worker = ParseWorker(self.ffprobe_path)
        self._parse_worker.parse_progress.connect(self._on_parse_progress)
        self._parse_worker.parse_finished.connect(self._on_parse_finished)
        self._parse_worker.start()

    def _on_parse_progress(self, name, status):
        self._scan_status.setText(f"解析: {name}  →  {status}")

    def _on_parse_finished(self, result):
        self._scan_status.setText(
            f"解析完成: 成功 {result.get('success',0)}  |  "
            f"失败 {result.get('failed',0)}"
        )
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("开始扫描")