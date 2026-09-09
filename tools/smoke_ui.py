"""[smoke] 离屏冒烟: 确认 player.py 重写没破坏主窗口装配与引擎热切换。

按 HANDOFF §7 的方法论: 模型看不到图, 一律运行时内省。
- WA_DontShowOnScreen 避免在用户屏幕上闪窗口
- 必须 load_config(), 绝不 MainWindow({}, ...) (HANDOFF §4.1 事故)
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_database
from app.services.player import PlayerService, _MpvEngine, _VividPlayerEngine
from app.ui.main_window import MainWindow
from app.utils.config_utils import load_config
from app.utils.path_detector import detect_ffprobe, detect_mpv

TAG = "[smoke]"


def log(*a):
    print(TAG, *a, flush=True)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    cfg = load_config()
    log("config keys:", sorted(cfg.keys()))
    init_database(cfg["system"]["db_path"])

    ffprobe = detect_ffprobe(cfg.get("ffmpeg", {}).get("ffprobe_path", "auto"))
    mpv = detect_mpv(cfg.get("player", {}).get("mpv_path", "auto"))
    log("ffprobe:", ffprobe)
    log("mpv    :", mpv)

    w = MainWindow(cfg, ffprobe_path=ffprobe, mpv_path=mpv)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1280, 800)
    w.show()
    for _ in range(4):
        app.processEvents()

    ok = []
    ps = w.player_service
    ok.append(("引擎类型 = mpv", ps.engine_type == "mpv"))
    ok.append(("引擎实例 = _MpvEngine", isinstance(ps._engine, _MpvEngine)))
    ok.append(("轮询间隔 = 10s", ps._engine.interval == 10.0))
    ok.append(("未播放时 is_playing False", ps.is_playing is False))
    ok.append(("状态条默认隐藏", w._now_bar.isHidden()))
    ok.append(("页面数 = 6", w.pages.count() == 6))
    log("devicePixelRatioF =", w.devicePixelRatioF(), " geometry =", w.geometry())

    # 不用 receivers(): HANDOFF §7 记过, PySide6 的 QObject.receivers()
    # 不接受 SignalInstance。改成行为验证 —— 直接发信号看主线程槽有没有跑。
    eng = ps._engine
    eng._playing = True
    eng._media_id = None          # _save_progress 对 None 直接 return, 不会脏库
    eng._progress_ready.emit(123, 456)
    for _ in range(3):
        app.processEvents()       # queued connection, 必须泵事件
    ok.append(("内部 _progress_ready → 主线程 _on_progress 生效",
               eng._position == 123 and eng._duration == 456))
    eng._playing = False
    eng._position = eng._duration = 0

    # 非法/停止态下这些内部信号必须是安全的 no-op (不能崩、不能弹框)
    eng._ipc_failed.emit("smoke-test-should-be-swallowed")
    eng._process_exited.emit()
    for _ in range(3):
        app.processEvents()
    ok.append(("停止态下 _ipc_failed/_process_exited 安全 no-op",
               eng.is_playing is False))

    # 热切换 (HANDOFF §4.10)
    ok.append(("切到 vividplayer", ps.set_engine("vividplayer") is True))
    ok.append(("实例已换成 _VividPlayerEngine",
               isinstance(ps._engine, _VividPlayerEngine)))
    ok.append(("切回 mpv", ps.set_engine("mpv") is True))
    ok.append(("实例换回 _MpvEngine", isinstance(ps._engine, _MpvEngine)))
    ok.append(("非法引擎被拒", ps.set_engine("vlc") is False))

    # 热切换后重建的引擎同样要能收进度 (_build_engine 走的是同一个 __init__)
    eng2 = ps._engine
    eng2._playing = True
    eng2._media_id = None
    eng2._progress_ready.emit(7, 8)
    for _ in range(3):
        app.processEvents()
    ok.append(("热切换后新引擎的进度通道也通", 
               eng2._position == 7 and eng2._duration == 8))
    eng2._playing = False

    # 侧边栏与页面标题
    nav = w.findChildren(QWidget)
    log("顶层子 widget 数:", len(nav))

    w.close()
    for _ in range(3):
        app.processEvents()

    bad = [name for name, res in ok if not res]
    for name, res in ok:
        log(f"  {'PASS' if res else 'FAIL'}  {name}")
    log(f"===== 离屏冒烟: {len(ok)-len(bad)}/{len(ok)} PASS =====")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
