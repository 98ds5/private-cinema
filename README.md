# 私人影院 (私人影视资源管理系统) — V1.0

面向个人的本地影视资源管理: **扫描入库 → FFprobe 参数解析 → MPV 播放/续播 → 数据统计**。
课设项目。技术栈: Python 3.10+ / PySide6 / SQLAlchemy 2.0 / SQLite / ffprobe / mpv。

需求与排期文档见同目录 `需求分析和初步项目文档.txt` 与 `1.0.txt`。

## 快速开始

```bash
# 1. 环境 (本机 base Python 是 3.8.8, 版本不够, 必须新建)
conda create -n cinema python=3.11 -y
conda activate cinema
# 或 python -m venv venv && venv\Scripts\activate

# 2. 依赖
pip install -r requirements.txt

# 3. 外部工具
#    ffprobe 本机已装 ✓ (C:\Users\...\Gyan.FFmpeg\...\ffprobe.exe)
#    mpv     本机已装 ✓ (D:\<mpv>\mpv-lazy\mpv.exe)
#    VividPlayer 本机已装 ✓ (微软商店版)

# 4. 运行 (双击可运行导航壳; 业务功能未实现会看到 TODO 占位)
python main.py
```

## 播放引擎

支持两种播放器, 在设置页切换:

| 引擎 | 方式 | 进度追踪 | 续播定位 | 推荐场景 |
|------|------|---------|---------|---------|
| **MPV** | 子进程 + JSON IPC 命名管道 | 精确 (每10秒轮询) | ✅ 支持 | 标清/高清, 需要精确管理 |
| **VividPlayer** | 协议启动 | 无（纯播放，不追踪） | ❌ 不支持 | HDR 大片，不关心进度 |

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
│   ├── services/            # scanner(扫描) / parser(解析) / player(播放) / stats(统计)
│   ├── storage/local.py     # 本地文件存储 (预留扩展)
│   ├── ui/                  # main_window + pages + widgets + styles(QSS深色)
│   └── utils/               # name_parser(文件名解析) / format_utils / path_detector / constants
├── tests/test_name_parser.py   # 文件名解析单测
├── data/cinema.db           # 运行时自动创建
└── docs/  …                 # (文档 txt 暂放项目根, 答辩前整理进 docs/)
```

## 当前进度 (框架阶段)

- [x] 项目骨架 + 目录结构 + 可运行导航壳
- [x] 数据库 5 张表 (models/tables.py)
- [x] 工具类: name_parser / format_utils / path_detector / constants
- [x] 文件名解析器单元测试
- [ ] 扫描入库 scanner.py      ← 排期 第3-4天
- [ ] FFprobe 解析 parser.py   ← 第5-6天
- [ ] UI 列表/详情页            ← 第7-8天
- [ ] MPV 播放 player.py        ← 第9-10天
- [ ] 统计 stats.py + 首页      ← 第11天
- [ ] 设置 settings_page        ← 第12天
- [ ] 联调/测试/文档/打包       ← 第13-14天

## 答辩随身卡

高频问题与回答要点见 `1.0.txt` 第七节 (SQLite 为什么、Media/MediaFile 为什么分开、
怎么识别电影动漫、怎么保证扫描不重复、MPV 怎么取进度、进度保存策略、ffprobe 怎么调、
为什么 QThread、数据量大怎么办)。