"""
播放进度持久化回归测试

覆盖 _save_progress 的双粒度写入 (电影→media 表 / 动漫→episodes 表)
以及动漫向 media 的聚合回写。

回归锁: 动漫分支原先只写 episodes, 从不回写 media, 导致看完动漫后
首页「已观看/观看中」始终为 0、「最近观看」永远不显示动漫。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.database import init_database, get_session
from app.models.tables import Library, Media, MediaFile, Episode, Season
from app.services.scanner import ScanWorker
from app.services.stats import StatsService
from app.services.player import _save_progress
from app.storage.local import LocalStorage

_app = QApplication.instance() or QApplication(sys.argv)


def _touch(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def lib(tmp_path):
    """一个含 1 部电影 + 1 部 3 集动漫的媒体库"""
    init_database(str(tmp_path / "test.db"))

    root = tmp_path / "lib"
    root.mkdir()
    movie = _touch(root / "Inception.2010.1080p.mkv")
    for n in (1, 2, 3):
        _touch(root / ("Some.Anime.S01E%02d.1080p.mkv" % n))

    with get_session() as s:
        s.add(Library(name="lib", path=str(root), media_type="mixed"))
        s.commit()

    worker = ScanWorker(LocalStorage())
    worker.run()

    # 注意: 不要在 with 会话块里 yield —— 那会把读事务开着贯穿整个测试,
    # 后续查询可能读到旧快照。先取值, 会话关闭后再 yield。
    with get_session() as s:
        m = s.query(Media).filter(Media.title == "Inception").one()
        a = s.query(Media).filter(Media.title == "Some Anime").one()
        ids = {"movie_id": m.id, "anime_id": a.id}

    ids["movie_path"] = str(movie)
    yield ids


def _episodes_of(media_id: int):
    """
    取某作品的全部集 → [(episode_id, episode_number), ...]

    Episode 没有 media_id 字段, 关系是 Episode → Season → Media,
    必须经 Season 关联。返回纯元组, 避免 ORM 实例脱离会话。
    """
    with get_session() as s:
        return (
            s.query(Episode.id, Episode.episode_number)
            .join(Season, Episode.season_id == Season.id)
            .filter(Season.media_id == media_id)
            .order_by(Episode.episode_number.asc())
            .all()
        )


def test_movie_progress_writes_media(lib):
    """电影: 进度写 media 表, 状态为观看中"""
    _save_progress(lib["movie_id"], 100, 1000)

    with get_session() as s:
        m = s.query(Media).filter(Media.id == lib["movie_id"]).one()
        assert m.status == "watching"
        assert m.watched_position == 100
        assert m.watched_duration == 1000
        assert m.last_watched_at is not None


def test_movie_95_percent_marks_watched(lib):
    """看到 95% 以上判定为已观看"""
    _save_progress(lib["movie_id"], 960, 1000)
    with get_session() as s:
        m = s.query(Media).filter(Media.id == lib["movie_id"]).one()
        assert m.status == "watched"

    _save_progress(lib["movie_id"], 500, 1000)
    with get_session() as s:
        m = s.query(Media).filter(Media.id == lib["movie_id"]).one()
        assert m.status == "watching", "未看完不应判为已观看"


def test_anime_progress_writes_episode(lib):
    """动漫: 进度写 episodes 表"""
    ep_id, ep_num = _episodes_of(lib["anime_id"])[0]

    assert ep_num == 1
    _save_progress(lib["anime_id"], 500, 1440, episode_id=ep_id)

    with get_session() as s:
        ep = s.query(Episode).filter(Episode.id == ep_id).one()
        assert ep.status == "watching"
        assert ep.watched_position == 500
        assert ep.last_watched_at is not None


def test_anime_rolls_up_to_media(lib):
    """
    动漫必须聚合回写 media —— 这是首页统计和「最近观看」的数据来源。
    """
    ep_ids = [eid for eid, _num in _episodes_of(lib["anime_id"])]

    assert len(ep_ids) == 3

    # 看完第 1 集 → media 应为 watching
    _save_progress(lib["anime_id"], 1400, 1440, episode_id=ep_ids[0])
    with get_session() as s:
        m = s.query(Media).filter(Media.id == lib["anime_id"]).one()
        assert m.status == "watching", "看过动漫后 media 状态未回写"
        assert m.last_watched_at is not None, "动漫未进入最近观看的数据条件"

    # 看完全部 3 集 → media 应为 watched
    for eid in ep_ids:
        _save_progress(lib["anime_id"], 1440, 1440, episode_id=eid)
    with get_session() as s:
        m = s.query(Media).filter(Media.id == lib["anime_id"]).one()
        assert m.status == "watched", "全部集数看完后应聚合为已观看"


def test_stats_reflect_anime_progress(lib):
    """统计与最近观看必须能看到动漫进度"""
    ep_id = _episodes_of(lib["anime_id"])[0][0]

    _save_progress(lib["anime_id"], 1440, 1440, episode_id=ep_id)

    stats = StatsService()
    overview = stats.get_overview()
    assert overview["total"] == 2, f"库里应为 2 部作品(1电影+1动漫): {overview}"
    assert overview["watching"] == 1, f"动漫应计为观看中: {overview}"
    assert overview["unwatched"] == 1, f"电影仍未观看: {overview}"

    recent = stats.get_recently_watched(5)
    titles = [r["title"] for r in recent]
    assert "Some Anime" in titles, f"动漫未出现在最近观看: {titles}"


def test_missing_media_id_is_noop():
    """media_id 为空时安全返回, 不抛异常"""
    _save_progress(None, 10, 100)
    _save_progress(0, 10, 100)
