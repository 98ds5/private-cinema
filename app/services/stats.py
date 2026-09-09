"""
数据统计服务 — 完整实现

指标:
  - 资源总量: 电影数 / 动漫数 / 总数
  - 观看状态: 已观看 / 观看中 / 未观看
  - 存储信息: 视频文件总数 / 占用空间
  - 类型分布: 电影与动漫占比
"""
from typing import Dict, Any
from sqlalchemy import func

from app.database import get_session
from app.models.tables import Media, MediaFile


class StatsService:
    """数据聚合统计 (纯查询, 无状态, 可每次现算)"""

    def get_overview(self) -> Dict[str, Any]:
        """总览: 电影/动漫/总数/已观看/观看中/未观看/文件数/占用字节"""
        with get_session() as s:
            total = s.query(func.count(Media.id)).scalar() or 0
            movies = s.query(func.count(Media.id)).filter(
                Media.media_type == "movie"
            ).scalar() or 0
            animes = s.query(func.count(Media.id)).filter(
                Media.media_type == "anime"
            ).scalar() or 0

            watched = s.query(func.count(Media.id)).filter(
                Media.status == "watched"
            ).scalar() or 0
            watching = s.query(func.count(Media.id)).filter(
                Media.status == "watching"
            ).scalar() or 0
            unwatched = s.query(func.count(Media.id)).filter(
                Media.status == "unwatched"
            ).scalar() or 0

            files = s.query(func.count(MediaFile.id)).scalar() or 0
            size_bytes = s.query(func.sum(MediaFile.file_size)).scalar() or 0

        return {
            "total": total,
            "movies": movies,
            "animes": animes,
            "watched": watched,
            "watching": watching,
            "unwatched": unwatched,
            "files": files,
            "size_bytes": size_bytes,
        }

    def get_type_distribution(self) -> Dict[str, int]:
        """类型分布: {"movie": N, "anime": N}"""
        with get_session() as s:
            movies = s.query(func.count(Media.id)).filter(
                Media.media_type == "movie"
            ).scalar() or 0
            animes = s.query(func.count(Media.id)).filter(
                Media.media_type == "anime"
            ).scalar() or 0
        return {"movie": movies, "anime": animes}

    def get_status_distribution(self) -> Dict[str, int]:
        """观看状态分布: {"unwatched":N, "watching":N, "watched":N}"""
        with get_session() as s:
            unwatched = s.query(func.count(Media.id)).filter(
                Media.status == "unwatched"
            ).scalar() or 0
            watching = s.query(func.count(Media.id)).filter(
                Media.status == "watching"
            ).scalar() or 0
            watched = s.query(func.count(Media.id)).filter(
                Media.status == "watched"
            ).scalar() or 0
        return {"unwatched": unwatched, "watching": watching, "watched": watched}

    def get_recently_watched(self, limit: int = 10) -> list:
        """最近观看记录 (按 last_watched_at 降序)"""
        with get_session() as s:
            results = s.query(Media).filter(
                Media.last_watched_at.isnot(None)
            ).order_by(Media.last_watched_at.desc()).limit(limit).all()

            # 必须在会话内物化成纯 dict: 会话关闭后 ORM 实例即脱离,
            # 再访问任何未加载的属性都会 DetachedInstanceError。
            return [
                {
                    "id": m.id,
                    "title": m.title,
                    "media_type": m.media_type,
                    "watched_at": m.last_watched_at.isoformat() if m.last_watched_at else None,
                    "progress": f"{m.watched_position}/{m.watched_duration}s",
                }
                for m in results
            ]

    def get_media_title(self, media_id: int) -> str:
        """按 id 取标题 (给「正在播放」状态条用); 查不到返回空串"""
        if not media_id:
            return ""
        with get_session() as s:
            row = s.query(Media.title).filter(Media.id == media_id).first()
        return row[0] if row and row[0] else ""

    def get_library_list(self, media_type: str = None, search: str = None,
                         sort: str = "title", limit: int = 100, offset: int = 0) -> dict:
        """
        影视库列表查询 → {"total": N, "items": [ {...}, ... ]}

        media_type: None / "all" 表示不限; "movie" / "anime" 按类型过滤。
        """
        with get_session() as s:
            q = s.query(Media)
            if media_type and media_type != "all":
                q = q.filter(Media.media_type == media_type)
            if search:
                q = q.filter(Media.title.ilike(f"%{search}%"))

            if sort == "year":
                q = q.order_by(Media.year.desc().nullslast())
            elif sort == "添加时间":
                q = q.order_by(Media.created_at.desc())
            elif sort == "最近观看":
                q = q.order_by(Media.last_watched_at.desc().nullslast())
            else:
                q = q.order_by(Media.title.asc())

            total = q.count()
            items = q.offset(offset).limit(limit).all()

            # 文件数用一次分组查询取回, 而不是逐个 m.files 懒加载:
            #   1. 避免 N+1 查询 (每部作品一次 SQL)
            #   2. 懒加载在会话关闭后必然 DetachedInstanceError
            ids = [m.id for m in items]
            if ids:
                counts = dict(
                    s.query(MediaFile.media_id, func.count(MediaFile.id))
                    .filter(MediaFile.media_id.in_(ids))
                    .group_by(MediaFile.media_id)
                    .all()
                )
            else:
                counts = {}

            return {
                "total": total,
                "items": [
                    {
                        "id": m.id,
                        "title": m.title,
                        "media_type": m.media_type,
                        "year": m.year,
                        "status": m.status,
                        "rating": m.rating,
                        "poster": m.poster_path,
                        "file_count": counts.get(m.id, 0),
                    }
                    for m in items
                ],
            }