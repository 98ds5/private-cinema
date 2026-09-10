"""海报提取(本地抽内嵌封面, 不联网刮削)的行为锁。

子进程调用统一走 posters._run, 打桩它即可覆盖成功/失败/超时/脏输出;
posters_dir() 打桩到 tmp_path, 避免测试写进真实的 data/posters/。
"""
import json
import subprocess

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from app.database import get_session, init_database
from app.models.tables import Media, MediaFile
from app.services import posters as P
from app.ui.pages.detail_page import DetailPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.widgets.media_card import MediaCard
from app.utils import path_detector as PD


@pytest.fixture(scope="module", autouse=True)
def app():
    """autouse: 造测试图的 QPixmap 需要 QGuiApplication 在场, 间接用到的测试太多"""
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def posters_dir(tmp_path, monkeypatch):
    """把海报目录打桩到 tmp —— 绝不能让测试写进项目的 data/posters/"""
    d = tmp_path / "posters"
    monkeypatch.setattr(P, "posters_dir", lambda: d)
    return d


@pytest.fixture
def video(tmp_path):
    """一个假的视频文件: 只关心存在性和同目录, 内容无所谓"""
    v = tmp_path / "Movie.Pacific.Rim.2013.2160p.mkv"
    v.write_bytes(b"\x1a\x45\xdf\xa3not really mkv")
    return v


def _fake_run(returncode=0, stdout="", stderr="", raises=None):
    """造一个替身 posters._run"""
    def _r(cmd, timeout):
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return _r


def _streams_json(attached_at=None, n_video=2):
    streams = []
    for i in range(n_video):
        streams.append({
            "index": i,
            "codec_name": "hevc" if i == 0 else "mjpeg",
            "disposition": {"attached_pic": 1 if i == attached_at else 0},
        })
    return json.dumps({"streams": streams})


def _make_jpg(path, w=200, h=300, color="crimson"):
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(w, h)
    pm.fill(QColor(color))
    assert pm.save(str(path), "JPG"), f"存不出测试图: {path}"
    return path


# ==========================================================================
# 路径
# ==========================================================================
class TestPaths:
    def test_poster_file_uses_media_id_not_title(self, posters_dir):
        """片名有中文/空格/超长, 拿来当文件名太容易出事, 所以统一用 id"""
        assert P.poster_file(7) == posters_dir / "7.jpg"

    def test_poster_file_coerces_str_id(self, posters_dir):
        assert P.poster_file("12").name == "12.jpg"

    def test_real_posters_dir_is_under_project_data(self):
        """这条不打桩, 要验的就是真的实现: 必须在 <项目根>/data/posters 下"""
        d = P.posters_dir()
        assert d.parent.name == "data" and d.name == "posters"
        assert d.is_absolute()


