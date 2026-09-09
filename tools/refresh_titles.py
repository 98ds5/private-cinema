"""
维护工具: 用当前解析器重算存量作品标题

为什么需要:
  解析器 (app/utils/name_parser.py) 只在扫描「新文件」时推导标题,
  已入库的记录不会自动重算。所以每次改进 clean_title 之后,
  库里的旧脏标题会一直留着 (本项目实测: 'Pacific Rim Eng Fre Ger…'
  在算法修好后仍然留在库里)。

安全设计:
  - 默认 dry-run, 只报告差异, 必须显式加 --apply 才写库
  - 干净标题重算结果不变 (tests/test_name_parser.py 已保证中文标题原样保留),
    所以重复执行是幂等的
  - 改名后若与其他作品 (title, media_type) 撞车, 拒绝写入并报告,
    合并策略需要人工决定

用法:
  cd 私人影院
  cinema_env\\python.exe tools\\refresh_titles.py            # 只看差异
  cinema_env\\python.exe tools\\refresh_titles.py --apply    # 实际写入
"""
import argparse
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)                      # config.json / ./data 都是相对路径
sys.path.insert(0, _ROOT)

from app.database import init_database, get_session          # noqa: E402
from app.models.tables import Media, MediaFile, Library       # noqa: E402
from app.utils.config_utils import load_config                # noqa: E402
from app.utils.name_parser import parse_filename              # noqa: E402


def find_root(file_path: str, libs: list):
    """按路径前缀匹配文件所属的媒体库根目录 (取最长匹配)"""
    best = None
    for _lid, lpath in libs:
        try:
            if Path(file_path).is_relative_to(Path(lpath)):
                if best is None or len(lpath) > len(best):
                    best = lpath
        except (OSError, ValueError):
            continue
    return best


def collect():
    """读出媒体库、作品、以及每部作品的第一个文件路径"""
    with get_session() as s:
        libs = [(l.id, l.path) for l in s.query(Library).all()]
        medias = [(m.id, m.title, m.media_type) for m in s.query(Media).all()]
        first_files = {}
        for mid, _t, _ty in medias:
            row = (s.query(MediaFile.file_path)
                   .filter(MediaFile.media_id == mid)
                   .order_by(MediaFile.file_name.asc())
                   .first())
            if row:
                first_files[mid] = row[0]
    return libs, medias, first_files


def compute_changes(libs, medias, first_files):
    """对比现有标题与重算标题, 返回差异列表"""
    changes = []
    for mid, old_title, mtype in medias:
        fp = first_files.get(mid)
        if not fp:
            print("  跳过 id=%s %r: 无关联文件" % (mid, old_title))
            continue
        root = find_root(fp, libs)
        new_title = parse_filename(fp, str(Path(fp).parent), root_dir=root).title
        if new_title and new_title != old_title:
            changes.append((mid, old_title, new_title, mtype))
    return changes


def find_collisions(changes):
    """改名后是否与其他作品重名 (同 title + 同 media_type)"""
    with get_session() as s:
        existing = {(t, ty): mid for t, ty, mid
                    in s.query(Media.title, Media.media_type, Media.id).all()}
    return [(mid, existing[(new, mtype)], new, mtype)
            for mid, _old, new, mtype in changes
            if existing.get((new, mtype)) not in (None, mid)]


def main():
    ap = argparse.ArgumentParser(description="重算存量作品标题")
    ap.add_argument("--apply", action="store_true",
                    help="实际写入数据库 (默认只 dry-run 报告差异)")
    args = ap.parse_args()

    config = load_config()
    init_database(config.get("system", {}).get("db_path", "./data/cinema.db"))

    libs, medias, first_files = collect()
    print("媒体库 -> %s" % (libs,))
    print("作品数 -> %d" % len(medias))
    print("")

    changes = compute_changes(libs, medias, first_files)
    print("=== 需要更新的标题 (%d 条) ===" % len(changes))
    for mid, old, new, mtype in changes:
        print("  id=%s [%s]" % (mid, mtype))
        print("    旧 -> %r" % old)
        print("    新 -> %r" % new)

    if not changes:
        print("\n无差异, 不需要修改。")
        return 0

    collisions = find_collisions(changes)
    if collisions:
        print("\n!!! 检测到改名碰撞, 已拒绝写入:")
        for mid, other, new, mtype in collisions:
            print("    id=%s 与 id=%s 都会变成 %r [%s]" % (mid, other, new, mtype))
        print("需要先决定合并策略 (保留哪条 / 文件如何归并), 请人工处理。")
        return 2

    if not args.apply:
        print("\n[dry-run] 未写入任何数据。确认无误后加 --apply 执行。")
        return 0

    with get_session() as s:
        for mid, _old, new, _mtype in changes:
            m = s.query(Media).filter(Media.id == mid).first()
            if m:
                m.title = new
        s.commit()
    print("\n已更新 %d 条。" % len(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
