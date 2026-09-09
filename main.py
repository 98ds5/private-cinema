"""
私人影院 — 程序入口

启动流程:
  1. 加载 config.json (残缺/损坏会自动补齐为完整结构)
  2. 初始化 SQLite 数据库 (自动建表)
  3. 检测 ffprobe / mpv 路径 (找不到返回 None, 后续在设置页可手动指定)
  4. 打开主窗口

运行:
  cd 私人影院
  python main.py
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
