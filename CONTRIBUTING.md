# 参与 / Contributing

这是个人自用的课设项目，不是框架，也不打算做通用化。**Issue 欢迎，PR 谨慎**——先说清楚我认什么、不认什么，省掉双方的时间。

## 报 Bug

开 issue 时请带上：

1. **运行方式**：源码跑（`python main.py`）还是打包版（`dist/` 里的 exe）
2. **环境**：Windows 版本 / Python 版本 / 能否找到 ffprobe、mpv（设置页底部会显示检测到的路径）
3. **复现步骤**：点了哪里、点了第几次
4. **期望 vs 实际**
5. **日志**：跑 `python main.py` 时的控制台输出（有报错就贴全文，别截一段）

## 提 PR

接受这种：

- 一改一事的 bug fix
- 补一条回归测试
- 错别字 / 文档纠正

不接受这种：

- 大范围重构、换框架、加抽象层（这个仓库的大小不需要）
- 顺手把整个文件重新格式化（diff 会淹掉真正的改动）
- 新增第三方依赖（现在只有 PySide6 / SQLAlchemy / pypinyin 三个，能不加就不加）
- 只改样式不改行为、但没说明为什么

## 本地开发

```powershell
conda create -n cinema python=3.11 -y
conda activate cinema
pip install -r requirements.txt

# mpv + ffmpeg + ffprobe：跑一次就行
.\scripts\download-tools.ps1

# 全量测试（438 条，约 55 秒）
pytest -q
```

## 这个仓库的测试惯例

改行为 = 加测试。这个仓库里几乎每条测试的 docstring 都能对应一次真实事故（`tests/test_parser.py` 开头就写着 cp936 解码那次 12/24 个文件被误判的经过）。所以：

- 修 bug 时，先把 bug 写成一条会红的测试，再改代码
- 测试里别 mock 掉你要验证的那一层——`tests/test_posters.py` 里 `ensure_poster` 的四级兜底是真的跑子进程边界，不是 fake 出来的
- 断言行为的**结果**（入库后的字段、返回的路径），别断言内部调用次数

## 代码风格

- 4 空格缩进，不引 linter 配置（没装这层）
- 中文注释，讲**为什么**这么写，不讲这行代码做了什么
- 新增模块前先看 `app/` 下有没有同类职责的地方可以放
