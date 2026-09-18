"""
设置页 — 播放引擎、路径检测、媒体库目录、扫描启动
"""
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
    QComboBox, QPushButton, QFileDialog, QMessageBox, QScrollArea,
)
from PySide6.QtCore import Qt, Signal

from app.services.player import PlayerService
from app.services.scanner import ScanWorker
from app.storage.local import LocalStorage
from app.services.parser import ParseWorker
from app.services.posters import PosterWorker
from app.services.maintenance import (clear_library, list_backups,
                                      restore_latest, snapshot)
from app.utils.path_detector import detect_ffmpeg
from app.ui.styles import save_config


class SettingsPage(QWidget):

    # 媒体库被清空/恢复后发出: 主窗口收到要重新查库刷新页面
    library_changed = Signal()

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
        self._poster_worker = None

        self._build_ui()
        self._refresh_lib_list()
        self._refresh_maintenance()

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

        # ---- 海报提取 (抽视频内嵌封面, 不联网) ----
        self._poster_btn = QPushButton("提取内嵌封面")
        self._poster_btn.setObjectName("themeToggle")
        self._poster_btn.setToolTip(
            "给每部作品配一张封面, 存到 data/posters/, 全程不联网。优先级:\n"
            "① 视频同目录你自己放的 poster / cover / folder.jpg\n"
            "② 视频文件里内嵌的封面 (mkv/mp4 的 attached_pic 流)\n"
            "③ 自动截帧: 片长 10% → 30% → 50% 各截一张, 黑屏的自动跳过\n"
            "④ 都没有就继续用首字母占位\n"
            "提取完切一下页面, 卡片上就会出现封面。"
        )
        self._poster_btn.clicked.connect(self._start_posters)
        layout.addWidget(self._poster_btn)

        self._poster_status = QLabel("海报: 未提取")
        self._poster_status.setObjectName("settingValue")
        layout.addWidget(self._poster_status)

        # ---- 媒体库维护 (危险操作: 清空前自动备份, 可一键恢复) ----
        mg = QFrame()
        mg.setObjectName("settingGroup")
        mg_v = QVBoxLayout(mg)
        mg_v.setSpacing(8)

        mg_title = QLabel("媒体库维护")
        mg_title.setObjectName("settingLabel")
        mg_v.addWidget(mg_title)

        self._maint_status = QLabel("")
        self._maint_status.setObjectName("settingValue")
        mg_v.addWidget(self._maint_status)

        maint_row = QHBoxLayout()
        maint_row.setSpacing(8)

        self._clear_btn = QPushButton("清空媒体库")
        self._clear_btn.setObjectName("themeToggle")
        self._clear_btn.setStyleSheet(
            "background-color: #d63031; color: #ffffff; font-weight: 600;"
            "padding: 6px 16px; border-radius: 8px; border: none;"
        )
        self._clear_btn.setToolTip(
            "删除全部作品/文件记录与海报缓存。\n"
            "① 硬盘上的视频文件不会被删除\n"
            "② 媒体库目录配置保留, 可以直接重新扫描\n"
            "③ 清空前会自动备份, 之后可点「恢复最近备份」找回"
        )
        self._clear_btn.clicked.connect(self._clear_library)
        maint_row.addWidget(self._clear_btn)

        self._restore_btn = QPushButton("恢复最近备份")
        self._restore_btn.setObjectName("themeToggle")
        self._restore_btn.setToolTip("从 data/backup/ 里最新的一份备份恢复数据库与海报缓存")
        self._restore_btn.clicked.connect(self._restore_library)
        maint_row.addWidget(self._restore_btn)

        maint_row.addStretch()
        mg_v.addLayout(maint_row)

        layout.addWidget(mg)

        layout.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _on_engine_changed(self, idx):
        engine = self._engine_combo.currentData()
        self.config.setdefault("player", {})["engine"] = engine
        save_config(self.config)

        # 同时立即切换运行中的引擎, 不必重启
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
        self._refresh_maintenance()

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
        self._refresh_maintenance()

    # ------------------------------------------------------------------
    # 海报提取 (本地内嵌封面, 不联网)
    # ------------------------------------------------------------------
    def _start_posters(self):
        """抽内嵌封面。必须放线程里: 十几个 ffmpeg 子进程会把 UI 卡死。"""
        if self._poster_worker is not None and self._poster_worker.isRunning():
            return

        # 只读配置, 不往里写
        ffmpeg_path = detect_ffmpeg(
            self.config.get("ffmpeg", {}).get("ffmpeg_path"))
        if not ffmpeg_path:
            self._poster_status.setText(
                "海报: 找不到 ffmpeg (只有 ffprobe 不够, 抽图要 ffmpeg)")
            return

        self._poster_btn.setEnabled(False)
        self._poster_btn.setText("正在提取...")
        self._poster_status.setText("海报: 正在提取内嵌封面...")

        self._poster_worker = PosterWorker(self.ffprobe_path, ffmpeg_path)
        self._poster_worker.poster_progress.connect(self._on_poster_progress)
        self._poster_worker.poster_finished.connect(self._on_poster_finished)
        self._poster_worker.poster_error.connect(self._on_poster_error)
        self._poster_worker.start()

    def _on_poster_progress(self, title, source):
        zh = {"cached": "已有", "embedded": "内嵌封面", "manual": "同目录图片",
              "none": "没有封面", "failed": "失败"}
        if str(source).startswith("frame"):
            label = f"自动截帧 {source.split('@')[-1]}处"
        else:
            label = zh.get(source, source)
        self._poster_status.setText(f"海报: {title} → {label}")

    def _on_poster_error(self, msg):
        self._poster_status.setText(f"海报错误: {msg}")

    def _on_poster_finished(self, counts):
        self._poster_status.setText(
            f"海报完成: 截帧 {counts.get('frame',0)}  |  "
            f"内嵌 {counts.get('embedded',0)}  |  "
            f"同目录 {counts.get('manual',0)}  |  "
            f"已有 {counts.get('cached',0)}  |  "
            f"没有 {counts.get('none',0)}  |  "
            f"失败 {counts.get('failed',0)}"
        )
        self._poster_btn.setEnabled(True)
        self._poster_btn.setText("提取内嵌封面")
        self._refresh_maintenance()

    # ------------------------------------------------------------------
    # 媒体库维护: 清空 / 恢复 (危险操作, 走确认框 + 自动备份)
    # ------------------------------------------------------------------
    def _refresh_maintenance(self):
        try:
            s = snapshot()
            backups = list_backups()
        except Exception as e:
            self._maint_status.setText(f"媒体库维护: 读取失败 {type(e).__name__}: {e}")
            self._restore_btn.setEnabled(False)
            return

        newest = backups[0].name if backups else "无"
        self._maint_status.setText(
            f"当前 {s['media']} 部作品 / {s['files']} 个文件 / {s['posters']} 张海报缓存"
            f"  |  备份 {len(backups)} 份, 最近 {newest}"
        )
        self._restore_btn.setEnabled(bool(backups))

    def _confirm(self, title: str, text: str, info: str, ok_label: str) -> bool:
        """危险操作确认框: 自定义中文按钮, 默认按钮是「取消」"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        box.setInformativeText(info)
        ok_btn = box.addButton(ok_label, QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)

        # 离屏冒烟/测试里弹模态框会永久阻塞事件循环, 这里允许被替换掉
        box.exec()
        return box.clickedButton() is ok_btn

    def _clear_library(self):
        try:
            s = snapshot()
            backups = len(list_backups())
        except Exception as e:
            self._maint_status.setText(f"清空失败: {type(e).__name__}: {e}")
            return

        if s["media"] == 0 and s["files"] == 0:
            self._maint_status.setText("媒体库本来就是空的, 不需要清空")
            return

        if not self._confirm(
            "确认清空媒体库",
            f"将删除 {s['media']} 部作品、{s['files']} 个文件记录、{s['posters']} 张海报缓存。",
            "• 硬盘上的视频文件不会被删除\n"
            "• 媒体库目录配置会保留, 可以直接重新扫描\n"
            f"• 清空前会自动备份(现有 {backups} 份), 之后可点「恢复最近备份」找回",
            "确认清空",
        ):
            self._maint_status.setText("已取消清空")
            return

        try:
            res = clear_library()
        except Exception as e:
            self._maint_status.setText(f"清空失败(未删除任何数据): {type(e).__name__}: {e}")
            return

        self._refresh_maintenance()
        self._maint_status.setText(
            f"已清空: 删除 {res['media']} 部作品 / {res['files']} 个文件 / "
            f"{res['posters']} 张海报; 备份保存在 {res['backup']}"
        )
        self.library_changed.emit()

    def _restore_library(self):
        backups = []
        try:
            backups = list_backups()
        except Exception as e:
            self._maint_status.setText(f"恢复失败: {type(e).__name__}: {e}")
            return

        if not backups:
            self._maint_status.setText("没有可用备份")
            return

        if not self._confirm(
            "确认恢复备份",
            f"将用备份 {backups[0].name} 覆盖当前媒体库(工作区数据会被替换)。",
            "• 硬盘上的视频文件不受影响\n"
            "• 数据库与海报缓存会一起还原到备份时的状态",
            "确认恢复",
        ):
            self._maint_status.setText("已取消恢复")
            return

        try:
            res = restore_latest()
        except Exception as e:
            self._maint_status.setText(f"恢复失败: {type(e).__name__}: {e}")
            return

        self._refresh_maintenance()
        self._maint_status.setText(
            f"已从备份恢复: {res['media']} 部作品 / {res['files']} 个文件 / "
            f"{res['posters']} 张海报  ({res['backup']})"
        )
        self.library_changed.emit()