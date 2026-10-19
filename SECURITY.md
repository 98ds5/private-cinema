# 安全问题 / Security Policy

这是个本地桌面应用（PySide6 + SQLite），不联网、不起服务，攻击面很小，但有两块是真敏感的：

1. **媒体库数据库**（`data/cinema.db`）：里面有你扫描过的目录路径和文件名
2. **备份文件**：媒体库维护功能会在清空/恢复时生成数据库与海报备份

## 怎么报

**别开公开 issue。** 用 GitHub 的私密漏洞报告：仓库页 → Security → Report a vulnerability（Advisories），只有我能看到。

报告里请写：影响哪个版本（源码 or 哪个 Release 的 exe）、复现步骤、影响（数据丢失 / 路径泄露 / 命令注入）。

## 不在范围内

- 需要本机已有管理员权限才能做的事
- 你自己把 `config.json` / 数据库发给别人导致的问题
- 第三方依赖（PySide6 / SQLAlchemy / ffmpeg / mpv）自身的漏洞——报给上游，我这边跟着升级

## 我的处理

业余时间看 issue，安全报告我会优先处理；修好后在 Release notes 里写清楚影响范围，不公开利用细节。
