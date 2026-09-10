"""
数据统计服务 — 完整实现

指标:
  - 资源总量: 电影数 / 动漫数 / 总数
  - 观看状态: 已观看 / 观看中 / 未观看
  - 存储信息: 视频文件总数 / 占用空间
  - 类型分布: 电影与动漫占比
"""
import locale
from typing import Dict, Any
from sqlalchemy import func

from app.database import get_session
from app.models.tables import Episode, Media, MediaFile, Season
from app.utils.quality import best_hdr, fmt_dur, has_chinese_subtitle

# 名称/年份排序用系统 locale 的语言学规则: 中文系统 = 拼音序 + 英文大小写不敏感。
# 只动 LC_COLLATE —— LC_NUMERIC/LC_TIME 等类别不受影响, 项目里也没有别的逻辑依赖它。
# setlocale 失败时 strxfrm 退化成 C locale (码点序), 依然确定可用, 不必恐慌。
try:
    locale.setlocale(locale.LC_COLLATE, "")
except locale.Error:
    pass

# 排序模式统一映射: UI 下拉传中文标签("名称"/"年份"/"添加时间"/"最近观看"),
# 旧默认值是英文 "title", recent 视图传 "最近观看" —— 全部归一到四个内部模式。
# 旧 bug: 服务端只认英文 "year", UI 的"年份"静默落进 else 变名称序 (HANDOFF §4.32)。
_SORT_MODES = {
    "名称": "title", "title": "title",
    "年份": "year", "year": "year",
    "添加时间": "added", "added": "added",
    "最近观看": "recent", "recent": "recent",
}


