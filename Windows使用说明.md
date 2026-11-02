# Windows 使用说明

## 结论

Windows 上**不需要安装 Python、数据库或任何服务**。推荐使用「绿色版 exe」：

- 解压后双击 `照片归档工具.exe`，浏览器自动打开本机网页；
- 人脸模型、运行库都在文件夹里，**首次使用也不用联网**；
- 照片、名单、人脸特征只留在本机 `data/`、`photos/` 里，不上传云端；
- 整个文件夹复制到另一台 Windows 电脑就能继续用。

> 建议浏览器：**Microsoft Edge 或 Google Chrome**。它们支持一次选择整个照片文件夹。

---

## 一、老师/家长怎么用（绿色版）

1. 把 `照片归档工具-Windows-x64.zip` 解压到桌面或 `D:\`。
   - 不要只把 exe 拖出来；`_internal\`、`insightface\` 必须和 exe 放在一起。
   - 不要放在 `C:\Program Files`（没有写权限）。
2. 双击 `照片归档工具.exe`。
3. 浏览器自动打开 `http://127.0.0.1:8765/`。若没有自动打开，手动把这个地址复制到 Edge/Chrome。
4. **使用期间保留黑色命令窗口**；关闭窗口或按 `Ctrl+C` 就停止服务。
5. 再次双击 exe，会直接打开已经在运行的页面，不会重复启动。

### 数据在哪里

```text
照片归档工具\
  data\       人员名单、人脸特征、阈值（最重要，定期备份）
  photos\     上传的活动照片
  out\        归档结果与报告
  roster\     底库参考照片（可选）
  照片归档工具.exe
  _internal\  程序运行库
  insightface\ 人脸模型
```

**备份 = 复制整个文件夹。** 换电脑时把整个文件夹拷过去即可。

### 网页里怎么操作

1. **底库管理** → **上传底库参考照片**：输入姓名，选择这个人的整个照片文件夹（建议 3～10 张清晰单人照）。
2. **总览** → **新建活动并上传照片**：选日期、填活动名、选择整个活动照片文件夹。
3. 等待自动识别，查看出现人员、未出现人员和灰区。
4. 处理 **灰区复核**、审查结果，确认未出现人员后发布正式名单。

> **人工添加优先**：系统没认出某张照片里的人、甚至没检出人脸时，打开这张照片，在「人工标记（以人工为准）」里选人并点「记为此人出现」；或在「未出现名单」里点「补活动照片」。人工标记会直接计入归档和出勤统计，不会被自动识别结果拦截。

---

## 二、绿色版从哪里来

### 方式 A：直接下载（有发布包时）

从项目的 GitHub Releases 下载 `photoorg-Windows-x64.zip`，解压即用。
（GitHub 资产使用 ASCII 文件名；本地自己打包时仍生成中文文件名。）

### 方式 B：自己打一个（推荐，项目已带离线包）

