"""
数据库初始化与会话管理

要点 (答辩常问):
  - SQLite 单文件零配置, 个人使用性能够, 所以不用 MySQL
  - WAL 模式: 允许读写并发, 扫描入库的同时 UI 还能查数据
  - 全局唯一 Engine + SessionFactory, 每个 Session 用完即关
"""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

_engine = None
_SessionFactory = None


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类"""
    pass


def init_database(db_path: str = "./data/cinema.db") -> None:
    """初始化数据库: 建目录 → 创建 Engine → 开 WAL → 建表 → 建 Session 工厂"""
    global _engine, _SessionFactory

    # 确保 data/ 目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 创建 Engine (惰性: 真正连库发生在第一次操作时)
    _engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        pool_pre_ping=True,
    )

    # SQLite 优化: 每次新建连接时执行 PRAGMA
    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # 导入模型模块以注册所有表 (必须有 import, 否则 Base.metadata 是空的)
    from app.models import tables  # noqa: F401
    Base.metadata.create_all(_engine)

    # Session 工厂: 不绑定具体连接, 每次用从池子里取
    _SessionFactory = sessionmaker(bind=_engine)


def get_session() -> Session:
    """获取一个新会话; 用完记得 close, 推荐 with get_session() as s:"""
    if _SessionFactory is None:
        raise RuntimeError("数据库未初始化, 请先调用 init_database()")
    return _SessionFactory()