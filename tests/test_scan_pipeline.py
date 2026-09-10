"""
扫描入库端到端回归: 扁平 / 嵌套布局的标题与年份解析、动漫自动建季建集、
扩展名覆盖与过滤、重复扫描幂等、统计聚合一致、丢失文件标记。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.database import init_database, get_session
from app.models.tables import Library, Media, MediaFile, Season, Episode
from app.services.scanner import ScanWorker
from app.services.stats import StatsService
from app.storage.local import LocalStorage

# QThread 的信号机制需要一个 QApplication 实例存在
_app = QApplication.instance() or QApplication(sys.argv)


def _touch(path: Path, size: int = 1024) -> Path:
    """创建一个指定大小的假视频文件 (扫描只看名字和大小, 不需要真实内容)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def db(tmp_path):
    """每个测试独立的数据库, 不污染真实 data/cinema.db"""
    init_database(str(tmp_path / "test.db"))
    yield


@pytest.fixture
def flat_lib(tmp_path, db):
    """扁平布局媒体库: 所有文件直接位于根目录"""
    root = tmp_path / "flat"
    root.mkdir()
    _touch(root / "Inception.2010.1080p.BluRay.x264.mkv")
    _touch(root / "Interstellar.2014.mkv")
    _touch(root / "Attack.on.Titan.S01E03.1080p.mkv")
    _touch(root / "One.Piece.EP07.mkv")
    _touch(root / "Old.Movie.wmv")          # 补充扩展名
    _touch(root / "Disc.Image.iso")         # 补充扩展名
    _touch(root / "DVD.Title.vob")          # 补充扩展名
    _touch(root / "notes.txt")              # 非视频, 必须忽略

    with get_session() as s:
        s.add(Library(name="flat", path=str(root), media_type="mixed"))
        s.commit()
    return root


def _scan() -> dict:
    """同步执行一次扫描 (直接调 run, 不起线程), 返回汇总"""
    worker = ScanWorker(LocalStorage())
    result = {}
    worker.scan_finished.connect(lambda d: result.update(d))
    worker.run()
    return result


def test_flat_layout_titles_come_from_filename(flat_lib):
    """扁平布局: 标题取文件名, 不能全库塌缩成目录名 'flat'"""
    stats = _scan()

    assert stats["new"] == 7, f"应入库 7 个视频文件, 实际 {stats}"
    assert stats["errors"] == 0

    with get_session() as s:
        titles = {m.title for m in s.query(Media).all()}

    assert "flat" not in titles, "标题错误地取了库根目录名 → 扁平布局判定失效"
    assert len(titles) == 7, f"7 个文件应得到 7 个不同作品, 实际 {titles}"
    assert "Inception" in titles
    assert "Interstellar" in titles


def test_flat_layout_extracts_year(flat_lib):
    """扁平布局下年份仍应从文件名解析"""
    _scan()
    with get_session() as s:
        inc = s.query(Media).filter(Media.title == "Inception").one()
        inter = s.query(Media).filter(Media.title == "Interstellar").one()
    assert inc.year == 2010
    assert inter.year == 2014


def test_anime_creates_season_and_episode(flat_lib):
    """动漫: S01E03 / EP07 应建季+集, 且文件正确挂到集上"""
    _scan()
    with get_session() as s:
        animes = s.query(Media).filter(Media.media_type == "anime").all()
        assert len(animes) == 2, f"应有 2 部动漫, 实际 {[a.title for a in animes]}"

        seasons = s.query(Season).all()
        episodes = s.query(Episode).all()
        assert len(seasons) == 2
        assert len(episodes) == 2

        eps = {(e.season.season_number, e.episode_number) for e in episodes}
        assert (1, 3) in eps, "S01E03 未正确解析"
        assert (1, 7) in eps, "EP07 未正确解析(应默认第 1 季)"

        # episode_id 关联
        linked = s.query(MediaFile).filter(
            MediaFile.episode_id.isnot(None)
        ).count()
        assert linked == 2


def test_extra_extensions_scanned_and_txt_ignored(flat_lib):
    """.wmv/.iso/.vob 应被扫到, .txt 应被忽略"""
    _scan()
    with get_session() as s:
        names = {mf.file_name for mf in s.query(MediaFile).all()}
        total = s.query(MediaFile).count()

    assert total == 7, f"视频文件总数应为 7, 实际 {total}"
    assert "Old.Movie.wmv" in names
    assert "Disc.Image.iso" in names
    assert "DVD.Title.vob" in names
    assert "notes.txt" not in names, "非视频文件被错误入库"


def test_rescan_is_idempotent(flat_lib):
    """重复扫描不产生重复记录"""
    first = _scan()
    second = _scan()

    assert first["new"] == 7
    assert second["new"] == 0, f"第二次扫描不应新增, 实际 {second}"
    assert second["skipped"] == 7

    with get_session() as s:
        assert s.query(Media).count() == 7
        assert s.query(MediaFile).count() == 7


def test_stats_overview_matches(flat_lib):
    """统计聚合结果应与入库数据一致"""
    _scan()
    overview = StatsService().get_overview()

    assert overview["total"] == 7
    assert overview["movies"] == 5
    assert overview["animes"] == 2
    assert overview["unwatched"] == 7
    assert overview["files"] == 7
    assert overview["size_bytes"] == 7 * 1024

    dist = StatsService().get_type_distribution()
    assert dist == {"movie": 5, "anime": 2}


def test_nested_layout_title_from_folder(tmp_path, db):
    """嵌套布局「一片一文件夹」: 标题取目录名, 年份从目录名提取"""
    root = tmp_path / "nested"
    _touch(root / "盗梦空间 (2010)" / "Inception.mkv")
    _touch(root / "进击的巨人" / "进击的巨人 S01E03.mkv")

    with get_session() as s:
        s.add(Library(name="nested", path=str(root), media_type="mixed"))
        s.commit()

    _scan()

    with get_session() as s:
        movie = s.query(Media).filter(Media.title == "盗梦空间").one()
        assert movie.year == 2010
        assert movie.media_type == "movie"

        anime = s.query(Media).filter(Media.title == "进击的巨人").one()
        assert anime.media_type == "anime"
        assert s.query(Episode).count() == 1


def test_library_list_all_views_after_scan(flat_lib):
    """扫描后 get_library_list 四个视图都能返回数据 (file_count 须在会话内算好)"""
    _scan()
    stats = StatsService()

    all_items = stats.get_library_list()
    assert all_items["total"] == 7
    assert len(all_items["items"]) == 7

    movies = stats.get_library_list(media_type="movie")
    assert movies["total"] == 5

    animes = stats.get_library_list(media_type="anime")
    assert animes["total"] == 2

    # file_count 必须在会话内算好, 且数值正确
    for item in all_items["items"]:
        assert item["file_count"] == 1, f"{item['title']} 文件数错误"
        assert item["title"], "标题不应为空"

    # 搜索过滤
    hit = stats.get_library_list(search="Inception")
    assert hit["total"] == 1
    assert hit["items"][0]["title"] == "Inception"


def test_missing_file_marked_after_rescan(flat_lib):
    """文件被删除后重扫: 标记为 failed / 文件已丢失"""
    _scan()
    victim = flat_lib / "Old.Movie.wmv"
    assert victim.exists()
    victim.unlink()

    stats = _scan()
    assert stats["missing"] >= 1, f"应检出丢失文件, 实际 {stats}"

    with get_session() as s:
        mf = s.query(MediaFile).filter(
            MediaFile.file_name == "Old.Movie.wmv"
        ).one()
        assert mf.parse_status == "failed"
        assert mf.parse_error == "文件已丢失"