打包必须在 **Windows 64 位** 上做一次（PyInstaller 不能从 macOS 交叉编译 Windows exe）。
项目里的 `windows-offline\` 已经放好：

- `python-3.11.9-amd64.exe`：Python 官方 64 位安装包；
- `buffalo_l\`：检测 + 识别两个模型文件（182 MB）。

所以**模型不需要在 Windows 上再下载**。

> `windows-offline\` 体积约 200 MB，不进入 git。请从当前项目文件夹**整体复制**到
> Windows，不要只 `git clone`（clone 下来的仓库不含这个文件夹）。
>
> 本地可以保留一个 `photoorg-windows-build.zip`（约 193 MB，源码 + 离线包 + bat，
> 不进入 git），直接拷到 Windows 解压即可。

1. 把整个项目文件夹复制到 Windows（包括 `windows-offline\`；不用复制 macOS 的 `.venv/`）。
2. 双击 **`安装Python-Windows.bat`**：静默安装 Python 3.11（当前用户，不需要管理员）。
   - 如果已经装过 Python 3.11，这一步会直接跳过。
   - 也可以手动运行 `windows-offline\python-3.11.9-amd64.exe`。
3. 关闭安装窗口，双击 **`打包Windows版.bat`**。
4. 脚本会自动：
   - 创建 `.venv-build\`；
   - 安装 PyInstaller 和项目依赖（**这一步需要联网从 PyPI 下载依赖**）；
   - 使用 `windows-offline\buffalo_l\` 的本地模型（不会下载模型）；
   - 生成 `dist\照片归档工具\`；
   - 压缩成 `dist\照片归档工具-Windows-x64.zip` 并给出 SHA256。
5. 把 zip 拷到老师的电脑，解压后双击 exe。

> **模型和 Python 已经离线**；只有第一次 `pip install` 打包依赖时需要联网。
> 如果打包机完全不能联网，请先在有网的 Windows 上打一次包，或反馈后我给你准备
> Windows 依赖 wheel 离线包。
>
> 想强制“没有本地模型就报错、绝不联网下载”，可运行：
> `打包Windows版.bat --offline`
>
> 想跳过模型（减小包体、首次运行再下载）可运行：
> `打包Windows版.bat --skip-model`
>
> 打包完成后可先自检一次（会真正加载模型）：
> `dist\照片归档工具\照片归档工具.exe --check`

### 方式 C：用 GitHub Actions 自动打包

仓库已带 `.github/workflows/build-windows.yml`：

- 在 GitHub 网页上 **Actions → build-windows → Run workflow**；
- 完成后在该次运行的 Artifacts 里下载 `photoorg-windows-x64`；
- 如果推送 `v*` 标签，会自动把 zip 附加到 Release。

这样不用本地装 Windows Python，也能得到 exe。

> 绿色版采用「文件夹 + exe」形式（onedir），不是单文件 exe：启动更快、杀毒误报更少，
> 对使用者来说仍然是「解压后双击 exe」。`_internal\` 和 `insightface\` 不能删。

### 代码更新后重新生成传输包

`windows-offline\`（模型 + Python）和 `.venv-build\` 都不用重建，只有 exe 需要重新打包。

如果要重新制作「源码 + 离线包 + bat」的传输 zip：

- **macOS**：双击 `制作传输包.command`；
- **Windows**：双击 `制作传输包.bat`；
- 或命令行：`python packaging/make_transfer_zip.py`。

会生成：

```text
photoorg-windows-build.zip
photoorg-windows-build.sha256.txt
```

`data/`、`photos/`、`out/`、`.venv/`、`.venv-build/` 不会被放进传输包。

---

## 三、常见问题

### 双击后被 SmartScreen / 杀毒软件拦截

PyInstaller 打的未签名 exe 常见误报。确认文件来自本仓库后：

- SmartScreen：**更多信息 → 仍要运行**；
- 杀毒软件：把整个程序文件夹加入信任/排除项。

程序只监听 `127.0.0.1`，不提供局域网访问，也不上传照片。

### 提示缺少 `VCRUNTIME140_1.dll` / `MSVCP140.dll`

极少数干净的 Windows 系统缺少微软运行库。安装一次
[Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
即可；Windows 10/11 通常已经自带。

### 提示「无法创建/读取工作区」或闪退

- 不要放在 `C:\Program Files`、`C:\Windows` 等受保护目录；
- 放到桌面、`D:\照片归档工具\` 这类有写权限的位置；
- 把黑色窗口里的错误信息截图反馈。

### 浏览器没有自动打开

手动在 Edge/Chrome 输入窗口里显示的地址，通常是：

```text
http://127.0.0.1:8765/
```

如果 8765 被别的软件占用，程序会自动顺延到 8766、8767……以窗口里显示的为准。

### Windows 防火墙弹窗

如果系统询问是否允许 Python/照片归档工具访问网络，勾选 **专用网络** 并允许即可。
程序只监听 `127.0.0.1`，不会把照片发到局域网或互联网。

### 打包时报 `WinError 206` / 文件名或扩展名太长

这是 Windows 的 260 字符路径限制。`onnx` 依赖里自带很深的测试数据目录，如果项目放在
微信文件夹、下载目录等很深的路径下，`pip install` 就会失败。

例如：

```text
C:\Users\你的用户名\Documents\xwechat_files\...\photoorg-windows-build\.venv-build\...
```

新版 `打包Windows版.bat` 会自动检测：路径超过 100 个字符时，会自己切换到
`短路径打包.bat` 的逻辑，把源码复制到 `%USERPROFILE%\photoorg-build` 再打包。
所以你直接重新运行最新版脚本即可。

如果仍然失败，可以手动处理（任选其一）：

1. **推荐：把整个程序文件夹移到短路径**，例如 `C:\photoorg` 或 `D:\photoorg`；
   删除里面的 `.venv-build\` 后，重新双击 `打包Windows版.bat`。
2. **手动运行短路径脚本**：双击项目里的 **`短路径打包.bat`**。
   产物在 `%USERPROFILE%\photoorg-build\dist\照片归档工具-Windows-x64.zip`。

> 打包失败后留下的 `.venv-build\` 是半成品，重试前删掉它更稳妥。

### 提示模型下载失败（仅未打包模型的版本）

项目已经带了离线模型；如果打包时仍提示下载，说明 `windows-offline\buffalo_l\`
没有复制过去。把下面两个文件放到该目录即可：

```text
windows-offline\buffalo_l\det_10g.onnx
windows-offline\buffalo_l\w600k_r50.onnx
```

也可以手动指定模型目录：

```bat
打包Windows版.bat --model-dir D:\buffalo_l
```

### 选择不了文件夹

使用 Edge 或 Chrome。Firefox 对网页选择目录的支持不完整。

### 首次识别较慢

第一次识别要加载模型（几秒）；活动照片越多、分辨率越大，处理时间越长。之后会快一些。

### 升级新版本会不会丢数据？

不会，只要保留 `data\`、`photos\`、`out\`、`roster\`。升级步骤：

1. 关闭程序（关掉黑色窗口）；
2. 备份整个旧文件夹；
3. 解压新版本到一个新文件夹；
4. 把旧文件夹里的 `data\`、`photos\`、`out\`、`roster\` 复制到新文件夹（覆盖同名目录）；
5. 双击新版本的 exe。

---

## 四、源码模式（开发者备用）

如果只是想改代码、不想打包，也可以用原来的脚本方式，在 Windows 上创建源码虚拟环境：

1. 安装 Python 3.11（64 位），勾选 **Add python.exe to PATH**；
2. 双击 **`首次安装并打开网页-Windows.bat`**（首次，联网安装依赖）；
3. 以后双击 **`打开网页-Windows.bat`**。

源码模式使用 `.venv-win\`，数据同样在当前项目文件夹。日常使用仍推荐绿色版 exe，目标电脑不需要 Python。

命令行方式：

```bat
py -3.11 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install -e .
.venv-win\Scripts\photoorg.exe init --class 三年二班
.venv-win\Scripts\photoorg.exe review
```

---

## 五、商用提醒

当前 InsightFace `buffalo_l` 模型仅用于本地研究/演示测试。正式商业部署前，请核对并取得模型许可，或替换为已获商用授权的模型。
