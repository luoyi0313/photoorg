
# photoorg · 班级照片归档与出勤统计

本机运行的「人脸注册（底库）+ 1:N 匹配」照片归档工具。照片和人脸特征不上传云端；人工修正写入独立、可审计、可撤销的 `overrides` 叠加层。

> 默认模型为 InsightFace `buffalo_l`。其预训练权重仅可用于非商业研究/测试；正式对外使用前必须取得相应商业授权或替换为合规权重。

## Windows 开箱使用（推荐绿色版 exe）

目标电脑**不需要 Python**。完整说明：[Windows使用说明.md](Windows使用说明.md)。

- **拿现成包**：下载 `照片归档工具-Windows-x64.zip`，解压后双击 `照片归档工具.exe`。数据和模型都在文件夹里，首次使用也不用联网。
- **自己打包**：项目已带 `windows-offline/`（Python 3.11.9 官方安装包 + 检测/识别模型，约 200 MB，不进入 git，需整体复制）。在 Windows 上双击 [安装Python-Windows.bat](安装Python-Windows.bat)，再双击 [打包Windows版.bat](打包Windows版.bat)，生成 `dist\照片归档工具-Windows-x64.zip`。模型不联网；只有首次 pip 安装 PyInstaller 等依赖需要联网。
- **整包拷贝**：本地可保留一个 `photoorg-windows-build.zip`（源码 + 离线模型 + Python 安装包 + bat，约 193 MB，不进入 git），直接拷到 Windows 解压使用。
- **更新后重新生成传输包**：Mac 双击 [制作传输包.command](制作传输包.command)，Windows 双击 [制作传输包.bat](制作传输包.bat)，或运行 `python packaging/make_transfer_zip.py`。会生成 zip 和 `.sha256.txt`，不会打包 `data/`、`photos/`、`.venv/`。
- **路径太长报 WinError 206**：最新脚本会自动检测并切换到 [短路径打包.bat](短路径打包.bat)（复制到 `%USERPROFILE%\photoorg-build` 打包）；也可以手动把文件夹移到 `C:\photoorg` 后重试。
- **云端打包**：运行 GitHub Actions 的 `build-windows` 工作流，从 Artifacts 下载 zip。
- 推荐使用 Edge 或 Chrome，以便直接选择照片文件夹。

源码模式（需要 Python）仍可用：第一次双击 [首次安装并打开网页-Windows.bat](首次安装并打开网页-Windows.bat)，以后双击 [打开网页-Windows.bat](打开网页-Windows.bat)。

## 打包 Windows 绿色版

PyInstaller 不能从 macOS 交叉编译 Windows exe，因此打包要在 Windows 上执行一次（或走 GitHub Actions）：

```bat
py -3.11 -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -e ".[build]"
.venv-build\Scripts\python.exe packaging\build_windows.py
```

产物：

```text
dist\照片归档工具\
  照片归档工具.exe
  _internal\                 PyInstaller 运行时
  insightface\models\buffalo_l\  只带检测 + 识别两个模型（约 190 MB）
  使用说明.txt
dist\照片归档工具-Windows-x64.zip
```

模型不打进 exe，升级程序时不用重打模型；打包时优先使用 `windows-offline/buffalo_l/`，没有才用开发机的 `~/.insightface`，最后才联网下载到 `vendor\insightface\`。

## macOS / 命令行快速开始

```bash
# 已创建 .venv 时
source .venv/bin/activate
pip install -e '.[dev]'

# 1. 初始化
photoorg init --class 三年二班

# 2. 准备 roster/<人名>/ 下每人 3～10 张清晰、多角度的脸
photoorg person add --class 三年二班 张三 李四
photoorg enroll --class 三年二班 --from ./roster

# 3. 放照片到 photos/20261029-秋游/，跑批次
photoorg run --class 三年二班 --batch 20261029-秋游

# 4. 复核灰区 / 审查结果 / 管理底库
photoorg review --class 三年二班
```

## CLI 常用操作

```bash
photoorg status --class 三年二班
photoorg fix --class 三年二班 --batch 20261029-秋游 --csv corrections.csv
photoorg overrides export --class 三年二班 --batch 20261029-秋游 -o fixes.csv
photoorg rebuild --class 三年二班 --batch 20261029-秋游 --dry-run
photoorg adopt --class 三年二班 --batch 20261029-秋游
photoorg calibrate --class 三年二班              # 仅给建议
photoorg state confirm-absent --class 三年二班 --batch 20261029-秋游 张三 --by 罗老师
photoorg state publish --class 三年二班 --batch 20261029-秋游 --by 罗老师
```

## 目录

```text
photos/<日期-活动>/            原始活动照片
roster/<人名>/                 录入底库的参考照片（可选）
data/persons.db                全局人员 / 班级关系 / 模板
 data/classes/<班级>/faces.db  不可变推理产物
 data/classes/<班级>/overrides.db  append-only 人工修正
out/<批次>/                    派生产物：硬链接归档 + report.md + report.csv
```

`out/` 每次都会根据数据库全量重建，请勿直接编辑。若确实在 Finder 中拖动了文件，执行 `photoorg adopt` 把变更反写为人工修正。

## 运行测试

```bash
pytest -q
```
