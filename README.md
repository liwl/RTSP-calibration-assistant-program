# 相机标定辅助工具

基于 RTSP 协议连接 IP 摄像机，自动检测棋盘格标定板并采集高质量标定图片的桌面工具。

## 功能特性

- **多品牌支持** — 海康威视、大华、宇视自动生成 RTSP 地址，自定义模式支持任意品牌
- **截图流程** — 定时自动采集 → 棋盘格检测 → 清晰度/曝光/贴边/面积比四重质量检测 → 姿态去重 → 保存
- **OSD 控制** — 通过 ONVIF / HTTP 协议清除相机 OSD 文字叠加，避免干扰标定
- **实时预览** — 双栏显示实时画面与抓图结果，带辅助线和实时质量评分
- **语音播报** — TTS 实时播报截图状态，解放双手
- **重投影误差筛选** — 采集完成后自动标定并计算每张图的重投影误差，不合格图片自动移至子目录
- **照片管理** — 按序列号归档，缩略图列表预览，支持大图查看

### 新增功能

- **形变检测** — 通过单应性矩阵条件数评估棋盘格透视形变程度，防止极端角度
- **实时辅助线** — 3×3 网格叠加、距离提示、角度提示、综合质量评分
- **角点置信度** — 评估亚像素收敛质量，过滤弱检测
- **焦距一致性检查** — 检测多帧之间焦距变化，避免自动对焦导致的标定失败
- **综合质量评分** — 0-100 分，综合考虑清晰度、曝光、贴边、面积比、形变、置信度

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

## 使用流程

1. 填写相机品牌、IP、端口、用户名、密码、通道号
2. 点击「生成」获取 RTSP 地址
3. 设置棋盘格内角点尺寸（如 9×6）、截图间隔、序列号、目标数量
4. 点击「开始截图」自动采集
5. 观察实时预览中的辅助线和质量评分，调整棋盘格位置和角度
6. 达到目标数量后点击「重投影误差筛选」剔除不合格图片

## 图像验证工具

采集完成后，可使用独立的验证脚本批量检查图像质量并生成可视化报告：

```bash
# 基本用法
python validate_calibration.py --input screenshots/A001/

# 指定输出文件
python validate_calibration.py --input screenshots/A001/ --report report.html

# 指定棋盘格尺寸
python validate_calibration.py --input screenshots/A001/ --pattern-size 9x6
```

生成的 HTML 报告包含：
- 每张图像的详细检查结果
- 综合质量评分
- 问题图像列表和修复建议
- 标定建议

## 目录结构

```
├── main.py                   GUI 主入口
├── rtsp_capture.py           RTSP 流捕获
├── chessboard_detector.py    棋盘格检测 & 质量检测
├── voice.py                  TTS 语音播报
├── osd_control.py            OSD 控制（ONVIF + HTTP）
├── validate_calibration.py   图像验证工具
├── ffmpeg/                   FFmpeg 可执行文件
├── screenshots/              截图存档目录
└── requirements.txt          依赖清单
```

## 构建

详见 [BUILD.md](BUILD.md)，使用 PyInstaller 打包为单文件 exe。

## 技术细节

详见 [技术说明.md](技术说明.md)，包含完整架构说明、质量检测标准、姿态去重算法等。
