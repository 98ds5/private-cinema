# 私人影院 — 本地影视资源管理

用 PySide6 写的本地影视库桌面应用：扫描目录入库、解析视频参数、管理海报、
双引擎播放与续播、数据统计。课设项目。

技术栈: Python 3.10+ / PySide6 / SQLAlchemy 2.0 + SQLite / ffprobe / mpv

## 功能

- **影视库**: 添加目录后递归扫描, 发行组命名自动解析出标题/年份/季集号, 动漫和电影同一流程
- **参数解析**: ffprobe 提取分辨率、帧率、编码、HDR 类型、音轨/字幕语言, 详情页展示
- **海报**: 完全离线的四级兜底 —— 手动图 > 内嵌封面 > ffmpeg 截帧 > 标题文字占位
- **播放**: MPV 引擎 (命名管道 JSON IPC, 每 10 秒轮询进度, 按集记录、可续播)
  或 VividPlayer 引擎 (协议启动, 适合 HDR 原盘), 设置页切换
- **统计与排序**: 在看列表、总数统计; 名称/年份/添加时间/最近观看四种排序 (中文按拼音序)
- **界面**: 两套设计语言 x 明暗双主题, 网格随窗口宽度重排, 小窗口下布局不塌陷

## 快速开始

```bash
# 环境 (需 Python 3.10+)
conda create -n cinema python=3.11 -y
conda activate cinema
# 或 python -m venv venv && venv\Scripts\activate

# 依赖
pip install -r requirements.txt

# 运行
python main.py
```

ffprobe / ffmpeg / mpv 不在包内: 程序启动时自动探测 PATH 与常见安装位置,
也可在 `config.json` 里写死路径 (见 `app/utils/path_detector.py`)。

## 播放引擎

| 引擎 | 方式 | 进度追踪 | 续播 | 适合 |
|------|------|---------|------|------|
| MPV | 子进程 + JSON IPC 命名管道 | 每 10 秒轮询 | 支持, 按集记忆 | 日常, 需要精确管理 |
| VividPlayer | 协议启动 | 无 | 不支持 | HDR 原盘 |

引擎选择存在 `config.json` 的 `player.engine` 字段, 默认 MPV。

## 目录结构

```
私人影院/
├── main.py                  # 程序入口
├── requirements.txt
├── config.json              # 首次运行自动生成
├── app/
│   ├── database.py          # 数据库初始化 (WAL) + 会话管理
│   ├── models/tables.py     # 5 张表: libraries/media/media_files/seasons/episodes
│   ├── services/            # scanner(扫描) parser(解析) player(播放) posters(海报) stats(统计)
│   ├── storage/local.py     # 本地文件存储 (预留扩展)
│   ├── ui/                  # main_window + pages(页面) + widgets(卡片/播放条) + styles(QSS)
│   └── utils/               # name_parser(文件名解析) quality(画质分级) config_utils 等
├── tools/                   # 离屏冒烟 / mpv 链路验证等开发脚本
├── tests/                   # pytest 回归 (~380 条)
└── data/                    # 运行时自动创建 (数据库 + 海报缓存)
```
