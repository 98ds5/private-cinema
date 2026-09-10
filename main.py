"""
私人影院 — 程序入口。
加载配置、初始化数据库、检测 ffprobe/mpv, 然后打开主窗口。
"""
import sys

from PySide6.QtWidgets import QApplication

from app.database import init_database
from app.utils.config_utils import load_config
from app.utils.path_detector import detect_ffprobe, detect_mpv
from app.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("私人影院")

    config = load_config()
    init_database(config.get("system", {}).get("db_path", "./data/cinema.db"))

    ffprobe_path = detect_ffprobe(config.get("ffmpeg", {}).get("ffprobe_path"))
    mpv_path = detect_mpv(config.get("player", {}).get("mpv_path"))

    window = MainWindow(config, ffprobe_path, mpv_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