def progress_text(position, duration) -> str:
    """
    续播进度的可读文案。

    原先 get_recently_watched 直接把 `f"{watched_position}/{watched_duration}s"`
    甩到 UI 上, 于是首页显示 "1440/1440s" 这种东西。
    """
    pos, dur = int(position or 0), int(duration or 0)
    if dur <= 0:
        return f"看到 {fmt_dur(pos)}" if pos > 0 else "未记录进度"
    if pos >= dur * 0.95:
        return "已看完"
    if pos <= 0:
        return "尚未开始"
    return f"看到 {fmt_dur(pos)} / {fmt_dur(dur)}"


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
        """
        最近观看记录 (按 last_watched_at 降序)。

        返回值带**续播所需的全部信息**: file_path / episode_id / position / duration。
        原先只有 id/title/media_type/watched_at 和一个裸字符串 progress,
        于是首页那几行既点不动、也看不出动漫看的是第几集
        → 用户报"最近观看也无法点进去具体看的哪个继续看"。

        ⚠️ 动漫的进度写在 episodes 表 (见 player._save_progress), media 表那份
        只是"最后看的那一集"的镜像 —— 不回 episodes 查就不知道是哪一集,
        也就没法把 episode_id 传给 PlayerService.play()。
        """
        with get_session() as s:
            results = s.query(Media).filter(
                Media.last_watched_at.isnot(None)
            ).order_by(Media.last_watched_at.desc()).limit(limit).all()

            ids = [m.id for m in results]
            ep_by_media, files_by_media = {}, {}
            if ids:
                # Episode 没有 media_id, 关系是 Episode → Season → Media
                ep_rows = (
                    s.query(Season.media_id, Episode.id, Episode.episode_number,
                            Episode.watched_position, Episode.last_watched_at)
                    .join(Season, Episode.season_id == Season.id)
                    .filter(Season.media_id.in_(ids),
                            Episode.last_watched_at.isnot(None))
                    .order_by(Episode.last_watched_at.desc())
                    .all()
                )
                for mid, eid, num, pos, _at in ep_rows:
                    # 已按时间降序, 每部作品的第一条就是最近看的那一集
                    ep_by_media.setdefault(mid, {
                        "episode_id": eid, "episode_number": num,
                        "position": pos or 0,
                    })

                # 文件路径一次取回, 不逐个 m.files 懒加载 (N+1 + 脱离会话后崩)
                f_rows = s.query(
                    MediaFile.media_id, MediaFile.id, MediaFile.file_path,
                    MediaFile.episode_id, MediaFile.duration,
                ).filter(MediaFile.media_id.in_(ids)).all()
                for mid, fid, path, eid, dur in f_rows:
                    files_by_media.setdefault(mid, []).append({
                        "file_id": fid, "file_path": path,
                        "episode_id": eid, "duration": dur or 0,
                    })

            # 必须在会话内物化成纯 dict: 会话关闭后 ORM 实例即脱离,
            # 再访问任何未加载的属性都会 DetachedInstanceError。
            items = []
            for m in results:
                ep = ep_by_media.get(m.id) or {}
                files = files_by_media.get(m.id, [])
                # 动漫挑那一集对应的文件; 电影(或没匹配上)取第一个
                pick = None
                if ep.get("episode_id"):
                    pick = next((f for f in files
                                 if f["episode_id"] == ep["episode_id"]), None)
                if pick is None and files:
                    pick = files[0]
                pick = pick or {}

                position = ep.get("position") or (m.watched_position or 0)
                duration = pick.get("duration") or (m.watched_duration or 0)
                items.append({
                    "id": m.id,
                    "title": m.title,
                    "media_type": m.media_type,
                    "poster": m.poster_path,
                    "watched_at": (m.last_watched_at.isoformat()
                                   if m.last_watched_at else None),
                    "episode_id": ep.get("episode_id"),
                    "episode_number": ep.get("episode_number"),
                    "file_id": pick.get("file_id"),
                    "file_path": pick.get("file_path"),
                    "position": position,
                    "duration": duration,
                    "progress_text": progress_text(position, duration),
                })
            return items

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

            total = q.count()

            mode = _SORT_MODES.get((sort or "").strip(), "title")
            if mode in ("title", "year"):
                # SQLite 只有 BINARY 排序规则: 英文大小写敏感 (Zebra < apple),
                # 中文按码点而非拼音。语言学规则塞不进 SQL → 取回全部符合行,
                # Python 里用 locale.strxfrm 排序再手动分页。个人库量级
                # (几百部以内) 全量取回完全可接受 (HANDOFF §4.32)。
                rows = q.all()
                if mode == "title":
                    rows.sort(key=lambda m: (locale.strxfrm(m.title or ""), m.id))
                else:
                    # 年份降序, 没解析出年份 (NULL) 的垫底; 同年内按名称语言学排
                    # (SQL 的 ORDER BY year DESC 对同值行顺序同样未定义, 实测跟着索引漂)
                    rows.sort(key=lambda m: (m.year is None, -(m.year or 0),
                                             locale.strxfrm(m.title or ""), m.id))
                items = rows[offset:offset + limit]
            else:
                if mode == "added":
                    # 添加时间降序 (最新在前); created_at 完全相同 (同一批扫描,
                    # 真库 15 部只差微秒) 时按 id 降序保证确定性
                    q = q.order_by(Media.created_at.desc(), Media.id.desc())
                else:  # recent
                    # 看过的按最近观看时间降序; 没看过的 (NULL) 全部垫底 ——
                    # SQL 对 NULL 行的相对顺序未定义 (实测按 rowid 裸奔, 数据一动就漂),
                    # 用添加时间降序兜底
                    q = q.order_by(Media.last_watched_at.desc().nullslast(),
                                   Media.created_at.desc(), Media.id.desc())
                items = q.offset(offset).limit(limit).all()

            # 文件数与画质信息各用一次分组查询取回, 而不是逐个 m.files 懒加载:
            #   1. 避免 N+1 查询 (每部作品一次 SQL)
            #   2. 懒加载在会话关闭后必然 DetachedInstanceError
            ids = [m.id for m in items]
            counts, tech = {}, {}
            if ids:
                counts = dict(
                    s.query(MediaFile.media_id, func.count(MediaFile.id))
                    .filter(MediaFile.media_id.in_(ids))
                    .group_by(MediaFile.media_id)
                    .all()
                )
                # 卡片上要显示 4K/HDR/中字 角标, 所以这里把这一页所有文件的技术
                # 元数据一次拉回来在 Python 里聚合 (100 部 × 几个文件, 量很小)。
                rows = s.query(
                    MediaFile.media_id, MediaFile.width, MediaFile.height,
                    MediaFile.hdr_type, MediaFile.video_codec,
                    MediaFile.subtitle_tracks,
                ).filter(MediaFile.media_id.in_(ids)).all()
                for mid, width, height, hdr, vcodec, subs in rows:
                    t = tech.setdefault(mid, {"widths": [], "heights": [],
                                              "hdrs": [], "vcodecs": [],
                                              "chi_sub": False})
                    if width:
                        t["widths"].append(width)
                    if height:
                        t["heights"].append(height)
                    if hdr:
                        t["hdrs"].append(hdr)
                    if vcodec:
                        t["vcodecs"].append(vcodec)
                    if has_chinese_subtitle(subs):
                        t["chi_sub"] = True
            else:
                counts = {}

            result_items = []
            for m in items:
                t = tech.get(m.id, {})
                result_items.append({
                    "id": m.id,
                    "title": m.title,
                    "media_type": m.media_type,
                    "year": m.year,
                    "status": m.status,
                    "rating": m.rating,
                    "poster": m.poster_path,
                    "file_count": counts.get(m.id, 0),
                    # 画质聚合: 一部作品多个文件时取最高的那个
                    "best_width": max(t.get("widths") or [0]),
                    "best_height": max(t.get("heights") or [0]),
                    "best_hdr": best_hdr(t.get("hdrs")),
                    "video_codec": (t.get("vcodecs") or [None])[0],
                    "has_chi_sub": t.get("chi_sub", False),
                })

            return {"total": total, "items": result_items}