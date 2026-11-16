# 批量解压控制台（batch-extractor）

零第三方依赖的批量解压工具：递归扫描目录（或手动指定文件），自动识别格式、自动归组分卷、自动尝试密码，结束后输出「成功 / 失败 / 跳过」按原因码分组的明细汇总。支持 zip / tar.gz / tar.bz2 / tar.xz / gz / bz2 / xz / lzma，装 7-Zip 后自动支持 7z / rar / 数字分卷（`7z.001`~）/ `.partN.rar` / AES 加密 ZIP。

提供三种运行形态，共享同一核心引擎：

| 形态 | 启动方式 | 适用 |
| --- | --- | --- |
| **桌面窗口（推荐）** | 双击 `批量解压控制台.exe`（免安装，约 14 MB）或 `python batch_gui.py` | 日常使用，直接弹窗操作，无需浏览器 |
| Web 页面 | 双击 `start.bat` 或 `python launch.py` | 浏览器操作页面（仅监听 127.0.0.1） |
| 命令行 CLI | `python batch_extract.py --input-dir … --output …` | 脚本集成、定时任务 |

> 📖 **文档**：[使用文档](docs/使用文档.md)（安装 / 配置 / 操作示例 / FAQ） · [程序文档](docs/程序文档.md)（架构 / 模块 / 核心逻辑 / 依赖） · [架构与工程知识](docs/架构与工程知识.md)（设计决策记录） · [工作日志](docs/工作日志.md)

## 核心特性

- **两阶段安全落盘**：先解压到隔离暂存区，再统一搬运到输出目录——失败不留半成品，所有格式共用同一套冲突检测、覆盖策略与路径安全逻辑
- **分卷自动归组**：`7z.001~004`、`xxx.7z.001`、`xxx.part1.rar` 整组算 1 个包，只解首卷；缺卷整组报 `missing_volume` 并列出缺失编号
- **密码策略**：单密码 / 候选密码列表 / 密码本文件，加密包逐个尝试；缺密码 = `password_required`，密码全不对 = `wrong_password`，均不影响其他包继续
- **路径安全**：包内绝对路径 / `..` / 盘符 / 符号链接 / Windows 保留名一律拦截（`unsafe_path`）
- **统一汇总**：stdout / API / 界面共用同一 schema（`batch-extract-summary/v1`），原因码一致（corrupt / password_required / wrong_password / missing_volume / unsupported / unsafe_path / conflict / io_error / invalid_input / external_failed / empty）
- **密码安全**：明文仅存任务内存，日志 / 汇总 / 本地存储中绝无明文；汇总 `auth` 只给数量
- **格式识别**：扩展名优先 + 文件头魔数兜底，无扩展名压缩包也能识别

## 目录结构

```
WorkBuddy/
├── batch_extract.py        # 核心引擎 + CLI + 本地 HTTP 服务（单文件，零第三方依赖）
├── batch_gui.py            # 桌面窗口版入口（tkinter，双击形态的界面层）
├── entry_gui.py            # Web 版 exe 双击入口（端口复用探测 + 起服务 + 开浏览器）
├── launch.py               # Web 形态一键启动器：状态查询 / 安全停止 / 换端口
├── start.bat               # Windows 双击入口（自动定位 Python 并调用 launch.py，GBK 编码）
├── selftest.py             # 确定性自测：99 项断言，全过输出 SELFTEST_ALL_PASS
├── make_package.py         # 分发打包：生成完整 zip + MANIFEST.txt（逐文件 SHA256）
├── MANIFEST.txt            # 分发包清单（字节数 / SHA256 / 用途）
├── web/
│   └── index.html          # Web 操作页面（单文件，白底蓝主色简洁设计，零外部资源，离线可用）
├── docs/
│   ├── 程序文档.md          # 架构说明 / 模块划分 / 核心逻辑 / 依赖说明
│   ├── 使用文档.md          # 安装步骤 / 配置方法 / 操作示例 / 常见问题
│   ├── 架构与工程知识.md     # 架构约定与工程实践记录
│   └── 工作日志.md          # 开发过程记录
└── samples/input/          # 试用样例
    ├── demo_ok.zip         # 正常包（含中文路径）
    ├── demo_broken.zip     # 损坏包（验证 corrupt 分类）
    ├── demo_locked.7z      # 加密包，密码 demo123
    └── 7z.001~7z.004       # 真实 4 卷分卷
```

## 环境要求

1. **Python 3.8+**（源码运行必需；桌面窗口需带 tkinter 的 Python，Windows 官方安装包默认含）。双击 `start.bat` 自动定位解释器：`BX_PYTHON` → `where python` → `py -3` → 常见安装目录。
2. **7-Zip（可选）**：`.7z / .rar / 数字分卷 / RAR 分卷 / AES 加密 ZIP` 需要；自动探测（PATH → 注册表 InstallLocation → 常见目录），装好后无需任何配置；未装时内置格式不受影响，外部格式明确报 `unsupported`。
3. **PyInstaller（仅打包 exe 时）**：`pip install pyinstaller`。
4. 无需 `pip install` 任何运行时第三方包。

## 快速开始

### 桌面窗口（推荐）

```powershell
# 方式 A：直接用打包好的 exe
双击 批量解压控制台.exe → 选输入目录 → 开始解压

# 方式 B：源码运行
python batch_gui.py
```

界面三步：① 选择压缩包（扫描文件夹 / 手动选文件）→ ② 确认输出目录与选项（覆盖 / 子文件夹 / 密码）→ ③ 点「开始解压」，日志实时滚动，结束后可「打开输出目录」「复制汇总」。

### Web 页面

```powershell
start.bat                  # 起服务 + 浏览器打开 http://127.0.0.1:8765
start.bat --status / --stop / --port 8877 / --no-browser
```

### 命令行

```powershell
python batch_extract.py --input-dir "D:\packs" --output "D:\out"            # 递归扫描
python batch_extract.py --archives a.7z.001 b.zip --output "D:\out"         # 分卷只填首卷
python batch_extract.py --input-dir "D:\packs" --output "D:\out" --password demo123 --overwrite
python batch_extract.py --input-dir "D:\packs" --output "D:\out" --password p1 --password p2
```

stdout 恒为汇总 JSON（可直接管道解析），日志走 stderr。完整参数见 [使用文档](docs/使用文档.md)。

## 打包 exe

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole --name "批量解压控制台" batch_gui.py   # 桌面窗口版
pyinstaller --onefile --noconsole --add-data "web;web" entry_gui.py     # Web 版
```

注意：需使用带 tkinter 的 Python 打包桌面版（精简/嵌入式 Python 会产出不可用程序）。

## 验证与分发

```powershell
python selftest.py          # 99 项确定性自测（引擎单测 + 真实样例 + HTTP 端到端 + 前端静态 QA）
python make_package.py      # 生成完整分发 zip + MANIFEST.txt（逐文件 SHA256）
```

## 试用样例（samples/input/）

| 样例 | 用途 |
| --- | --- |
| demo_ok.zip | 正常包，含中文路径 |
| demo_broken.zip | 损坏包（验证 corrupt 分类） |
| demo_locked.7z | 加密包，密码 `demo123` |
| 7z.001~7z.004 | 真实 4 卷分卷（验证归组与串读） |
