"""
媒体库维护: 备份 / 清空 / 恢复。

设计原则(破坏性操作的护栏):
  1. 清空前**自动备份**, 备份失败就不清 —— 宁可不清, 也不能把数据清没了兜不住;
  2. 只删数据库记录与海报缓存, **绝不动硬盘上的视频文件**;
  3. 媒体库目录配置(libraries)保留, 清空后可直接重新扫描;
  4. 备份里**同时含数据库与海报**, 恢复时一起还原(只还原库的话, 卡片会因为没有图而退回占位)。

备份目录结构:
    data/backup/20260918-173000/cinema.db
    data/backup/20260918-173000/posters/*.jpg
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.database import current_db_path, dispose_engine, get_session, init_database
from app.models.tables import Episode, Library, Media, MediaFile, Season
from app.services.posters import posters_dir as default_posters_dir

BACKUP_DIR_NAME = "backup"
DB_FILE_NAME = "cinema.db"
POSTERS_SUBDIR = "posters"


# ----------------------------------------------------------------------
# 路径解析 (允许调用方显式传路径, 便于测试指向临时目录)
# ----------------------------------------------------------------------
def _resolve_db(db: Optional[str] = None) -> Path:
    return Path(db) if db else Path(current_db_path())


def _resolve_posters(posters: Optional[Path] = None) -> Path:
    return Path(posters) if posters else default_posters_dir()


def backups_root(db: Optional[str] = None) -> Path:
    """备份根目录: 与数据库同级的 backup/"""
    return _resolve_db(db).parent / BACKUP_DIR_NAME


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sqlite_backup(src: Path, dst: Path) -> None:
    """用 SQLite 自己的 backup API 复制库 —— 比拷文件安全(会正确处理 WAL)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _count_posters(posters: Path) -> int:
    return len(list(posters.glob("*.jpg"))) if posters.is_dir() else 0


# ----------------------------------------------------------------------
# 查询 / 备份
# ----------------------------------------------------------------------
def snapshot(db: Optional[str] = None, posters: Optional[Path] = None) -> dict:
    """当前库的规模, 给 UI 显示与确认框用。"""
    pdir = _resolve_posters(posters)
    with get_session() as s:
        return {
            "media": s.query(Media).count(),
            "files": s.query(MediaFile).count(),
            "episodes": s.query(Episode).count(),
            "libraries": s.query(Library).count(),
            "posters": _count_posters(pdir),
        }


def backup_now(db: Optional[str] = None, posters: Optional[Path] = None,
               stamp: Optional[str] = None) -> Path:
    """把数据库与海报复制进 data/backup/<时间戳>/, 返回备份目录。"""
    dest = backups_root(db) / (stamp or _stamp())
    dest.mkdir(parents=True, exist_ok=True)
    _sqlite_backup(_resolve_db(db), dest / DB_FILE_NAME)

    pdir = _resolve_posters(posters)
    if pdir.is_dir():
        shutil.copytree(pdir, dest / POSTERS_SUBDIR, dirs_exist_ok=True)
    return dest


def list_backups(db: Optional[str] = None) -> list:
    """已有备份, 新的在前 (按目录名的时间戳排序)。"""
    root = backups_root(db)
    if not root.is_dir():
        return []
    items = [d for d in root.iterdir()
             if d.is_dir() and (d / DB_FILE_NAME).is_file()]
    return sorted(items, key=lambda d: d.name, reverse=True)


# ----------------------------------------------------------------------
# 清空 / 恢复
# ----------------------------------------------------------------------
def clear_library(db: Optional[str] = None, posters: Optional[Path] = None) -> dict:
    """
    清空媒体库: 删掉作品/文件/季集记录与海报缓存, 保留媒体库目录配置。
    清空前一定先备份; 备份失败会直接抛异常, 不做任何删除。
    """
    before = snapshot(db, posters)
    backup = backup_now(db, posters)          # 备份失败 -> 抛异常, 后面不会执行
    pdir = _resolve_posters(posters)

    with get_session() as s:
        # 先删子表再删主表 (bulk delete 不走 ORM 级联, 但库里有 ON DELETE CASCADE)
        s.query(MediaFile).delete(synchronize_session=False)
        s.query(Episode).delete(synchronize_session=False)
        s.query(Season).delete(synchronize_session=False)
        s.query(Media).delete(synchronize_session=False)
        s.commit()

    removed_posters = 0
    if pdir.is_dir():
        for f in pdir.glob("*.jpg"):
            try:
                f.unlink()
                removed_posters += 1
            except OSError:
                pass

    return {
        "backup": str(backup),
        "media": before["media"],
        "files": before["files"],
        "episodes": before["episodes"],
        "posters": removed_posters,
        "libraries": before["libraries"],
    }


def restore_latest(db: Optional[str] = None, posters: Optional[Path] = None) -> dict:
    """从最近的备份恢复数据库与海报 (覆盖当前库)。"""
    backups = list_backups(db)
    if not backups:
        raise RuntimeError("没有可用备份")

    latest = backups[0]
    db_file = _resolve_db(db)
    pdir = _resolve_posters(posters)

    # 必须先释放连接池里的句柄, 否则 Windows 上库文件被占用、写不进去
    dispose_engine()
    for sidecar in (f"{db_file}-wal", f"{db_file}-shm"):
        try:
            Path(sidecar).unlink()
        except OSError:
            pass

    _sqlite_backup(latest / DB_FILE_NAME, db_file)

    # 海报一起还原: 先清空当前缓存, 再从备份拷回
    src_posters = latest / POSTERS_SUBDIR
    if src_posters.is_dir():
        if pdir.is_dir():
            for f in pdir.glob("*.jpg"):
                try:
                    f.unlink()
                except OSError:
                    pass
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_posters, pdir, dirs_exist_ok=True)

    init_database(str(db_file))
    after = snapshot(db, posters)
    return {"backup": str(latest), **after}
