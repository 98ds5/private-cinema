"""
本地文件系统操作封装 (StorageBackend 的本地实现)

之所以单独抽一层: 需求文档 6.3 预留了扩展能力,
后续支持 NAS/网盘时, 只要换一个实现, 扫描服务不用改。
"""
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class FileInfo:
    """扫描发现的一个视频文件"""
    path: str
    name: str
    size: int            # 字节
    modified_at: float   # 修改时间戳


class LocalStorage:
    """本地文件系统操作"""

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def get_info(self, path: str) -> Optional[FileInfo]:
        p = Path(path)
        if not p.exists():
            return None
        stat = p.stat()
        return FileInfo(
            path=str(p.resolve()),
            name=p.name,
            size=stat.st_size,
            modified_at=stat.st_mtime,
        )

    def list_videos(self, directory: str,
                    extensions: List[str]) -> List[FileInfo]:
        """
        递归列出目录下所有指定扩展名的视频文件。
        跳过隐藏文件; 单个文件/目录无权限时跳过不中断。
        """
        results = []
        dir_path = Path(directory)
        if not dir_path.is_dir():
            return results

        ext_set = {e if e.startswith(".") else f".{e}" for e in extensions}

        try:
            for item in dir_path.rglob("*"):
                if not item.is_file():
                    continue
                if item.name.startswith("."):
                    continue
                if item.suffix.lower() not in ext_set:
                    continue
                try:
                    stat = item.stat()
                    results.append(FileInfo(
                        path=str(item.resolve()),
                        name=item.name,
                        size=stat.st_size,
                        modified_at=stat.st_mtime,
                    ))
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        return results

    def open_path(self, path: str) -> str:
        """返回规范化绝对路径 (传给播放器用)"""
        return str(Path(path).resolve())