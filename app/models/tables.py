"""
数据模型 — 5 张表:
  libraries    媒体库目录 (用户添加的扫描根目录: 电影库/动漫库/混合)
  media        影视作品, 电影与动漫统一, 用 media_type 区分
  media_files  视频文件, 一部作品可有多个版本 (一对多)
  seasons      动漫季度 (仅动漫使用)
  episodes     动漫单集, 每集对应一个视频文件

为什么 Media 与 MediaFile 分开: 同一作品常有多个文件 (多版本/多语言音轨),
合成一张表会让标题、年份等重复存储难维护。分开后 Media 存"是什么作品",
MediaFile 存"哪个文件", 一对多关联。观看进度: 电影存 media, 动漫存 episodes,
作品整体状态由各集聚合得出。
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text,
    DateTime, Enum, BigInteger, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Library(Base):
    """媒体库目录配置"""
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)          # 如 "电影库"
    path = Column(String(1024), nullable=False, unique=True)
    media_type = Column(                                # movie / anime / mixed
        Enum("movie", "anime", "mixed", name="lib_type"),
        default="mixed",
    )
    scan_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Media(Base):
    """影视作品 (电影 + 动漫统一模型)"""
    __tablename__ = "media"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False, index=True)
    original_title = Column(String(255))                # 原始名 (日文/英文)
    media_type = Column(                                # movie / anime
        Enum("movie", "anime", name="media_type"),
        nullable=False, index=True,
    )
    year = Column(Integer, index=True)
    rating = Column(Float, default=0.0)                 # 0-10, 默认 0
    director = Column(String(255))
    synopsis = Column(Text)
    poster_path = Column(String(512))

    # ---- 观看状态 (电影的进度直接存这里) ----
    status = Column(
        Enum("unwatched", "watching", "watched", name="watch_status"),
        default="unwatched", index=True,
    )
    watched_position = Column(Integer, default=0)       # 秒
    watched_duration = Column(Integer, default=0)       # 秒 (视频总时长)
    last_watched_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关系: 一部作品对应多个文件 / 多个季度
    files = relationship("MediaFile", back_populates="media",
                         cascade="all, delete-orphan")
    seasons = relationship("Season", back_populates="media",
                           cascade="all, delete-orphan")


class MediaFile(Base):
    """视频文件记录 (含 FFprobe 解析结果)"""
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_id = Column(Integer, ForeignKey("media.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    # 动漫单集关联: 电影文件该字段为 NULL
    episode_id = Column(Integer,
                        ForeignKey("episodes.id", ondelete="SET NULL"),
                        nullable=True, index=True)

    file_path = Column(String(1024), nullable=False, unique=True)  # 去重靠它
    file_name = Column(String(512), nullable=False)
    file_size = Column(BigInteger, default=0)

    # ---- FFprobe 解析结果 (parse_status 为 success 才有意义) ----
    duration = Column(Integer, default=0)               # 秒
    width = Column(Integer)
    height = Column(Integer)
    frame_rate = Column(Float)
    video_codec = Column(String(64))                    # hevc / h264 / av1 ...
    hdr_type = Column(String(32), default="SDR")        # SDR / HDR10 / HDR10+ / Dolby Vision / HLG
    audio_codec = Column(String(64))
    audio_tracks = Column(Text)                         # JSON 字符串: 音轨列表
    subtitle_tracks = Column(Text)                      # JSON 字符串: 字幕列表
    container_format = Column(String(32))               # matroska / mov,mp4 ...

    parse_status = Column(
        Enum("pending", "parsing", "success", "failed", name="parse_status"),
        default="pending",
    )
    parse_error = Column(Text)
    parsed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    media = relationship("Media", back_populates="files")
    episode = relationship("Episode", back_populates="file")


class Season(Base):
    """动漫季度"""
    __tablename__ = "seasons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_id = Column(Integer, ForeignKey("media.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    season_number = Column(Integer, nullable=False)
    title = Column(String(255))                         # 如 "第1季"

    media = relationship("Media", back_populates="seasons")
    episodes = relationship("Episode", back_populates="season",
                            order_by="Episode.episode_number",
                            cascade="all, delete-orphan")


class Episode(Base):
    """动漫单集"""
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    season_id = Column(Integer, ForeignKey("seasons.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    episode_number = Column(Integer, nullable=False)
    title = Column(String(255))

    # ---- 观看状态 (动漫的进度存这里) ----
    status = Column(
        Enum("unwatched", "watching", "watched", name="ep_status"),
        default="unwatched",
    )
    watched_position = Column(Integer, default=0)
    last_watched_at = Column(DateTime)

    season = relationship("Season", back_populates="episodes")
    file = relationship("MediaFile", back_populates="episode", uselist=False)