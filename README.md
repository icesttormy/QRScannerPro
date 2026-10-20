# QRScannerPro

一个常驻 Windows 系统托盘的截图二维码 / 网址识别工具。按下全局快捷键后框选屏幕区域，程序会识别二维码和图片中的文字网址，并把结果展示在可复制、可滚动的窗口中。

## 功能

- 启动后常驻系统托盘，不强制弹出截图界面。
- 默认快捷键 `Ctrl+Alt+Q`，可在任意窗口触发框选。
- 支持二维码识别和英文网址 OCR。
- 多显示器环境下截取鼠标当前所在的屏幕。
- 结果窗口支持完整文本、逐条复制、复制全部和滚动查看。
- 只允许打开 `http/https` 链接；纯文本、WiFi、vCard、`file://` 等内容只展示和复制。
- 可选“打开链接前先显示结果”，默认开启。
- 支持自定义浏览器、全局快捷键和开机自启动。
- 配置采用原子写入，降低异常退出导致配置损坏的风险。

## 内容处理规则

| 识别内容 | 行为 |
| --- | --- |
| `http/https` 链接 | 可打开、可复制；默认先展示结果 |
| 普通文本二维码 | 只展示和复制，不打开浏览器 |
| WiFi / vCard / 电话 / 邮件 | 展示原文并复制 |
| `file://`、`javascript:`、命令行参数等 | 只展示和复制，拒绝执行 |
| 多个结果 | 显示结果列表，可逐条复制或打开 |

右下角提示卡片用于显示“已打开链接”“未识别到内容”“打开失败”等状态，成功提示会自动消失，鼠标悬停时暂停计时。

## 使用

1. 运行 `截图识码工具.exe`，程序进入系统托盘。
2. 按 `Ctrl+Alt+Q`，或右键托盘图标选择“立即截图识别”。
3. 拖动鼠标框选二维码或包含网址的文字区域。
4. 在结果窗口中查看、复制或打开识别内容。

## 配置和日志

配置文件：

```text
%APPDATA%\ScreenQRScanner\config.json
```

运行日志：

```text
%APPDATA%\ScreenQRScanner\run.log
```

卸载程序会清理 `%APPDATA%\ScreenQRScanner`。如需保留配置，请在卸载前备份该目录。

## 从源码运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

OCR 需要 Tesseract。程序会自动查找以下位置：

1. 项目内的 `tesseract_bin\tesseract.exe`
2. 环境变量 `TESSERACT_CMD`
3. 系统 `PATH` 中的 `tesseract`
4. `C:\Program Files\Tesseract-OCR\tesseract.exe`
5. `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`

没有 Tesseract 时，二维码识别仍然可用，文字网址 OCR 不可用。

## 构建 Windows exe

推荐使用 Python 3.10~3.14，当前在 Python 3.14.5 上验证通过。

```text
pip install -r requirements.txt
build.bat
```

`build.bat` 会：

- 如果存在 `tesseract_bin`，只提取运行所需的 DLL 和 `eng.traineddata`。
- 将构建输出放到 `dist_candidate`，不覆盖旧的 `dist`。
- 检查 PyInstaller 是否误收集了第三方运行环境中的 ICU DLL；发现问题会直接构建失败。

构建结果：

```text
dist_candidate\截图识码工具.exe
```

需要的安装包可以使用 Inno Setup 编译 `setup.iss`，输出到 `installer_candidate`。

### 打包环境注意事项

PyInstaller 会依据 `PATH` 搜索 DLL。如果构建环境中存在第三方 `poppler\Library\bin`，其中的 `icuuc.dll` / `icudt78.dll` 可能被误当成 Qt 依赖，导致程序启动时报：

```text
DLL load failed while importing QtCore
```

`build.bat` 已隔离构建用的 `PATH`，并通过 `tools/check_pyinstaller_bundle.py` 检查 DLL 来源。

## 测试

```text
python -m unittest discover -s tests -v
```

测试覆盖 URL 规则、域名截断、去重、配置原子写入、文字二维码样例和提示卡片状态。

## 目录结构

```text
QRScannerPro/
├── main.py
├── icon.ico
├── build.bat
├── setup.iss
├── requirements.txt
├── tools/
│   ├── prepare_tesseract_bundle.py
│   └── check_pyinstaller_bundle.py
└── tests/
    ├── test_core.py
    └── assets/
        └── text_qr.png
```

## 仓库说明

- `.venv`、`tesseract_bin`、`dist*`、`build*` 和安装包输出不会提交到源码仓库。
- 单个 onefile exe 体积较大，建议通过 GitHub Releases 分发，而不是直接提交进仓库。
- 源码公开并不等于可以商用；使用、修改和再分发规则以仓库根目录的 [`LICENSE`](LICENSE) 为准。

## 许可证

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅授权非商业用途。要点如下：

- 允许个人学习、研究、实验、业余项目等非商业使用，也允许慈善机构、教育机构、公共研究机构、公共安全或卫生机构、环保组织和政府机构将其用于非商业目的。
- 未经版权所有者书面许可，不得将本项目或其修改版用于任何商业目的，包括但不限于销售、收费分发、集成进商业产品或用于商业服务交付。
- 修改或再分发时，必须保留 `LICENSE` 文件和其中以 `Required Notice:` 开头的版权声明。
- 如需商业使用，请先联系版权所有者取得单独的商业授权。
- 第三方依赖（Qt/PySide6、OpenCV、Tesseract、pyzbar 等）仍遵循其各自许可证；本许可证只覆盖本仓库中由作者编写的代码。

Copyright (c) 2026 icesttormy

> 说明：这是一个“源码可见、仅限非商用”的项目，不是 OSI 定义的开源许可项目。
