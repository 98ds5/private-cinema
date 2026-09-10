"""
详情页 — 作品信息 + 文件/剧集列表 + 播放入口
数据在会话内物化成 dict 再交给 UI, 避免 DetachedInstanceError。
"""
from typing import Optional
import os

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QScrollArea,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from app.database import get_session
from app.models.tables import Media, MediaFile, Episode
from app.services.stats import StatsService
from app.utils.quality import format_tech_line, tech_tooltip


class DetailPage(QWidget):
    # 海报区尺寸 (布局、_apply_poster 与测试共用)
    POSTER_W = 200
    POSTER_H = 280

    def __init__(self, media_id: int = None,
                 stats_service: Optional[StatsService] = None,
                 parent=None):
        super().__init__(parent)
        self.media_id = media_id
        self.stats = stats_service

        # 整页放进滚动区, 否则小窗下内容会被硬压扁 (选集行压成高度 0)
        # 不给布局设 setAlignment()/setRowStretch(), 顶部对齐靠末尾 addStretch()
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

        # 海报: 有图显示图, 路径失效/解码失败则保持前两字占位
        poster = QLabel(media["title"][:2] if media["title"] else "?")
        poster.setObjectName("posterPlaceholder")
        poster.setFixedSize(self.POSTER_W, self.POSTER_H)
        poster.setAlignment(Qt.AlignCenter)
        self._apply_poster(poster, media.get("poster"))
        self._info_layout.addWidget(poster)

        meta = QVBoxLayout()
        meta.setSpacing(8)

        name = QLabel(media["title"])
        name.setObjectName("pageTitle")
        # 同 fileTitle: 长标题在高 DPI 下会把整页最小宽度顶爆, 显式夹住, 全文放 tooltip
        name.setMinimumWidth(60)
        name.setToolTip(media["title"])
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

        # 动漫标题叫"选集", 电影叫"视频文件"
        if media["media_type"] == "anime":
            self._files_title.setText(f"选集 · 共 {len(files)} 集")
        else:
            self._files_title.setText(f"视频文件 · 共 {len(files)} 个")

        self._show_files(media, files)

    def _apply_poster(self, label: QLabel, src: Optional[str]):
        """有海报显示图, 否则保持占位文字。
        竖版封面填满横向海报区: KeepAspectRatioByExpanding + 居中裁切"""
        if not src or not os.path.isfile(src):
            return
        pm = QPixmap(src)
        if pm.isNull():
            return                        # 解不出来: 继续用占位
        pm = pm.scaled(self.POSTER_W, self.POSTER_H,
                       Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (pm.width() - self.POSTER_W) // 2)
        y = max(0, (pm.height() - self.POSTER_H) // 2)
        label.setPixmap(pm.copy(x, y, self.POSTER_W, self.POSTER_H))
        label.setText("")

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

            # 每集/每文件独立播放入口
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

            # 技术信息单独一行放在文件名下面
            tech = QLabel(format_tech_line(f))
            tech.setObjectName("fileMeta")

            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(name)
            texts.addWidget(tech)
            text_box = QWidget()
            text_box.setLayout(texts)

            # QLabel 最小宽度等于整行文字宽, 长文本会把页面最小宽度顶大 → 显式设小,
            # 完整内容放 tooltip
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
        """默认播放目标: 动漫取第一个未看完的集, 电影/兜底取第一个文件"""
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
        """续播起点。粒度必须与 _save_progress 一致: 动漫按集, 电影按 media"""
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
