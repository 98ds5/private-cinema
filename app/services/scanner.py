"""
扫描入库服务 — 完整实现

流程:
  1. storage.list_videos() 递归收集所有视频文件
  2. 对比数据库已有的 file_path → 识别新文件 / 已存在 / 已删除
  3. 新文件: parse_filename → 建 Media / MediaFile (动漫还要建 Season/Episode)
  4. 已删除文件标记为丢失
  5. 文件大小变化 → 触发重新解析
"""
from pathlib import Path
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QThread, Signal

from app.database import get_session
from app.models.tables import Library, Media, MediaFile, Season, Episode
from app.storage.local import LocalStorage
from app.utils.name_parser import parse_filename, is_anime_directory
from app.utils.constants import VIDEO_EXTENSIONS


class ScanWorker(QThread):
    """后台扫描线程"""

    scan_started = Signal()
    scan_progress = Signal(int, int, str)
    scan_file_added = Signal(str)
    scan_finished = Signal(dict)
    scan_error = Signal(str)

    def __init__(self, storage: LocalStorage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        self.scan_started.emit()
        stats = {"new": 0, "skipped": 0, "missing": 0, "errors": 0}

        try:
            with get_session() as session:
                # 1. 获取所有启用的媒体库目录
                libraries = session.query(Library).filter(
                    Library.scan_enabled == True
                ).all()

                if not libraries:
                    self.scan_finished.emit({**stats, "msg": "没有启用的媒体库目录"})
                    return

                # 2. 收集数据库已有文件路径
                existing_files = {
                    row.file_path
                    for row in session.query(MediaFile.file_path).all()
                }

                # 3. 遍历每个目录
                for lib in libraries:
                    if self._cancelled:
                        break

                    lib_path = lib.path
                    media_type = lib.media_type  # movie / anime / mixed

                    # 3a. 收集视频文件
                    files = self.storage.list_videos(lib_path, VIDEO_EXTENSIONS)
                    total = len(files)

                    for idx, fi in enumerate(files):
                        if self._cancelled:
                            break

                        fp = fi.path
                        self.scan_progress.emit(idx + 1, total, fi.name)

                        if fp in existing_files:
                            # 已存在 → 检查文件大小是否变化
                            mf = session.query(MediaFile).filter(
                                MediaFile.file_path == fp
                            ).first()
                            if mf and mf.file_size != fi.size:
                                mf.file_size = fi.size
                                mf.parse_status = "pending"
                                mf.parse_error = None
                                stats["skipped"] += 1
                            else:
                                stats["skipped"] += 1
                            continue

                        # 4. 新文件 → 入库
                        try:
                            self._add_file(
                                session, fp, fi, lib, media_type, lib_path,
                            )
                            stats["new"] += 1
                            self.scan_file_added.emit(fp)
                        except Exception as e:
                            stats["errors"] += 1
                            self.scan_error.emit(f"{fi.name}: {e}")

                    # 5. 标记该目录下已删除的文件
                    dir_files = {f.path for f in files}
                    missing = session.query(MediaFile).filter(
                        MediaFile.file_path.notin_(dir_files),
                        MediaFile.file_path.like(f"{lib_path}%"),
                    ).all()
                    for mf in missing:
                        mf.parse_status = "failed"
                        mf.parse_error = "文件已丢失"
                        stats["missing"] += 1

                session.commit()

        except Exception as e:
            self.scan_error.emit(f"扫描异常: {e}")
            stats["errors"] += 1

        self.scan_finished.emit(stats)

    def _add_file(self, session, fp: str, fi, lib, media_type: str,
                  root_dir: str = None):
        """将单个视频文件入库"""

        parent_dir = str(Path(fp).parent)
        parsed = parse_filename(fp, parent_dir, root_dir=root_dir)

        # 确定 media_type
        file_is_anime = parsed.is_anime or is_anime_directory(Path(parent_dir))
        if media_type == "mixed":
            mtype = "anime" if file_is_anime else "movie"
        else:
            mtype = media_type  # 用户指定

        # 查找或创建 Media
        title = parsed.title or Path(fp).stem
        media = session.query(Media).filter(
            Media.title == title,
            Media.media_type == mtype,
        ).first()

        if not media:
            media = Media(
                title=title,
                original_title=parsed.original_title or title,
                media_type=mtype,
                year=parsed.year,
                status="unwatched",
            )
            session.add(media)
            session.flush()  # 获取 id

        # 创建 MediaFile
        mf = MediaFile(
            media_id=media.id,
            file_path=fp,
            file_name=fi.name,
            file_size=fi.size,
            parse_status="pending",
        )
        session.add(mf)
        session.flush()

        # 如果是动漫且有季号 → 创建 Season / Episode
        if mtype == "anime" and parsed.episode_number is not None:
            sn = parsed.season_number or 1
            season = session.query(Season).filter(
                Season.media_id == media.id,
                Season.season_number == sn,
            ).first()
            if not season:
                season = Season(
                    media_id=media.id,
                    season_number=sn,
                    title=f"第{sn}季",
                )
                session.add(season)
                session.flush()

            episode = session.query(Episode).filter(
                Episode.season_id == season.id,
                Episode.episode_number == parsed.episode_number,
            ).first()
            if not episode:
                episode = Episode(
                    season_id=season.id,
                    episode_number=parsed.episode_number,
                    title=parsed.title or f"EP{parsed.episode_number}",
                )
                session.add(episode)
                session.flush()

            mf.episode_id = episode.id