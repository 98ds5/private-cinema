"""
配置读写 —— 全项目唯一来源

设计要点:
  DEFAULT_CONFIG  唯一默认值定义, main.py 与 styles.py 都从这里取, 不再各自复制。
  deep_merge      递归合并, 只覆盖 patch 中出现的键。
  load_config     以默认值为基底合并磁盘配置 → 残缺文件也能得到完整结构。
  save_config     以「默认值 ← 磁盘 ← 本次修改」三层合并后写盘 → 自愈。

为什么 save_config 必须三层合并:
  调用方可能只持有一个局部字典 (例如仅 {"ui": {"version": "echo"}})。
  若直接整份覆盖, 会把 system / libraries / ffmpeg 等键全部抹掉,
  下次启动读取 db_path 时 KeyError 崩溃 (本项目真实踩过)。
"""
import json
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("./config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "libraries": [],                      # 媒体库目录, 在设置页添加
    "player": {
        "engine": "mpv",                  # 播放引擎: "mpv" / "vividplayer"
        "mpv_path": "auto",               # auto = 自动检测
        "hw_decode": "auto",              # auto / yes / no
        "progress_interval": 10,          # 进度保存间隔(秒)
    },
    "system": {
        "db_path": "./data/cinema.db",
        "theme": "dark",                  # dark / light
    },
    "ffmpeg": {
        "ffprobe_path": "auto",
    },
    "ui": {
        "version": "prism",               # 设计版本: prism / echo
    },
}


def deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """把 patch 递归合并进 base (原地修改并返回 base); 只覆盖 patch 中出现的键。"""
    for key, value in (patch or {}).items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _defaults() -> Dict[str, Any]:
    """返回默认配置的深拷贝, 避免调用方污染模块级常量。"""
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """
    加载配置。始终以默认值为基底, 再合并磁盘内容:
    即使 config.json 残缺或损坏, 也一定返回结构完整的配置。
    """
    config = _defaults()

    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                deep_merge(config, loaded)
                return config
    except (json.JSONDecodeError, OSError):
        pass  # 损坏 → 回退默认值

    # 文件不存在或已损坏: 写出一份完整默认配置
    save_config(config, path)
    return config


def save_config(config: Dict[str, Any], path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """
    持久化配置。三层合并: 默认值 ← 磁盘现有 ← 本次修改。
    返回合并后的完整配置 (调用方可直接用它替换自己手里的局部字典)。
    """
    merged = _defaults()

    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            if isinstance(on_disk, dict):
                deep_merge(merged, on_disk)
    except (json.JSONDecodeError, OSError):
        pass

    deep_merge(merged, config or {})

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=4)
    except OSError:
        pass

    return merged
