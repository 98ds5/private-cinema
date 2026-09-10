"""
数据库初始化与会话管理。
SQLite 单文件零配置, 够用即可; 开 WAL 允许读写并发; 全局唯一 Engine, Session 用完即关。
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
    """初始化数据库: 建目录、创建 Engine、开 WAL、建表并建 Session 工厂"""
    global _engine, _SessionFactory

    # 确保 data/ 目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 创建 Engine (惰性连接, 真正连库在第一次操作时)
    _engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        pool_pre_ping=True,
    )

    # 每次新建连接时执行 PRAGMA (WAL 允许读写并发, foreign_keys 打开外键约束)
    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # 必须先 import 模型注册表, 否则 Base.metadata 是空的、建不出表
    from app.models import tables  # noqa: F401
    Base.metadata.create_all(_engine)

    # Session 工厂: 每次从连接池取
    _SessionFactory = sessionmaker(bind=_engine)


def get_session() -> Session:
    """获取一个新会话; 用完记得 close, 推荐 with get_session() as s:"""
    if _SessionFactory is None:
        raise RuntimeError("数据库未初始化, 请先调用 init_database()")
    return _SessionFactory()