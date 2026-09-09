"""
影视详情页 — 显示作品信息 + 文件/剧集列表 + 播放入口

设计要点:
  1. 会话内物化成纯 dict 再交给 UI。ORM 实例一旦脱离 Session, 访问未加载的
     关系属性就会 DetachedInstanceError (本项目在 stats.py 踩过同一个坑)。
  2. 播放粒度: 电影存 media 表, 动漫存 episodes 表 (见 models/tables.py 注释),
     因此播放时必须把 episode_id 传给 PlayerService, 续播也要按集读取进度。
  3. 不写死颜色: 全部用 objectName, 由 styles.get_qss 按当前版本+主题取色,
     这样 Echo/Prism × 明/暗 四种组合都自动适配。
"""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QScrollArea,
)
from PySide6.QtCore import Qt

from app.database import get_session
from app.models.tables import Media, MediaFile, Episode
from app.services.stats import StatsService
from app.utils.quality import format_tech_line, tech_tooltip


class DetailPage(QWidget):
    def __init__(self, media_id: int = None,
                 stats_service: Optional[StatsService] = None,
                 parent=None):
        super().__init__(parent)
        self.media_id = media_id
        self.stats = stats_service

        # 整页必须放进滚动区。
        # 本页的 minimumSizeHint 实测是 1002x996 (200x280 海报 + 信息区 + 10 集列表),
        # 而窗口小一点就只有 ~800x520。没有滚动区时 Qt 只能硬压 ——
        # 实测小窗下 10 个选集行按钮**全部塌成高度 0** (y=358..358, 49x0),
        # 主播放按钮被压成 97x11; 全屏 (2409x1352) 则一切正常。
        # 用户报的"动漫详情页没有选集""播放键不对""全屏正常小窗口有问题"
        # 三条其实是同一个根因。
        #
        # 形状照抄 SettingsPage (已验证可用): outer 零边距 → QScrollArea → container。
        # 红线 (HANDOFF §4.7): 不要给 container 的布局设 setAlignment(),
        # 也不要加 setRowStretch(); 顶部对齐用末尾的 addStretch() 实现。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        container = QWidget()
        container.setObjectName("pageContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # 返回按钮
        back_btn = QPushButton("←  返回")
        back_btn.setObjectName("themeToggle")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._go_back)
        layout.addWidget(back_btn, alignment=Qt.AlignLeft)

        # 信息区
        self._info_frame = QFrame()
        self._info_frame.setObjectName("settingGroup")
        self._info_layout = QHBoxLayout(self._info_frame)
        self._info_layout.setSpacing(24)
        layout.addWidget(self._info_frame)

        # 文件 / 剧集列表
        self._files_title = QLabel("视频文件")
        self._files_title.setObjectName("pageSubtitle")
        layout.addWidget(self._files_title)

        self._files_container = QFrame()
        self._files_container.setObjectName("settingGroup")
        self._files_layout = QVBoxLayout(self._files_container)
        self._files_layout.setSpacing(6)
        layout.addWidget(self._files_container)

        layout.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

        if media_id:
            self._load(media_id)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def _load(self, media_id: int):
        """读取作品 + 文件, 在会话内物化成 dict"""
        with get_session() as s:
            m = s.query(Media).filter(Media.id == media_id).first()
            if not m:
                return

            media = {
                "id": m.id,
                "title": m.title or "",
                "media_type": m.media_type,
                "year": m.year,
                "status": m.status,
                "rating": m.rating,
                "poster": m.poster_path,
                "synopsis": m.synopsis,
                "director": m.director,
                "watched_position": m.watched_position or 0,
                "watched_duration": m.watched_duration or 0,
            }

            # 按集号排序 (电影无集号 → nullslast 落到文件名排序)
            rows = (
                s.query(MediaFile, Episode.episode_number)
                .outerjoin(Episode, MediaFile.episode_id == Episode.id)
                .filter(MediaFile.media_id == media_id)
                .order_by(
                    Episode.episode_number.asc().nullslast(),
                    MediaFile.file_name.asc(),
                )
                .all()
            )

            files = [
                {
                    "id": f.id,
                    "file_path": f.file_path,
                    "file_name": f.file_name,
                    "file_size": f.file_size or 0,
                    "episode_id": f.episode_id,
                    "episode_number": ep_num,
                    "duration": f.duration or 0,
                    "width": f.width,
                    "height": f.height,
                    "video_codec": f.video_codec,
                    "hdr_type": f.hdr_type,
                    # 这几项原先没往 UI 传, 于是帧率/音轨/字幕轨在详情页全丢了
                    "frame_rate": f.frame_rate,
                    "audio_codec": f.audio_codec,
                    "audio_tracks": f.audio_tracks,
                    "subtitle_tracks": f.subtitle_tracks,
                    "container_format": f.container_format,
                }
                for f, ep_num in rows
            ]

        self._show_info(media, files)

    # ------------------------------------------------------------------
    # 信息区
    # ------------------------------------------------------------------
    def _show_info(self, media: dict, files: list):
        self._clear_layout(self._info_layout)

        # 海报占位 (未接刮削, 用标题首字)
        poster = QLabel(media["title"][:2] if media["title"] else "?")
        poster.setObjectName("posterPlaceholder")
        poster.setFixedSize(200, 280)
        poster.setAlignment(Qt.AlignCenter)
        self._info_layout.addWidget(poster)

        meta = QVBoxLayout()
        meta.setSpacing(8)

        name = QLabel(media["title"])
        name.setObjectName("pageTitle")
        meta.addWidget(name)

        status_map = {
            "unwatched": "未观看", "watching": "观看中", "watched": "已观看",
        }
        kind = "动漫" if media["media_type"] == "anime" else "电影"

        for label, val in [
            ("类型", kind),
            ("年份", str(media["year"]) if media["year"] else "—"),
            ("状态", status_map.get(media["status"], "—")),
            ("评分", f"{media['rating']}/10" if media["rating"] else "—"),
            ("文件", f"{len(files)} 个"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(6)
            k = QLabel(f"{label}:")
            k.setObjectName("metaKey")
            v = QLabel(val)
            v.setObjectName("metaValue")
            row.addWidget(k)
            row.addWidget(v)
            row.addStretch()
            meta.addLayout(row)

        if media.get("synopsis"):
            syn = QLabel(media["synopsis"])
            syn.setObjectName("metaKey")
            syn.setWordWrap(True)
            meta.addWidget(syn)

        meta.addStretch()

        # 播放 / 续播
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        target = self._pick_file(files)

        play_btn = QPushButton("▶  播放")
        play_btn.setObjectName("primaryAction")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setEnabled(target is not None)
        play_btn.clicked.connect(lambda: self._play(media, target))
        btn_row.addWidget(play_btn)

        if media["watched_position"] and target is not None:
            resume_btn = QPushButton("↻  续播")
            resume_btn.setObjectName("ghostAction")
            resume_btn.setCursor(Qt.PointingHandCursor)
            resume_btn.clicked.connect(
                lambda: self._play(media, target, resume=True)
            )
            btn_row.addWidget(resume_btn)

        btn_row.addStretch()
        meta.addLayout(btn_row)

        self._info_layout.addLayout(meta, stretch=1)

        # 动漫叫"选集", 电影叫"视频文件" —— 用户报"没有选集"时,
        # 一部分原因就是这个标题在动漫页上也写着"视频文件", 认不出来。
        if media["media_type"] == "anime":
            self._files_title.setText(f"选集 · 共 {len(files)} 集")
        else:
            self._files_title.setText(f"视频文件 · 共 {len(files)} 个")

        self._show_files(media, files)

    # ------------------------------------------------------------------
    # 文件 / 剧集列表
    # ------------------------------------------------------------------
    def _show_files(self, media: dict, files: list):
        self._clear_layout(self._files_layout)

        if not files:
            lbl = QLabel("暂无文件")
            lbl.setObjectName("settingValue")
            lbl.setAlignment(Qt.AlignCenter)
            self._files_layout.addWidget(lbl)
            return

        for f in files:
            row = QHBoxLayout()
            row.setSpacing(10)

            # 每集/每文件独立播放入口 (动漫原来只能播第一个文件)
            play = QPushButton("▶ 播放")
            play.setObjectName("rowAction")
            play.setCursor(Qt.PointingHandCursor)
            play.clicked.connect(lambda checked=False, mf=f: self._play(media, mf))
            row.addWidget(play)

            if f.get("episode_id"):
                resume = QPushButton("↻")
                resume.setObjectName("rowAction")
                resume.setToolTip("从上次位置续播")
                resume.setCursor(Qt.PointingHandCursor)
                resume.clicked.connect(
                    lambda checked=False, mf=f: self._play(media, mf, resume=True)
                )
                row.addWidget(resume)

            prefix = f"第{f['episode_number']}集 · " if f.get("episode_number") else ""
            name = QLabel(prefix + (f["file_name"] or ""))
            name.setObjectName("fileTitle")

            # 技术信息单独一行放在文件名下面。
            # 原先它和文件名挤在同一行, 而且只渲染 宽x高/编码/HDR/时长/体积,
            # 其中 hdr_type == "SDR" 被**刻意隐藏**, 帧率/音轨/字幕轨一条都没显示
            # → 用户报"视频画质信息不够明确, 比如 hdr 什么的"。
            # 数据其实 25/25 全在库里, 纯粹是没往上传。
            tech = QLabel(format_tech_line(f))
            tech.setObjectName("fileMeta")

            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(name)
            texts.addWidget(tech)
            text_box = QWidget()
            text_box.setLayout(texts)

            # QLabel 的 minimumSizeHint 等于整行文字的宽度。不设显式最小宽度的话,
            # 一个长文件名或一长串技术参数就能把整页最小宽度顶到 1000px 以上,
            # 而横向滚动条是关掉的 → 右侧被裁掉。完整内容都在 tooltip 里。
            name.setMinimumWidth(60)
            tech.setMinimumWidth(60)
            tip = tech_tooltip(f)
            name.setToolTip(tip)
            tech.setToolTip(tip)
            row.addWidget(text_box, stretch=1)

            container = QWidget()
            container.setLayout(row)
            self._files_layout.addWidget(container)

    # ------------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------------
    def _pick_file(self, files: list) -> Optional[dict]:
        """
        默认播放目标: 动漫取第一个未看完的集, 电影/兜底取第一个文件。
        """
        if not files:
            return None

        ep_ids = [f["episode_id"] for f in files if f.get("episode_id")]
        if ep_ids:
            with get_session() as s:
                rows = s.query(Episode.id, Episode.status).filter(
                    Episode.id.in_(ep_ids)
                ).all()
            status_map = {r[0]: r[1] for r in rows}
            for f in files:
                eid = f.get("episode_id")
                if eid and status_map.get(eid) != "watched":
                    return f

        return files[0]

    def _resume_position(self, media: dict, mf: dict) -> int:
        """
        续播起点。粒度必须和 _save_progress 的写入粒度一致:
        动漫读 episodes.watched_position, 电影读 media.watched_position。
        """
        if mf.get("episode_id"):
            with get_session() as s:
                ep = s.query(Episode).filter(
                    Episode.id == mf["episode_id"]
                ).first()
                return (ep.watched_position or 0) if ep else 0
        return media.get("watched_position") or 0

    def _find_player_service(self):
        parent = self.parent()
        while parent and not hasattr(parent, "player_service"):
            parent = parent.parent()
        return getattr(parent, "player_service", None) if parent else None

    def _play(self, media: dict, mf: Optional[dict], resume: bool = False):
        """交给 PlayerService 播放 (MPV 全功能 / VividPlayer 纯拉起)"""
        if mf is None:
            return
        ps = self._find_player_service()
        if ps is None:
            return

        start_pos = self._resume_position(media, mf) if resume else 0
        ps.play(
            mf["file_path"],
            media["id"],
            episode_id=mf.get("episode_id"),
            start_pos=start_pos,
        )

    # ------------------------------------------------------------------
    def _go_back(self):
        parent = self.parent()
        while parent and not hasattr(parent, "pages"):
            parent = parent.parent()
        if parent and hasattr(parent, "pages"):
            parent.pages.removeWidget(self)
            parent.pages.setCurrentIndex(0)
            self.deleteLater()

    @staticmethod
    def _clear_layout(layout):
        """递归清空布局 (子布局也要删, 否则残留)"""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                DetailPage._clear_layout(sub)
                sub.deleteLater()
