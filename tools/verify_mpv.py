"""[verify] 真 mpv 端到端验证: 管道连接、进度解析、落库、续播定位、退出发 finished。
选动漫单集一次覆盖 episodes/media 两条进度写入路径; 测前快照数据库, 测后恢复。
"""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_session, init_database
from app.models.tables import Episode, Media, MediaFile
from app.services.player import _MpvEngine
from app.utils.config_utils import load_config     # 必须真实加载, 传空 dict 会缺键
from app.utils.path_detector import detect_mpv

TAG = "[verify]"


def log(*a):
    print(TAG, *a, flush=True)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def snapshot(media_id, episode_id):
    with get_session() as s:
        m = s.query(Media).filter(Media.id == media_id).one()
        e = s.query(Episode).filter(Episode.id == episode_id).one()
        return {
            "media_status": m.status, "media_pos": m.watched_position,
            "media_dur": m.watched_duration, "media_at": m.last_watched_at,
            "ep_status": e.status, "ep_pos": e.watched_position,
            "ep_at": e.last_watched_at,
        }


def restore(media_id, episode_id, snap):
    with get_session() as s:
        m = s.query(Media).filter(Media.id == media_id).one()
        m.status, m.watched_position = snap["media_status"], snap["media_pos"]
        m.watched_duration, m.last_watched_at = snap["media_dur"], snap["media_at"]
        e = s.query(Episode).filter(Episode.id == episode_id).one()
        e.status, e.watched_position = snap["ep_status"], snap["ep_pos"]
        e.last_watched_at = snap["ep_at"]
        s.commit()


def main():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    cfg = load_config()
    init_database(cfg["system"]["db_path"])
    mpv = detect_mpv(cfg.get("player", {}).get("mpv_path", "auto"))
    log("mpv =", mpv)
    if not mpv:
        log("FATAL 没探测到 mpv")
        return 1

    # 挑一个动漫单集文件
    with get_session() as s:
        row = (
            s.query(MediaFile.id, MediaFile.file_path, MediaFile.media_id,
                    MediaFile.episode_id, Media.title)
            .join(Media, MediaFile.media_id == Media.id)
            .filter(MediaFile.episode_id.isnot(None))
            .first()
        )
        if row is None:
            log("FATAL 库里没有动漫单集文件")
            return 1
        fid, fpath, media_id, ep_id, title = row
    log(f"目标: {title} / media_id={media_id} episode_id={ep_id}")
    log(f"文件: {fpath}")

    before = snapshot(media_id, ep_id)
    log("播放前:", before)

    test_cfg = dict(cfg)
    test_cfg["player"] = dict(cfg.get("player", {}))
    test_cfg["player"]["progress_interval"] = 2       # 生产 10s, 验证等不起

    # ---- 第 1 轮: 从头播 ----
    eng = _MpvEngine(test_cfg, mpv_path=mpv)
    seen, errors, finished = [], [], []
    eng.playback_position.connect(lambda p, d: seen.append((p, d)))
    eng.playback_error.connect(errors.append)
    eng.playback_finished.connect(finished.append)

    log("--- 第 1 轮: 从头播放 (mpv 窗口会真的弹出来几秒) ---")
    eng.play(fpath, media_id, episode_id=ep_id, start_pos=0)
    log("管道名:", eng._pipe_name)
    pump(app, 11)
    log(f"收到 {len(seen)} 次进度: {seen}")
    log("错误:", errors or "无")
    eng.stop()
    pump(app, 2)
    after1 = snapshot(media_id, ep_id)
    log("stop() 后落库:", after1)

    ok1 = bool(seen) and seen[-1][1] > 0 and seen[-1][0] > 0
    # start_pos=0 时首个 time-pos 必须接近 0: mpv 的 save-position-on-quit 缓存可能偷偷续播。
    ok0 = bool(seen) and seen[0][0] < 30
    ok2 = (after1["ep_pos"] or 0) > 0 and (after1["media_pos"] or 0) > 0
    ok3 = after1["ep_status"] in ("watching", "watched")
    ok4 = after1["media_status"] in ("watching", "watched")
    log(f"[0] 从头播不偷续播    : {'PASS' if ok0 else 'FAIL'} "
        f"(首个 time-pos={seen[0][0] if seen else 'N/A'}, 期望 <30)")
    log(f"[1] 进度解析出来      : {'PASS' if ok1 else 'FAIL'}")
    log(f"[2] episodes+media 落库: {'PASS' if ok2 else 'FAIL'}")
    log(f"[3] episode 状态回写   : {'PASS' if ok3 else 'FAIL'} ({after1['ep_status']})")
    log(f"[4] media 聚合回写     : {'PASS' if ok4 else 'FAIL'} ({after1['media_status']})")

    # ---- 第 2 轮: 续播 ----
    resume_from = max(60, (after1["ep_pos"] or 0))
    log(f"--- 第 2 轮: 续播 start_pos={resume_from} ---")
    eng2 = _MpvEngine(test_cfg, mpv_path=mpv)
    seen2, errors2 = [], []
    eng2.playback_position.connect(lambda p, d: seen2.append((p, d)))
    eng2.playback_error.connect(errors2.append)
    eng2.play(fpath, media_id, episode_id=ep_id, start_pos=resume_from)
    pump(app, 9)
    log(f"收到 {len(seen2)} 次进度: {seen2}")
    log("错误:", errors2 or "无")
    eng2.stop()
    pump(app, 2)

    # mpv --start=N 之后 time-pos 应从 N 附近开始, 而不是 0
    ok5 = bool(seen2) and seen2[0][0] >= resume_from - 5
    log(f"[5] 续播定位生效      : {'PASS' if ok5 else 'FAIL'} "
        f"(首个 time-pos={seen2[0][0] if seen2 else 'N/A'}, 期望≈{resume_from})")

    # ---- 第 3 轮: 用户直接关窗 → playback_finished ----
    log("--- 第 3 轮: 模拟用户直接关掉 mpv 窗口 ---")
    eng3 = _MpvEngine(test_cfg, mpv_path=mpv)
    fin3, seen3 = [], []
    eng3.playback_finished.connect(fin3.append)
    eng3.playback_position.connect(lambda p, d: seen3.append((p, d)))
    eng3.play(fpath, media_id, episode_id=ep_id, start_pos=0)
    pump(app, 6)
    if eng3._process is not None:
        eng3._process.kill()          # 等价于用户点窗口关闭
    pump(app, 3)
    ok6 = fin3 == [media_id]
    log(f"[6] 进程退出发 finished: {'PASS' if ok6 else 'FAIL'} (收到 {fin3})")
    after3 = snapshot(media_id, ep_id)
    # start_pos=0 且只播了几秒, 落库进度必须是小数字, 以此排除 mpv 自行从缓存续播。
    ok7 = 0 < (after3["ep_pos"] or 0) < 60
    log(f"[7] 退出时进度已落盘   : {'PASS' if ok7 else 'FAIL'} "
        f"({after3['ep_pos']}, 期望 0<x<60)")
    ok8 = bool(seen3) and seen3[0][0] < 30
    log(f"[8] 第3轮也没偷续播    : {'PASS' if ok8 else 'FAIL'} "
        f"(进度序列 {seen3})")

    restore(media_id, ep_id, before)
    log("数据库已恢复原状:", snapshot(media_id, ep_id))

    checks = [ok0, ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8]
    passed = sum(checks)
    log(f"===== 真机端到端: {passed}/{len(checks)} PASS =====")
    return 0 if passed == len(checks) else 2


if __name__ == "__main__":
    sys.exit(main())
