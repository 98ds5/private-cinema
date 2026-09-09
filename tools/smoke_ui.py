"""[smoke] 离屏冒烟: 确认主窗口装配、引擎热切换、详情页小窗布局都没坏。

按 HANDOFF §7 的方法论: 模型看不到图, 一律运行时内省。
- WA_DontShowOnScreen 避免在用户屏幕上闪窗口
- 必须 load_config(), 绝不 MainWindow({}, ...) (HANDOFF §4.1 事故)

⚠️ 2026-09-09 教训: 本脚本上一版在"引擎热切换"两项上报了 PASS, 而用户手点时
   切换是**完全失效**的。原因是我直接调 ps.set_engine(), 绕过了设置页
   「先写 config 再调 set_engine」的真实顺序。seam 不对的测试就是假绿。
   现在改成走 SettingsPage 的下拉框。
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QPushButton, QScrollArea, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_session, init_database
from app.models.tables import Media
from app.services.player import PlayerService, _MpvEngine, _VividPlayerEngine
from app.ui.main_window import MainWindow
from app.ui import main_window as mw_mod
from app.ui.pages import settings_page as settings_mod
from app.ui.pages.detail_page import DetailPage
from app.ui.pages.settings_page import SettingsPage
from app.utils.config_utils import load_config
from app.utils.path_detector import detect_ffprobe, detect_mpv

TAG = "[smoke]"


class _NoModal:
    """
    顶掉 main_window 模块里的 QMessageBox。

    ⚠️ 离屏脚本绝不能弹模态框: QMessageBox.warning 内部是 exec(), 会**永久阻塞**
    事件循环, 脚本挂死 (2026-09-09 实际发生过: pwsh 300s 超时), 而且在用户屏幕上
    弹出真窗口。触发路径是 set_engine("非法值") → playback_error →
    main_window._on_playback_error → QMessageBox.warning。

    生产代码弹这个框是对的(HANDOFF: 播放失败绝不静默), 要改的是诊断工具。
    只替换 main_window 模块内的那个名字, 不动全局 PySide6。
    """
    calls = []

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _NoModal.calls.append((title, str(text)))
        log(f"      [拦截模态框] {title}: {text}")
        return 0x00000400          # QMessageBox.StandardButton.Ok

    @staticmethod
    def information(parent, title, text, *a, **k):
        _NoModal.calls.append((title, str(text)))
        return 0x00000400

    @staticmethod
    def question(*a, **k):
        return 0x00000400


def log(*a):
    print(TAG, *a, flush=True)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    mw_mod.QMessageBox = _NoModal      # 必须在建 MainWindow 之前顶掉, 见 _NoModal 文档
    cfg = load_config()
    log("config keys:", sorted(cfg.keys()))

    # 冒烟要验的是 MPV 引擎的装配, 但用户可能已把 config 切到 vividplayer
    # (2026-09-09 就撞上了: _VividPlayerEngine 没有 interval 属性 → AttributeError)。
    # 只在内存里改, 不落盘: save_config 后面会被打桩。
    log(f"config 里的 engine = {cfg.get('player', {}).get('engine')} → 冒烟强制用 mpv")
    cfg.setdefault("player", {})["engine"] = "mpv"

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

    # 热切换 (HANDOFF §4.10) —— 必须走 SettingsPage 的真实 seam, 见文件头教训
    real_cfg = w.config
    saved = []
    settings_mod.save_config = lambda c: saved.append(     # 打桩: 不写真实 config.json
        c.get("player", {}).get("engine"))
    sp = SettingsPage(config=real_cfg, ffprobe_path=None, mpv_path=None,
                      player_service=ps)
    start_engine = ps.engine_type
    target_idx = 1 if start_engine == "mpv" else 0
    sp._engine_combo.setCurrentIndex(target_idx)
    for _ in range(3):
        app.processEvents()
    want = _VividPlayerEngine if target_idx == 1 else _MpvEngine
    ok.append((f"下拉框 {start_engine} → {sp._engine_combo.currentData()}: 引擎真的重建了",
               isinstance(ps._engine, want)))
    ok.append(("设置页给出了反馈文案", "已切换" in sp._scan_status.text()))
    ok.append(("切换已写回 config",
               bool(saved) and saved[-1] == sp._engine_combo.currentData()))

    back_idx = 0 if target_idx == 1 else 1
    sp._engine_combo.setCurrentIndex(back_idx)
    for _ in range(3):
        app.processEvents()
    ok.append(("切回原引擎", ps.engine_type == start_engine))

    # 非法引擎: 必须被拒, 而且必须**发信号**而不是静默 —— 但那个信号在
    # MainWindow 里接的是模态框, 所以本脚本开头已经把 mw_mod.QMessageBox 顶掉了。
    _NoModal.calls.clear()
    ok.append(("非法引擎被拒", ps.set_engine("vlc") is False))
    ok.append(("非法引擎会报错而不是静默 (且没弹模态框卡死脚本)",
               any("vlc" in t for _, t in _NoModal.calls)))
    sp.deleteLater()

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

    # 详情页在小窗口下不能被压扁。
    # 2026-09-09 用户手点报的「没有选集」「播放键不对」「全屏正常小窗口有问题」
    # 是同一个根因: 本页原先没有 QScrollArea, minimumSizeHint 1002x996 而小窗只给
    # 798x524, Qt 硬压之下 10 个选集行按钮全部塌成高度 0。
    with get_session() as s:
        a = s.query(Media).filter(Media.media_type == "anime").first()
        anime_id = a.id if a else None
    if anime_id:
        w.resize(949, 612)
        for _ in range(3):
            app.processEvents()
        dp = DetailPage(anime_id, stats_service=w.stats_service)
        w.pages.addWidget(dp)
        w.pages.setCurrentWidget(dp)
        for _ in range(4):
            app.processEvents()

        sc = dp.findChild(QScrollArea)
        btns = dp.findChildren(QPushButton)
        zero = [b.text() for b in btns if b.height() == 0]
        plays = [b for b in btns if b.text() == "▶ 播放"]
        main_btn = [b for b in btns if b.objectName() == "primaryAction"]
        host = sc.widget() if sc else None

        ok.append(("小窗下详情页有滚动区", sc is not None))
        ok.append((f"小窗下 {len(btns)} 个按钮无一被压成 0 高度", not zero))
        ok.append((f"10 集选集行都在且可点 (实得 {len(plays)})", len(plays) == 10))
        ok.append(("主播放按钮不低于自身 sizeHint",
                   bool(main_btn) and
                   main_btn[0].height() >= main_btn[0].sizeHint().height() - 1))
        ok.append(("内容能横向缩进视口 (横向滚动条是关的)",
                   host is not None and
                   host.width() <= sc.viewport().width() + 1))
        ok.append(("宿主没被压到 minimumSizeHint 以下",
                   host is not None and
                   host.height() >= host.minimumSizeHint().height() - 1))
        ok.append(("动漫页标题写的是选集", "选集" in dp._files_title.text()))
        log(f"      小窗实测: 页面 {dp.width()}x{dp.height()}, 宿主 "
            f"{host.width() if host else '?'}x{host.height() if host else '?'}, "
            f"滚动范围 {sc.verticalScrollBar().maximum() if sc else '?'}")

        w.pages.removeWidget(dp)
        dp.deleteLater()
        for _ in range(3):
            app.processEvents()
    else:
        ok.append(("库里没有动漫, 跳过详情页小窗检查", True))

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
