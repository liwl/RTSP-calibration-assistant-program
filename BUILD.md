# 构建指南

## 环境要求

- Windows 10/11 64位
- Python 3.8+
- 管理员权限（安装依赖可能需要）

## 安装依赖

```bash
pip install -r requirements.txt
pip install pyinstaller
```

## 打包命令

```bash
pyinstaller --noconsole --onefile --name "相机标定辅助工具" ^
  --hidden-import pythoncom ^
  --hidden-import win32com ^
  --hidden-import win32com.client ^
  --hidden-import win32com.server ^
  --hidden-import win32com.shell ^
  main.py
```

### 参数说明

| 参数 | 作用 |
|------|------|
| `--noconsole` | 隐藏控制台窗口（纯GUI应用） |
| `--onefile` | 打包成单个 exe 文件 |
| `--name` | 指定输出文件名 |
| `--hidden-import` | 强制包含动态导入的模块（voice.py 中的 `pythoncom`、`win32com`） |

## 输出产物

```
dist/
  相机标定辅助工具.exe   ← 独立可执行文件（约 70MB）
build/                    ← 构建临时文件（可删除）
*.spec                    ← 配置文件（可删除）
```

## 常见问题

### 杀毒软件误报
PyInstaller 打包的 exe 可能被部分杀毒软件误报，添加信任即可。

### 语音功能失效
确保系统已安装 **Microsoft Speech API (SAPI)**，Windows 10/11 默认自带。如语音功能异常，请检查系统语音设置：
`设置 → 时间和语言 → 语音 → 语音合成`。

### 减小体积
可移除 OpenCV 中不需要的模块，但操作较复杂。默认打包包含完整 OpenCV（约占 50MB）。
