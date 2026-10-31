
# photoorg · 班级照片归档与出勤统计

本机运行的「人脸注册（底库）+ 1:N 匹配」照片归档工具。照片和人脸特征不上传云端；人工修正写入独立、可审计、可撤销的 `overrides` 叠加层。

> 默认模型为 InsightFace `buffalo_l`。其预训练权重仅可用于非商业研究/测试；正式对外使用前必须取得相应商业授权或替换为合规权重。

## Windows 开箱使用

Windows 用户请先看：[Windows使用说明.md](Windows使用说明.md)。

- 第一次：双击 [首次安装并打开网页-Windows.bat](首次安装并打开网页-Windows.bat)，会自动创建 Windows 虚拟环境、安装依赖并打开网页；只需预先安装 Python 3.11（64 位）。
- 以后：双击 [打开网页-Windows.bat](打开网页-Windows.bat)。
- 推荐使用 Edge 或 Chrome，以便直接选择照片文件夹。

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