# ==========================================================================
# ffprobe 找 attached_pic
# ==========================================================================
class TestFindAttachedPic:
    def test_finds_the_attached_pic_stream(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(stdout=_streams_json(attached_at=1)))
        assert P.find_attached_pic("ffprobe", str(video)) == 1

    def test_no_attached_pic_returns_none(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(stdout=_streams_json(attached_at=None)))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_missing_ffprobe_path(self, video):
        assert P.find_attached_pic(None, str(video)) is None
        assert P.find_attached_pic("", str(video)) is None

    def test_missing_video_file(self, tmp_path):
        assert P.find_attached_pic("ffprobe", str(tmp_path / "nope.mkv")) is None

    def test_ffprobe_nonzero_exit(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(returncode=1, stderr="Invalid data"))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_dirty_json_does_not_raise(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(stdout="{not json"))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_empty_stdout(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(stdout=""))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_timeout_is_swallowed(self, video, monkeypatch):
        """网络盘卡住时 ffmpeg 会超时, 不能让整批海报提取炸掉"""
        monkeypatch.setattr(P, "_run", _fake_run(
            raises=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_stream_entry_without_disposition(self, video, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(
            stdout=json.dumps({"streams": [{"index": 0}]})))
        assert P.find_attached_pic("ffprobe", str(video)) is None

    def test_disposition_value_as_string(self, video, monkeypatch):
        """有些 ffmpeg 版本会把它序列化成字符串 "1" 而不是数字 1"""
        monkeypatch.setattr(P, "_run", _fake_run(stdout=json.dumps({"streams": [
            {"index": 3, "disposition": {"attached_pic": "1"}}]})))
        assert P.find_attached_pic("ffprobe", str(video)) == 3


# ==========================================================================
# ffmpeg 抽帧
# ==========================================================================
class TestExtractStream:
    def test_success_writes_the_file(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "1.jpg"

        def _r(cmd, timeout):
            _make_jpg(P._tmp_out(out))    # ffmpeg 写的是临时名, 之后才原子改名
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(P, "_run", _r)
        assert P.extract_stream("ffmpeg", str(video), 1, out) is True
        assert out.is_file() and out.stat().st_size > 0
        assert not P._tmp_out(out).exists(), "临时文件该被清掉"

    def test_normalises_size_and_quality(self, video, tmp_path, monkeypatch):
        """存图要压到长边<=448 且保持比例 —— 横版剧照硬裁成竖版会切掉大半画面"""
        seen = []
        out = tmp_path / "posters" / "n.jpg"

        def _r(cmd, timeout):
            seen.append(cmd)
            _make_jpg(P._tmp_out(out))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(P, "_run", _r)
        P.extract_stream("ffmpeg", str(video), 1, out)
        joined = " ".join(seen[0])
        assert "-q:v" in joined and P._QV in seen[0]
        assert "force_original_aspect_ratio=decrease" in joined
        assert "320:448" not in joined and "crop" not in joined, \
            "不该强制竖版裁切"

    def test_zero_exit_but_no_file_counts_as_failure(self, video, tmp_path, monkeypatch):
        """ffmpeg 有时返回 0 却什么都没写, 所以要以文件为准"""
        out = tmp_path / "posters" / "2.jpg"
        monkeypatch.setattr(P, "_run", _fake_run(returncode=0))
        assert P.extract_stream("ffmpeg", str(video), 1, out) is False

    def test_empty_file_counts_as_failure(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "3.jpg"

        def _r(cmd, timeout):
            tmp = P._tmp_out(out)
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(P, "_run", _r)
        assert P.extract_stream("ffmpeg", str(video), 1, out) is False
        assert not out.exists(), "半截的图不能留在最终位置, 否则下次会被当成有效缓存"

    def test_nonzero_exit(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "4.jpg"
        monkeypatch.setattr(P, "_run", _fake_run(returncode=1, stderr="Stream map '0:1' matches no streams"))
        assert P.extract_stream("ffmpeg", str(video), 1, out) is False

    def test_missing_ffmpeg(self, video, tmp_path):
        assert P.extract_stream(None, str(video), 1, tmp_path / "x.jpg") is False

    def test_maps_the_requested_stream(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "5.jpg"
        seen = []

        def _r(cmd, timeout):
            seen.append(cmd)
            _make_jpg(P._tmp_out(out))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(P, "_run", _r)
        P.extract_stream("ffmpeg", str(video), 2, out)
        assert "-map" in seen[0] and "0:2" in seen[0], f"没按流号 map: {seen[0]}"

    def test_timeout_is_swallowed(self, video, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(
            raises=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)))
        assert P.extract_stream("ffmpeg", str(video), 1, tmp_path / "x.jpg") is False


# ==========================================================================
# 同目录手动海报
# ==========================================================================
class TestManualPoster:
    def test_finds_poster_jpg(self, video):
        want = _make_jpg(video.parent / "poster.jpg")
        assert P.find_manual_poster(str(video)) == want

    def test_prefers_poster_over_cover(self, video):
        _make_jpg(video.parent / "cover.png")
        want = _make_jpg(video.parent / "poster.jpg")
        assert P.find_manual_poster(str(video)) == want

    def test_falls_back_to_same_stem(self, video):
        want = _make_jpg(video.parent / f"{video.stem}.jpg")
        assert P.find_manual_poster(str(video)) == want

    def test_case_insensitive(self, video):
        want = _make_jpg(video.parent / "POSTER.JPG")
        assert P.find_manual_poster(str(video)) == want

    def test_none_when_nothing_there(self, video):
        assert P.find_manual_poster(str(video)) is None

    def test_ignores_non_image_files(self, video):
        (video.parent / "poster.txt").write_text("not an image")
        assert P.find_manual_poster(str(video)) is None

    def test_missing_dir(self, tmp_path):
        assert P.find_manual_poster(str(tmp_path / "nope" / "a.mkv")) is None

    def test_empty_path(self):
        assert P.find_manual_poster("") is None
        assert P.find_manual_poster(None) is None


# ==========================================================================
# ensure_poster 的优先级
# ==========================================================================
class TestEnsurePoster:
    def test_cached_wins_without_touching_ffmpeg(self, posters_dir, video, monkeypatch):
        cached = _make_jpg(posters_dir / "1.jpg")
        monkeypatch.setattr(P, "find_attached_pic",
                            lambda *a, **k: pytest.fail("有缓存还去跑 ffprobe"))
        path, source = P.ensure_poster(1, [str(video)], "ffprobe", "ffmpeg")
        assert source == "cached" and path == str(cached)

    def test_empty_cached_file_is_redone(self, posters_dir, video, monkeypatch):
        """上次抽到一半崩了会留下 0 字节文件, 不能当缓存用"""
        posters_dir.mkdir(parents=True, exist_ok=True)
        (posters_dir / "1.jpg").write_bytes(b"")
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        monkeypatch.setattr(P, "find_manual_poster", lambda *a, **k: None)
        assert P.ensure_poster(1, [str(video)], "ffprobe", "ffmpeg") == (None, None)

    def test_embedded_beats_frame_but_loses_to_manual(self, posters_dir, video, monkeypatch):
        """手动图 > 内嵌封面 > 截帧: 用户自己丢的图是最强的意图信号"""
        _make_jpg(video.parent / "poster.jpg")
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: 1)

        def fake_extract(ff, vp, idx, out):
            _make_jpg(P._tmp_out(out))                  # ffmpeg 写的是临时名
            return True

        monkeypatch.setattr(P, "extract_stream", fake_extract)
        path, source = P.ensure_poster(2, [str(video)], "ffprobe", "ffmpeg")
        assert source == "manual", f"同目录有 poster.jpg 就不该再用内嵌封面, 实得 {source}"
        assert path == str(posters_dir / "2.jpg")

    def test_embedded_used_when_no_manual_image(self, posters_dir, video, monkeypatch):
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: 1)
        monkeypatch.setattr(P, "extract_stream",
                            lambda ff, vp, idx, out: bool(_make_jpg(P._tmp_out(out))))
        path, source = P.ensure_poster(2, [str(video)], "ffprobe", "ffmpeg")
        assert source == "embedded" and path == str(posters_dir / "2.jpg")

    def test_falls_back_to_manual_when_no_attached_pic(self, posters_dir, video, monkeypatch):
        manual = _make_jpg(video.parent / "cover.png")
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        path, source = P.ensure_poster(3, [str(video)], "ffprobe", "ffmpeg")
        assert source == "manual" and path == str(posters_dir / "3.jpg")
        assert (posters_dir / "3.jpg").read_bytes() == manual.read_bytes(), "应该把图片复制过来"

    def test_manual_copy_leaves_no_empty_file_on_failure(self, posters_dir, video, monkeypatch):
        """复制失败不能留下 0 字节的 jpg, 否则下次会被当成有效缓存"""
        monkeypatch.setattr(P, "find_manual_poster",
                            lambda vp: posters_dir / "不存在的图.jpg")
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        assert P.ensure_poster(4, [str(video)], None, None) == (None, None)
        assert not (posters_dir / "4.jpg").exists()

    def test_none_when_nothing_available(self, posters_dir, video, monkeypatch):
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        assert P.ensure_poster(5, [str(video)], None, None) == (None, None)

    def test_no_files_at_all(self, posters_dir):
        assert P.ensure_poster(6, [], "ffprobe", "ffmpeg") == (None, None)
        assert P.ensure_poster(6, None, "ffprobe", "ffmpeg") == (None, None)

    def test_tries_every_file_of_a_multi_episode_work(self, posters_dir, tmp_path, monkeypatch):
        """动漫一季十几集, 第一集没封面不代表整部没有"""
        a = tmp_path / "E01.mkv"; a.write_bytes(b"x")
        b = tmp_path / "E02.mkv"; b.write_bytes(b"x")
        tried = []
        monkeypatch.setattr(P, "find_attached_pic",
                            lambda ff, vp: (tried.append(vp), None if vp.endswith("E01.mkv") else 1)[1])
        monkeypatch.setattr(P, "extract_stream",
                            lambda ff, vp, idx, out: bool(_make_jpg(out)))
        path, source = P.ensure_poster(7, [str(a), str(b)], "ffprobe", "ffmpeg")
        assert source == "embedded"
        assert tried == [str(a), str(b)], "应该按顺序把每个文件都试一遍"

    def test_force_ignores_the_cache(self, posters_dir, video, monkeypatch):
        _make_jpg(posters_dir / "8.jpg")
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        monkeypatch.setattr(P, "find_manual_poster", lambda *a, **k: None)
        assert P.ensure_poster(8, [str(video)], "ffprobe", "ffmpeg", force=True) == (None, None)


# ==========================================================================
# PosterWorker (直接调 run(), 不起真线程)
# ==========================================================================
@pytest.fixture
def db(tmp_path):
    init_database(str(tmp_path / "cinema.db"))
    with get_session() as s:
        for i, (title, nfiles) in enumerate([("有封面的电影", 1), ("动漫", 3), ("啥都没有", 1)], start=1):
            m = Media(id=i, title=title, media_type="movie", year=2020)
            s.add(m)
            s.flush()
            for j in range(nfiles):
                s.add(MediaFile(media_id=i, file_path=f"/fake/{i}_{j}.mkv",
                                file_name=f"{i}_{j}.mkv", duration=1000,
                                parse_status="success"))
        s.commit()
    return tmp_path


class TestPosterWorker:
    def _collect(self, worker):
        prog, errs = [], []
        worker.poster_progress.connect(lambda t, s: prog.append((t, s)))
        worker.poster_error.connect(lambda m: errs.append(m))
        done = []
        worker.poster_finished.connect(lambda c: done.append(c))
        return prog, errs, done

    def test_writes_poster_path_back_to_the_db(self, db, posters_dir, monkeypatch):
        monkeypatch.setattr(P, "find_attached_pic",
                            lambda ff, vp: 1 if vp.startswith("/fake/1_") else None)
        monkeypatch.setattr(P, "extract_stream",
                            lambda ff, vp, idx, out: bool(_make_jpg(out)))
        monkeypatch.setattr(P, "find_manual_poster", lambda vp: None)

        w = P.PosterWorker("ffprobe", "ffmpeg")
        prog, errs, done = self._collect(w)
        w.run()                                    # 直接调, 不起线程

        assert done and done[0]["embedded"] == 1, done
        with get_session() as s:
            got = s.get(Media, 1).poster_path
            assert got == str(posters_dir / "1.jpg")
            assert s.get(Media, 3).poster_path is None, "没封面的不该被写脏"

    def test_counts_every_outcome(self, db, posters_dir, monkeypatch):
        monkeypatch.setattr(P, "find_attached_pic",
                            lambda ff, vp: 1 if vp.startswith("/fake/1_") else None)
        monkeypatch.setattr(P, "extract_stream",
                            lambda ff, vp, idx, out: bool(_make_jpg(out)))
        monkeypatch.setattr(P, "find_manual_poster", lambda vp: None)
        w = P.PosterWorker("ffprobe", "ffmpeg")
        prog, errs, done = self._collect(w)
        w.run()
        c = done[0]
        assert c["embedded"] == 1 and c["none"] == 2 and c["failed"] == 0, c
        assert len(prog) == 3

    def test_one_broken_file_does_not_abort_the_batch(self, db, posters_dir, monkeypatch):
        def boom(ff, vp):
            if vp.startswith("/fake/1_"):
                raise OSError("网络盘断了")
            return None
        monkeypatch.setattr(P, "find_attached_pic", boom)
        monkeypatch.setattr(P, "find_manual_poster", lambda vp: None)
        w = P.PosterWorker("ffprobe", "ffmpeg")
        prog, errs, done = self._collect(w)
        w.run()
        assert done[0]["failed"] == 1, done
        assert len(prog) == 3, "第一个炸了也得把后面两个跑完"
        assert errs and "网络盘断了" in errs[0]

    def test_cancel_stops_early(self, db, posters_dir, monkeypatch):
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        monkeypatch.setattr(P, "find_manual_poster", lambda *a, **k: None)
        w = P.PosterWorker("ffprobe", "ffmpeg")
        prog, errs, done = self._collect(w)
        w.cancel()
        w.run()
        assert prog == [], "取消之后不该再处理任何一部"

    def test_signals_are_declared(self):
        for name in ("poster_started", "poster_progress",
                     "poster_finished", "poster_error"):
            assert hasattr(P.PosterWorker, name), f"缺信号 {name}"


# ==========================================================================
# MediaCard 显示海报
# ==========================================================================
class TestCardPoster:
    def test_shows_the_image_when_the_file_exists(self, app, tmp_path):
        jpg = _make_jpg(tmp_path / "9.jpg", 400, 600)
        card = MediaCard({"id": 9, "title": "有海报", "poster": str(jpg)})
        pm = card._poster.pixmap()
        assert pm is not None and not pm.isNull(), "有海报文件却没显示出来"
        assert (pm.width(), pm.height()) == (MediaCard.CARD_W, MediaCard.POSTER_H), \
            f"海报该填满 {MediaCard.CARD_W}x{MediaCard.POSTER_H}, 实得 {pm.width()}x{pm.height()}"

    def test_placeholder_when_no_poster(self, app):
        card = MediaCard({"id": 10, "title": "没海报"})
        assert card._poster.pixmap() is None or card._poster.pixmap().isNull()
        assert card._poster.text() == "没", "该退回首字母占位"

    def test_placeholder_when_path_is_stale(self, app, tmp_path):
        """DB 里存的路径可能已经失效(用户挪了文件), 不能显示成一块空白"""
        card = MediaCard({"id": 11, "title": "路径失效",
                          "poster": str(tmp_path / "gone.jpg")})
        assert card._poster.text() == "路"

    def test_placeholder_when_file_is_not_an_image(self, app, tmp_path):
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"definitely not a jpeg")
        card = MediaCard({"id": 12, "title": "坏图", "poster": str(bad)})
        assert card._poster.text() == "坏", "解码失败要退回占位"

    def test_empty_poster_string(self, app):
        card = MediaCard({"id": 13, "title": "空串", "poster": ""})
        assert card._poster.text() == "空"

    def test_vertical_cover_is_center_cropped_not_letterboxed(self, app, tmp_path):
        """竖版封面进横向海报区必须 Expanding + 居中裁, 不留左右黑边"""
        jpg = _make_jpg(tmp_path / "v.jpg", 200, 300)
        card = MediaCard({"id": 14, "title": "竖版", "poster": str(jpg)})
        pm = card._poster.pixmap()
        assert pm.width() == MediaCard.CARD_W, "左右留了黑边, 说明没用 Expanding"


# ==========================================================================
# DetailPage 显示海报
# ==========================================================================
class TestDetailPagePoster:
    """走真实 seam: init_database → DetailPage(media_id) 构造即加载, 与主窗口同一条链"""
    # 详情页海报区实测尺寸 (_show_info 里 setFixedSize(200, 280))
    W, H = 200, 280

    @pytest.fixture
    def seeded(self, tmp_path):
        """两部作品: id=1 有海报文件, id=2 没有; 各配一个视频文件"""
        init_database(str(tmp_path / "cinema.db"))
        jpg = _make_jpg(tmp_path / "posters" / "1.jpg", 400, 600)
        with get_session() as s:
            s.add(Media(id=1, title="有海报的电影", media_type="movie", year=2020,
                        poster_path=str(jpg)))
            s.add(Media(id=2, title="没海报的电影", media_type="movie", year=2021))
            s.flush()
            for i in (1, 2):
                f = tmp_path / f"movie{i}.mkv"
                f.write_bytes(b"\0" * 64)
                s.add(MediaFile(media_id=i, file_path=str(f), file_name=f.name,
                                duration=1000, parse_status="success"))
            s.commit()
        return tmp_path

    @staticmethod
    def _poster(page):
        lbl = page.findChild(QLabel, "posterPlaceholder")
        assert lbl is not None, "详情页找不到海报 QLabel"
        return lbl

    def test_shows_the_image_when_the_db_has_a_poster(self, app, seeded):
        page = DetailPage(1, stats_service=None)
        lbl = self._poster(page)
        pm = lbl.pixmap()
        assert pm is not None and not pm.isNull(), \
            "库里有海报文件, 详情页却还在用首字母占位"
        assert (pm.width(), pm.height()) == (self.W, self.H), \
            f"海报该填满 {self.W}x{self.H}, 实得 {pm.width()}x{pm.height()}"
        assert lbl.text() == "", "显示图片时不该再叠着占位文字"

    def test_placeholder_when_no_poster(self, app, seeded):
        page = DetailPage(2, stats_service=None)
        lbl = self._poster(page)
        assert lbl.pixmap() is None or lbl.pixmap().isNull()
        assert lbl.text() == "没海", "该退回标题前两字占位"

    def test_placeholder_when_path_is_stale(self, app, seeded):
        """DB 里的路径可能失效 (用户挪走/删了 data/posters), 不能显示成一块空白"""
        with get_session() as s:
            s.get(Media, 1).poster_path = str(seeded / "posters" / "gone.jpg")
            s.commit()
        page = DetailPage(1, stats_service=None)
        assert self._poster(page).text() == "有海"

    def test_placeholder_when_file_is_not_an_image(self, app, seeded):
        (seeded / "posters" / "1.jpg").write_bytes(b"definitely not a jpeg")
        page = DetailPage(1, stats_service=None)
        assert self._poster(page).text() == "有海", "解码失败要退回占位"

    def test_vertical_cover_is_center_cropped_not_letterboxed(self, app, seeded):
        """竖版 2:3 封面 → 200x280 海报区必须 Expanding + 居中裁, 不留黑边"""
        page = DetailPage(1, stats_service=None)
        pm = self._poster(page).pixmap()
        assert pm is not None and not pm.isNull()
        assert pm.width() == self.W, "左右留了黑边, 说明没用 Expanding"


# ==========================================================================
# 设置页入口
# ==========================================================================
class TestSettingsEntry:
    def test_button_and_status_exist(self, app):
        p = SettingsPage(config={"ffmpeg": {}}, ffprobe_path=None, mpv_path=None)
        assert p._poster_btn.text() == "提取内嵌封面"
        assert p._poster_status.text() == "海报: 未提取"
        assert p._poster_btn.toolTip(), "这个功能需要解释, 不能没 tooltip"

    def test_missing_ffmpeg_reports_instead_of_crashing(self, app, monkeypatch):
        monkeypatch.setattr("app.ui.pages.settings_page.detect_ffmpeg", lambda *_a, **_k: None)
        p = SettingsPage(config={"ffmpeg": {}}, ffprobe_path="ffprobe", mpv_path=None)
        p._start_posters()
        assert "ffmpeg" in p._poster_status.text()
        assert p._poster_btn.isEnabled(), "报错之后按钮不该一直卡死"

    def test_starts_the_worker_when_ffmpeg_is_found(self, app, monkeypatch):
        started = []
        monkeypatch.setattr("app.ui.pages.settings_page.detect_ffmpeg",
                            lambda *_a, **_k: "C:/ffmpeg.exe")

        # 一个最小的假 worker: 只验证"按钮被禁用 + worker 被 start"
        class _W:
            def __init__(self, *a, **k):
                self.started = False
                self.poster_progress = _Sig()
                self.poster_finished = _Sig()
                self.poster_error = _Sig()

            def isRunning(self):
                return False

            def start(self):
                self.started = True
                started.append(self)

        monkeypatch.setattr("app.ui.pages.settings_page.PosterWorker", _W)
        p = SettingsPage(config={"ffmpeg": {}}, ffprobe_path="ffprobe", mpv_path=None)
        p._start_posters()
        assert started, "没启动 worker"
        assert p._poster_btn.isEnabled() is False, "跑起来之后按钮该禁用, 否则能连点"
        assert p._poster_btn.text() == "正在提取..."

    def test_finished_handler_restores_the_button(self, app):
        p = SettingsPage(config={"ffmpeg": {}}, ffprobe_path=None, mpv_path=None)
        p._poster_btn.setEnabled(False)
        p._poster_btn.setText("正在提取...")
        p._on_poster_finished({"embedded": 12, "manual": 2, "cached": 0,
                               "none": 1, "failed": 0})
        assert p._poster_btn.isEnabled() and p._poster_btn.text() == "提取内嵌封面"
        assert "12" in p._poster_status.text() and "内嵌" in p._poster_status.text()


class _Sig:
    """最小的信号替身: 只需要支持 connect"""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


# ==========================================================================
# ffmpeg 探测
# ==========================================================================
class TestDetectFfmpeg:
    def test_uses_custom_path(self, tmp_path, monkeypatch):
        exe = tmp_path / "ffmpeg.exe"
        exe.write_bytes(b"x")
        assert PD.detect_ffmpeg(str(exe)) == str(exe)

    def test_falls_back_to_ffprobe_sibling(self, tmp_path, monkeypatch):
        """ffprobe 同目录下的 ffmpeg.exe 也要能找到 (PATH 里不一定有)"""
        probe = tmp_path / "ffprobe.exe"
        probe.write_bytes(b"x")
        sib = tmp_path / "ffmpeg.exe"
        sib.write_bytes(b"x")
        monkeypatch.setattr(PD, "_detect", lambda tool, custom=None: None)
        monkeypatch.setattr(PD, "detect_ffprobe", lambda *_a, **_k: str(probe))
        assert PD.detect_ffmpeg() == str(sib)

    def test_none_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PD, "_detect", lambda tool, custom=None: None)
        monkeypatch.setattr(PD, "detect_ffprobe", lambda *_a, **_k: None)
        assert PD.detect_ffmpeg() is None


# ==========================================================================
# 自动截帧兜底
# ==========================================================================
def _make_solid(path, color, fmt="PNG", w=64, h=64):
    """造一张纯色图。亮度测试用 PNG: JPEG 有损, 阈值卡不准"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(w, h)
    pm.fill(QColor(color) if not isinstance(color, QColor) else color)
    assert pm.save(str(path), fmt), f"存不出测试图: {path}"
    return path


class TestBrightness:
    def test_black_is_dark(self, tmp_path):
        p = _make_solid(tmp_path / "black.png", "black")
        assert P.average_brightness(p) < 5
        assert P.is_dark_image(p) is True

    def test_white_is_not_dark(self, tmp_path):
        p = _make_solid(tmp_path / "white.png", "white")
        assert P.average_brightness(p) > 250
        assert P.is_dark_image(p) is False

    def test_threshold_is_honoured(self, tmp_path):
        """亮度 20 该判黑、30 不该 —— 阈值 25 必须真的被用上"""
        dark = _make_solid(tmp_path / "d.png", QColor(20, 20, 20))
        ok = _make_solid(tmp_path / "o.png", QColor(30, 30, 30))
        assert P.is_dark_image(dark, threshold=25) is True
        assert P.is_dark_image(ok, threshold=25) is False

    def test_missing_file_counts_as_dark(self, tmp_path):
        """读不出来就算不合格: 截帧"成功"但图坏了同样不能当封面"""
        assert P.average_brightness(tmp_path / "nope.png") is None
        assert P.is_dark_image(tmp_path / "nope.png") is True

    def test_corrupt_file_counts_as_dark(self, tmp_path):
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image at all")
        assert P.is_dark_image(bad) is True

    def test_mid_grey_is_not_dark(self, tmp_path):
        p = _make_solid(tmp_path / "grey.png", QColor(128, 128, 128))
        assert 118 < P.average_brightness(p) < 138
        assert P.is_dark_image(p) is False


class TestClampTimestamps:
    def test_typical_movie(self):
        assert P._clamp_timestamps(1000.0) == (100.0, 300.0, 500.0)

    def test_no_duration_means_no_timestamps(self):
        assert P._clamp_timestamps(None) == ()
        assert P._clamp_timestamps(0) == ()
        assert P._clamp_timestamps(-5) == ()

    def test_never_past_the_end(self):
        """贴着片尾截会失败, 所以要留安全边距"""
        for d in (2.0, 5.0, 60.0, 7200.0):
            got = P._clamp_timestamps(d)
            assert got, f"时长 {d} 一个时间点都没给"
            for ts in got:
                assert 0 <= ts <= d - P._SAFETY_MARGIN + 1e-6, (d, ts)

    def test_very_short_video_stays_inside_the_safe_window(self):
        """再短的片子, 时间点也全被夹进 [0, duration - 安全边距] 且严格递增去重"""
        got = P._clamp_timestamps(1.5)
        assert got, "再短的片子也该至少给一个时间点"
        assert all(0 <= ts <= 1.5 - P._SAFETY_MARGIN + 1e-6 for ts in got), got
        assert got == tuple(sorted(set(got))), got

    def test_strictly_increasing_and_deduped(self):
        for d in (1.5, 3.0, 10.0, 90.0, 1000.0):
            got = list(P._clamp_timestamps(d))
            assert got == sorted(set(got)), f"时长 {d} 的时间点没去重/没递增: {got}"


class TestExtractFrame:
    def _fake_ok(self, monkeypatch, out, seen=None):
        def _r(cmd, timeout):
            if seen is not None:
                seen.append(cmd)
            _make_jpg(P._tmp_out(out))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(P, "_run", _r)

    def test_success_and_tmp_cleaned(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "1.jpg"
        self._fake_ok(monkeypatch, out)
        assert P.extract_frame("ffmpeg", str(video), 123.4, out) is True
        assert out.is_file() and out.stat().st_size > 0
        assert not P._tmp_out(out).exists(), "临时文件该被清掉"

    def test_seek_flag_comes_before_input(self, video, tmp_path, monkeypatch):
        """-ss 必须在 -i 前面才是快速定位; 放后面要解码整段, 4K 长片差几十倍"""
        out = tmp_path / "posters" / "2.jpg"
        seen = []
        self._fake_ok(monkeypatch, out, seen)
        P.extract_frame("ffmpeg", str(video), 100.0, out)
        cmd = seen[0]
        assert cmd.index("-ss") < cmd.index("-i"), f"-ss 跑到 -i 后面了: {cmd}"
        assert "100.000" in cmd

    def test_normalises_size_and_quality(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "3.jpg"
        seen = []
        self._fake_ok(monkeypatch, out, seen)
        P.extract_frame("ffmpeg", str(video), 10.0, out)
        joined = " ".join(seen[0])
        assert "-q:v" in joined and P._QV in seen[0]
        assert "force_original_aspect_ratio=decrease" in joined

    def test_zero_exit_but_no_file_is_failure(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "4.jpg"
        monkeypatch.setattr(P, "_run", _fake_run(returncode=0))
        assert P.extract_frame("ffmpeg", str(video), 10.0, out) is False

    def test_missing_ffmpeg(self, video, tmp_path):
        assert P.extract_frame(None, str(video), 10.0, tmp_path / "x.jpg") is False

    def test_timeout_is_swallowed(self, video, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_run", _fake_run(
            raises=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)))
        assert P.extract_frame("ffmpeg", str(video), 10.0, tmp_path / "x.jpg") is False

    def test_negative_timestamp_is_clamped(self, video, tmp_path, monkeypatch):
        out = tmp_path / "posters" / "5.jpg"
        seen = []
        self._fake_ok(monkeypatch, out, seen)
        P.extract_frame("ffmpeg", str(video), -50.0, out)
        assert "0.000" in seen[0]


class TestFrameFallback:
    """ensure_poster 的最后一档: 没有手动图也没有内嵌封面时自动截帧"""

    def _nothing_else(self, monkeypatch):
        monkeypatch.setattr(P, "find_manual_poster", lambda vp: None)
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)

    def test_frame_used_as_last_resort(self, posters_dir, video, monkeypatch):
        self._nothing_else(monkeypatch)
        monkeypatch.setattr(P, "extract_frame",
                            lambda ff, vp, ts, out: bool(_make_jpg(out, 120, 180)))
        path, source = P.ensure_poster(20, [str(video)], "ffprobe", "ffmpeg",
                                       durations=[1000.0])
        assert source == "frame@10%" and path == str(posters_dir / "20.jpg")

    def test_dark_frame_falls_through_to_the_next_ratio(self, posters_dir, video, monkeypatch):
        """片头 10% 常是黑场或片商 logo, 必须换下一个时间点再试"""
        self._nothing_else(monkeypatch)
        tried = []

        def fake(ff, vp, ts, out):
            tried.append(ts)
            _make_solid(out, "black" if ts < 200 else QColor(140, 90, 60), fmt="JPG")
            return True

        monkeypatch.setattr(P, "extract_frame", fake)
        path, source = P.ensure_poster(21, [str(video)], "ffprobe", "ffmpeg",
                                       durations=[1000.0])
        assert source == "frame@30%", f"10% 那张是黑的该退到 30%; 实得 {source}, 试过 {tried}"

    def test_all_dark_returns_none_and_leaves_no_scraps(self, posters_dir, video, monkeypatch):
        self._nothing_else(monkeypatch)
        monkeypatch.setattr(P, "extract_frame",
                            lambda ff, vp, ts, out: bool(_make_solid(out, "black", fmt="JPG")))
        assert P.ensure_poster(22, [str(video)], "ffprobe", "ffmpeg",
                               durations=[1000.0]) == (None, None)
        assert not (posters_dir / "22.jpg").exists(), "全黑就别留下残次品"

    def test_uses_the_db_duration_without_probing(self, posters_dir, video, monkeypatch):
        """库里已有 MediaFile.duration, 不该再跑一次 ffprobe"""
        self._nothing_else(monkeypatch)
        monkeypatch.setattr(P, "get_duration",
                            lambda *a, **k: pytest.fail("给了 durations 还去 probe"))
        monkeypatch.setattr(P, "extract_frame",
                            lambda ff, vp, ts, out: bool(_make_jpg(out)))
        assert P.ensure_poster(23, [str(video)], "ffprobe", "ffmpeg",
                               durations=[600.0])[1] == "frame@10%"

    def test_probes_duration_when_the_db_has_none(self, posters_dir, video, monkeypatch):
        self._nothing_else(monkeypatch)
        probed = []
        monkeypatch.setattr(P, "get_duration",
                            lambda ff, vp: (probed.append(vp), 900.0)[1])
        monkeypatch.setattr(P, "extract_frame",
                            lambda ff, vp, ts, out: bool(_make_jpg(out)))
        assert P.ensure_poster(24, [str(video)], "ffprobe", "ffmpeg",
                               durations=[None])[1] == "frame@10%"
        assert probed == [str(video)]

    def test_no_ffmpeg_means_no_frames(self, posters_dir, video, monkeypatch):
        self._nothing_else(monkeypatch)
        monkeypatch.setattr(P, "extract_frame",
                            lambda *a, **k: pytest.fail("没有 ffmpeg 不该截帧"))
        assert P.ensure_poster(25, [str(video)], "ffprobe", None,
                               durations=[1000.0]) == (None, None)

    def test_no_duration_means_no_frames(self, posters_dir, video, monkeypatch):
        """拿不到时长就不能瞎猜时间点"""
        self._nothing_else(monkeypatch)
        monkeypatch.setattr(P, "get_duration", lambda *a, **k: None)
        monkeypatch.setattr(P, "extract_frame",
                            lambda *a, **k: pytest.fail("没有时长不该截帧"))
        assert P.ensure_poster(26, [str(video)], "ffprobe", "ffmpeg",
                               durations=[None]) == (None, None)

    def test_multi_episode_tries_each_file(self, posters_dir, tmp_path, monkeypatch):
        """动漫一季十几集: 第一集全黑不代表整部都没法用"""
        self._nothing_else(monkeypatch)
        a = tmp_path / "E01.mkv"; a.write_bytes(b"x")
        b = tmp_path / "E02.mkv"; b.write_bytes(b"x")
        seen = []

        def fake(ff, vp, ts, out):
            seen.append(vp)
            if vp.endswith("E01.mkv"):
                _make_solid(out, "black", fmt="JPG")
            else:
                _make_jpg(out, 120, 180)
            return True

        monkeypatch.setattr(P, "extract_frame", fake)
        source = P.ensure_poster(27, [str(a), str(b)], "ffprobe", "ffmpeg",
                                 durations=[1000.0, 1000.0])[1]
        assert source == "frame@10%"
        assert str(b) in seen, "第一集不行就该试第二集"


class TestWorkerFrameCounting:
    def test_frame_source_is_counted_under_frame(self, db, posters_dir, monkeypatch):
        monkeypatch.setattr(P, "find_manual_poster", lambda vp: None)
        monkeypatch.setattr(P, "find_attached_pic", lambda *a, **k: None)
        monkeypatch.setattr(P, "extract_frame",
                            lambda ff, vp, ts, out: bool(_make_jpg(out)))
        w = P.PosterWorker("ffprobe", "ffmpeg")
        prog, errs, done = [], [], []
        w.poster_progress.connect(lambda t, s: prog.append((t, s)))
        w.poster_finished.connect(lambda c: done.append(c))
        w.run()
        c = done[0]
        assert c["frame"] == 3 and c["embedded"] == 0 and c["none"] == 0, c
        assert all(s.startswith("frame@") for _, s in prog), prog
        with get_session() as s:
            assert s.get(Media, 2).poster_path == str(posters_dir / "2.jpg")
