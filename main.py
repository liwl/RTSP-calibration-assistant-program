"""
RTSP 相机标定辅助工具 - 主程序

基于 Tkinter 的桌面 GUI 应用，用于自动采集相机标定图像。
主要功能：
- 连接 RTSP 视频流（支持海康威视/大华/宇视等品牌）
- 自动检测棋盘格标定板
- 多重质量检测（清晰度、曝光、贴边、面积比）
- 姿态去重，避免重复角度
- 重投影误差筛选，剔除不合格图像
- 语音播报采集状态
- OSD 控制，清除/恢复相机文字叠加

架构：
main.py                  GUI 主程序
├── rtsp_capture.py       RTSP 流捕获 & 截图
├── chessboard_detector.py  棋盘格角点检测 & 质量检测
├── voice.py              TTS 语音播报
├── osd_control.py        OSD 文字叠加控制
└── screenshots/          截图存档目录
    ├── config.json       UI 配置持久化
    ├── rtsp_history.json RTSP 地址历史
    └── {序列号}/          按序列号归档的截图
"""

import json
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import os
import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont
from datetime import datetime

from rtsp_capture import RTSPSnapshotter
from chessboard_detector import ChessboardDetector
from voice import VoiceAnnouncer
from osd_control import OSDController


class App:
    """
    RTSP 相机标定辅助工具主应用程序
    
    基于 Tkinter 的桌面 GUI，集成 RTSP 流捕获、棋盘格检测、
    质量控制、语音播报和 OSD 控制等功能。
    
    主要组件：
    - 控制面板：相机连接、参数设置、操作按钮
    - 预览区：实时画面 + 抓图结果双栏显示
    - 照片列表：缩略图预览，支持点击查看大图
    - 日志区：运行日志和状态信息
    
    截图流程：
    1. 用户设置参数（棋盘格尺寸、间隔、序列号等）
    2. 点击「开始截图」启动后台采集线程
    3. 定时抓帧 → 棋盘格检测 → 质量检测 → 姿态去重 → 保存
    4. 达到目标数量后可进行重投影误差筛选
    """
    
    def __init__(self):
        """初始化应用程序，创建主窗口和核心组件"""
        self.root = tk.Tk()
        self.root.title("RTSP相机标定辅助工具")
        self.root.geometry("1280x800")
        self.root.minsize(1000, 600)

        # 核心组件
        self.snapshotter = RTSPSnapshotter()      # RTSP 流帧抓取器
        self.detector = ChessboardDetector()      # 棋盘格检测器
        self.announcer = VoiceAnnouncer()         # 语音播报器
        self.osd_ctrl = OSDController()           # OSD 控制器

        # 运行状态
        self.running = False                      # 截图任务运行标志
        self._stop_event = threading.Event()      # 停止事件信号
        self._capture_thread = None               # 截图采集线程
        self._live_running = False                # 实时预览运行标志
        self._capture_preview_image = None        # 抓图预览 PhotoImage 引用
        self._live_preview_image = None           # 实时预览 PhotoImage 引用
        self.osd_status_var = tk.StringVar(value="OSD: 未操作")

        # 照片数据存储
        self.photo_data = []          # 已拍照片列表：[(filepath, hist, gray, corners, shape), ...]
        self._thumb_refs = []         # PhotoImage 引用列表，防止垃圾回收
        self._reprojection_errors = {}  # 重投影误差：{filepath: error_value}
        self.current_photo_count = 0  # 当前已拍照片总数
        self.new_capture_count = 0    # 本次新采集数量
        self._target_count = 20       # 目标采集数量
        self.save_dir = "screenshots" # 截图保存根目录

        # 持久化配置
        self._history_file = os.path.join(self.save_dir, "rtsp_history.json")
        self._rtsp_history = self._load_rtsp_history()
        self._config_file = os.path.join(self.save_dir, "config.json")
        self._config = self._load_config()
        self.camera_list = self._config.get('cameras', [])
        self._batch_updating = False  # 批量更新标志，防止循环触发

        # 初始化 GUI
        self._build_gui()
        self._center_window()
        # 延迟加载已有照片（等待窗口显示后再加载）
        self.root.after(500, self._load_photos_from_directory)

    def _center_window(self):
        """将窗口居中显示在屏幕上"""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_gui(self):
        """
        构建主界面布局
        
        整体结构：
        ┌─────────────────────────────────────────┐
        │ 工具栏 (质量阈值、OSD控制、翻转)          │
        ├─────────────────────────────────────────┤
        │ 控制面板 (相机、RTSP地址、参数、按钮)      │
        ├───────────────────────────┬─────────────┤
        │ 状态栏                     │             │
        ├───────────────────────────┤  照片列表    │
        │ 运行日志                   │  (缩略图)    │
        ├───────────────────────────┤             │
        │ 预览区 (实时画面 | 抓图结果) │             │
        └───────────────────────────┴─────────────┘
        """
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 构建顶部区域
        self._build_toolbar(main_frame)
        self._build_control_area(main_frame)

        # 构建内容区域（左侧 + 右侧）
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        # 左侧：状态栏 + 日志 + 预览
        left_frame = ttk.Frame(content_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_status_bar(left_frame)
        self._build_log_area(left_frame)
        self._build_preview_area(left_frame)

        # 右侧：照片列表
        self._build_photo_list(content_frame)

    def _build_toolbar(self, parent):
        """工具栏：质量阈值、OSD控制、画面翻转"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(frame, text="质量阈值...", command=self._open_quality_dialog, width=14).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(frame, text="OSD控制...", command=self._open_osd_dialog, width=14).pack(side=tk.LEFT, padx=5)

        ttk.Separator(frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self.flip_var = tk.BooleanVar(value=self._config.get('flip_180', False))
        ttk.Checkbutton(frame, text="画面翻转180°（相机倒装时使用）", variable=self.flip_var).pack(side=tk.LEFT)

    def _open_quality_dialog(self):
        """
        质量阈值设置弹窗（模态）
        
        提供以下参数的调整：
        - 棋盘格面积占比：棋盘格占图像面积的最小比例
        - 姿态中心距离：用于姿态去重的中心位置差异阈值
        - 姿态缩放比：用于姿态去重的尺度差异范围
        - 形状差异：用于姿态去重的形状相似度阈值
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("质量阈值设置")
        dialog.geometry("550x380")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 主框架
        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        ttk.Label(main_frame, text="调整质量检测阈值", font=("", 11, "bold")).pack(anchor=tk.W, pady=(0, 10))
        
        # 参数定义：(标签, 配置键, 默认值, 最小值, 最大值, 说明)
        fields = [
            ("棋盘格面积占比≥:", "area_ratio_min", "0.10", 0.01, 0.50, 
             "棋盘格凸包面积占图像面积的最小比例。值越大要求棋盘格在画面中占比越大。"),
            ("姿态中心距离≤ (像素):", "pose_center_dist", "50", 10, 200, 
             "用于姿态去重。两幅图的棋盘格中心距离小于此值时，可能被视为重复姿态。"),
            ("姿态缩放比最小值:", "pose_scale_min", "0.85", 0.50, 0.99, 
             "用于姿态去重。新图与已有图的尺度比值小于此值时，视为不同距离。"),
            ("姿态缩放比最大值:", "pose_scale_max", "1.15", 1.01, 2.00, 
             "用于姿态去重。新图与已有图的尺度比值大于此值时，视为不同距离。"),
            ("形状差异≤:", "pose_shape_diff", "0.05", 0.01, 0.20, 
             "用于姿态去重。归一化形状差异小于此值时，视为相同姿态。值越小越严格。"),
        ]

        vars_ = {}
        validation_errors = {}
        
        # 创建参数输入区域
        param_frame = ttk.Frame(main_frame)
        param_frame.pack(fill=tk.BOTH, expand=True)
        
        for label, key, default, min_val, max_val, desc in fields:
            # 参数行
            row = ttk.Frame(param_frame)
            row.pack(fill=tk.X, pady=3)
            
            # 标签
            ttk.Label(row, text=label, width=20, anchor=tk.E).pack(side=tk.LEFT, padx=(0, 8))
            
            # 输入框
            v = tk.StringVar(value=self._config.get(key, default))
            vars_[key] = v
            
            entry = ttk.Entry(row, textvariable=v, width=10, justify=tk.CENTER)
            entry.pack(side=tk.LEFT)
            
            # 范围提示
            range_text = f"({min_val} ~ {max_val})"
            ttk.Label(row, text=range_text, width=15, foreground="#666").pack(side=tk.LEFT, padx=(5, 0))
            
            # 错误提示标签
            error_label = ttk.Label(row, text="", foreground="red", width=20)
            error_label.pack(side=tk.LEFT, padx=(5, 0))
            validation_errors[key] = error_label

        # 说明区域
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(10, 5))
        ttk.Label(main_frame, text="参数说明：", font=("", 10, "bold")).pack(anchor=tk.W)
        
        for label, key, default, min_val, max_val, desc in fields:
            ttk.Label(main_frame, text=f"• {label.rstrip('≥:≤')}：{desc}", 
                     foreground="#555", wraplength=500, justify=tk.LEFT).pack(anchor=tk.W, pady=1)

        def validate_input(key, value, min_val, max_val):
            """验证输入值是否在有效范围内"""
            try:
                num_val = float(value.strip())
                if num_val < min_val or num_val > max_val:
                    validation_errors[key].config(text=f"范围: {min_val}-{max_val}")
                    return False
                else:
                    validation_errors[key].config(text="")
                    return True
            except ValueError:
                validation_errors[key].config(text="必须是数字")
                return False

        def on_ok():
            # 验证所有输入
            all_valid = True
            for label, key, default, min_val, max_val, desc in fields:
                if not validate_input(key, vars_[key].get(), min_val, max_val):
                    all_valid = False
            
            if not all_valid:
                messagebox.showerror("输入错误", "请检查输入值是否在有效范围内", parent=dialog)
                return
            
            # 更新配置
            for label, key, default, min_val, max_val, desc in fields:
                self._config[key] = vars_[key].get().strip()
            
            self._save_config()
            messagebox.showinfo("成功", "质量阈值已更新", parent=dialog)
            dialog.destroy()

        # 按钮行
        btn_row = ttk.Frame(main_frame)
        btn_row.pack(fill=tk.X, pady=(15, 0))
        ttk.Button(btn_row, text="确定", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)

    def _open_osd_dialog(self):
        """OSD控制弹窗（非模态）"""
        dialog = tk.Toplevel(self.root)
        dialog.title("OSD控制")
        dialog.geometry("320x180")
        dialog.resizable(False, False)
        dialog.transient(self.root)

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        cam = self._get_selected_camera()
        cam_info = cam['name'] if cam else "未选择"
        ttk.Label(frame, text=f"当前相机: {cam_info}", font=("", 10)).pack(anchor=tk.W, pady=(0, 12))

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=5)
        self.osd_clear_btn = ttk.Button(btn_row, text="清除OSD", command=self._disable_osd, width=12)
        self.osd_clear_btn.pack(side=tk.LEFT, padx=5)
        self.osd_restore_btn = ttk.Button(btn_row, text="恢复OSD", command=self._enable_osd, width=12)
        self.osd_restore_btn.pack(side=tk.LEFT, padx=5)

        self.osd_status_var = tk.StringVar(value="OSD: 未操作")
        ttk.Label(frame, textvariable=self.osd_status_var, foreground="#888").pack(pady=(8, 0))

        ttk.Button(frame, text="关闭", command=dialog.destroy, width=10).pack(pady=(10, 0))

    def _build_control_area(self, parent):
        frame = ttk.LabelFrame(parent, text="控制面板", padding=10)
        frame.pack(fill=tk.X, pady=(0, 5))

        # 隐藏变量（通过相机下拉设置，无可见输入框）
        self.brand_var = tk.StringVar(value='海康威视')
        self.ip_var = tk.StringVar(value='')
        self.port_var = tk.StringVar(value='554')
        self.user_var = tk.StringVar(value='admin')
        self.pwd_var = tk.StringVar(value='')
        self.channel_var = tk.StringVar(value='1')

        # ── 第1行：相机下拉 + CRUD ──
        cam_row = ttk.Frame(frame)
        cam_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cam_row, text="相机:").pack(side=tk.LEFT)
        self.camera_name_var = tk.StringVar()
        self.camera_combo = ttk.Combobox(cam_row, textvariable=self.camera_name_var,
                                          state="readonly", width=22)
        self.camera_combo.pack(side=tk.LEFT, padx=(3, 8))
        self.camera_combo.bind("<<ComboboxSelected>>", self._on_camera_select)

        self.add_cam_btn = ttk.Button(cam_row, text="+添加", command=self._add_camera, width=6)
        self.add_cam_btn.pack(side=tk.LEFT, padx=2)
        self.edit_cam_btn = ttk.Button(cam_row, text="编辑", command=self._edit_camera, width=6)
        self.edit_cam_btn.pack(side=tk.LEFT, padx=2)
        self.del_cam_btn = ttk.Button(cam_row, text="删除", command=self._delete_camera, width=6)
        self.del_cam_btn.pack(side=tk.LEFT, padx=2)

        # ── 第2行：RTSP地址（始终可编辑） ──
        url_row = ttk.Frame(frame)
        url_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(url_row, text="RTSP:").pack(side=tk.LEFT)
        self.rtsp_var = tk.StringVar()
        self.rtsp_entry = ttk.Combobox(url_row, textvariable=self.rtsp_var, values=self._rtsp_history)
        self.rtsp_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # ── 第3行：棋盘格 / 截图间隔 / 序列号 / 目标 / 后端 ──
        param_frame = ttk.Frame(frame)
        param_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(param_frame, text="棋盘格:").pack(side=tk.LEFT)
        cb_frame = ttk.Frame(param_frame)
        cb_frame.pack(side=tk.LEFT, padx=(3, 12))
        self.cb_w_var = tk.StringVar(value=self._config.get('cb_w', '9'))
        self.cb_h_var = tk.StringVar(value=self._config.get('cb_h', '6'))
        ttk.Entry(cb_frame, textvariable=self.cb_w_var, width=4, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(cb_frame, text="×", font=("", 12)).pack(side=tk.LEFT, padx=2)
        ttk.Entry(cb_frame, textvariable=self.cb_h_var, width=4, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(param_frame, text="间隔:").pack(side=tk.LEFT)
        self.interval_var = tk.StringVar(value=self._config.get('interval', '5'))
        interval_frame = ttk.Frame(param_frame)
        interval_frame.pack(side=tk.LEFT, padx=(3, 12))
        ttk.Entry(interval_frame, textvariable=self.interval_var, width=5, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(interval_frame, text="秒").pack(side=tk.LEFT, padx=1)
        ttk.Label(param_frame, text="序列号:").pack(side=tk.LEFT)
        self.serial_var = tk.StringVar(value=self._config.get('serial', 'A001'))
        self.serial_entry = ttk.Entry(param_frame, textvariable=self.serial_var, width=8, justify=tk.CENTER)
        self.serial_entry.pack(side=tk.LEFT, padx=(3, 12))
        self.serial_var.trace_add("write", self._on_serial_change)
        ttk.Label(param_frame, text="目标:").pack(side=tk.LEFT)
        self.target_count_var = tk.StringVar(value=self._config.get('target_count', '20'))
        ttk.Entry(param_frame, textvariable=self.target_count_var, width=5, justify=tk.CENTER).pack(side=tk.LEFT, padx=(3, 0))
        ttk.Label(param_frame, text="张").pack(side=tk.LEFT, padx=1)

        # 后端选择器
        ttk.Separator(param_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(param_frame, text="后端:").pack(side=tk.LEFT)
        self.backend_var = tk.StringVar(value="FFmpeg (推荐)")
        self.backend_combo = ttk.Combobox(param_frame, textvariable=self.backend_var,
                                          values=["FFmpeg (推荐)", "OpenCV"],
                                          state="readonly", width=12)
        self.backend_combo.pack(side=tk.LEFT, padx=(3, 0))
        self.backend_combo.bind("<<ComboboxSelected>>", self._on_backend_change)

        # ── 第4行：操作按钮 ──
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(2, 0))
        self.start_btn = ttk.Button(btn_frame, text="开始截图", command=self.start_capture, width=10)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_capture, state=tk.DISABLED, width=8)
        self.stop_btn.pack(side=tk.LEFT, padx=4)
        self.test_btn = ttk.Button(btn_frame, text="测试连接", command=self.test_connection, width=10)
        self.test_btn.pack(side=tk.LEFT, padx=4)
        
        # 手动抓图按钮
        self.manual_capture_btn = ttk.Button(btn_frame, text="手动抓图", command=self.manual_capture, width=10)
        self.manual_capture_btn.pack(side=tk.LEFT, padx=4)
        
        ttk.Button(btn_frame, text="打开目录", command=self.open_screenshots_dir, width=10).pack(side=tk.LEFT, padx=4)
        self.reproject_btn = ttk.Button(btn_frame, text="重投影误差筛选", command=self._run_reprojection_filter, width=14, state=tk.DISABLED)
        self.reproject_btn.pack(side=tk.LEFT, padx=4)

        # ── 初始化 ──
        self._populate_camera_combo()
        self._generate_rtsp_url()

    def _generate_rtsp_url(self, save_history=True):
        brand = self.brand_var.get()
        ip = self.ip_var.get().strip()
        port = self.port_var.get().strip() or "554"
        user = self.user_var.get().strip()
        pwd = self.pwd_var.get().strip()
        channel = self.channel_var.get().strip() or "1"

        if not ip:
            url = ""
        elif brand == "海康威视":
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{channel}01"
        elif brand == "大华":
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype=0"
        elif brand == "宇视":
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/unicast/c{channel}/s0/live"
        else:
            url = ""
        self.rtsp_var.set(url)
        if save_history and url:
            self._add_to_rtsp_history(url)

    def _on_rtsp_field_change(self, *args):
        if getattr(self, '_batch_updating', False):
            return
        self._generate_rtsp_url(save_history=False)

    def _on_backend_change(self, *args):
        use_ffmpeg = self.backend_var.get() == "FFmpeg (推荐)"
        self.snapshotter.set_backend(use_ffmpeg)
        self.log(f"切换后端: {'FFmpeg' if use_ffmpeg else 'OpenCV'}")
        if self.rtsp_var.get().strip():
            self.log("切换后端后请重新测试连接")

    def _load_rtsp_history(self):
        try:
            with open(self._history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []

    def _save_rtsp_history(self):
        try:
            os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
            with open(self._history_file, 'w', encoding='utf-8') as f:
                json.dump(self._rtsp_history, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _add_to_rtsp_history(self, url):
        if not url or url in self._rtsp_history:
            return
        self._rtsp_history.insert(0, url)
        if len(self._rtsp_history) > 50:
            self._rtsp_history = self._rtsp_history[:50]
        try:
            self.rtsp_entry.config(values=self._rtsp_history)
        except:
            pass
        self._save_rtsp_history()

    def _load_config(self):
        try:
            with open(self._config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except:
            return {}

        # 旧版 flat 格式迁移为 cameras 列表格式
        if 'brand' in cfg and 'cameras' not in cfg:
            name = f"{cfg.get('brand', '')}-{cfg.get('ip', 'ip')}" if cfg.get('ip') else "默认相机"
            cfg['cameras'] = [{
                'name': name,
                'brand': cfg.get('brand', '海康威视'),
                'ip': cfg.get('ip', ''),
                'port': cfg.get('port', '554'),
                'username': cfg.get('username', 'admin'),
                'password': cfg.get('password', ''),
                'channel': cfg.get('channel', '1'),
            }]
            cfg['selected_camera'] = name
            for k in ['brand', 'ip', 'port', 'username', 'password', 'channel']:
                cfg.pop(k, None)
            self._save_config_direct(cfg)
        return cfg

    def _save_config_direct(self, config):
        """直接将原始 dict 写入 config.json（用于迁移）"""
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _save_config(self):
        config = {
            'cameras': getattr(self, 'camera_list', []),
            'selected_camera': self.camera_name_var.get() if hasattr(self, 'camera_name_var') else '',
            'cb_w': self.cb_w_var.get(),
            'cb_h': self.cb_h_var.get(),
            'interval': self.interval_var.get(),
            'serial': self.serial_var.get(),
            'target_count': self.target_count_var.get(),
            'area_ratio_min': self._config.get('area_ratio_min', '0.10'),
            'pose_center_dist': self._config.get('pose_center_dist', '50'),
            'pose_scale_min': self._config.get('pose_scale_min', '0.85'),
            'pose_scale_max': self._config.get('pose_scale_max', '1.15'),
            'pose_shape_diff': self._config.get('pose_shape_diff', '0.05'),
            'flip_180': self.flip_var.get(),
        }
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _get_selected_camera(self):
        """返回当前选中的相机 dict，或 None"""
        name = self.camera_name_var.get() if hasattr(self, 'camera_name_var') else ''
        if name and hasattr(self, 'camera_list'):
            for cam in self.camera_list:
                if cam['name'] == name:
                    return cam
        return None

    def _populate_camera_combo(self):
        """刷新相机下拉列表"""
        if not hasattr(self, 'camera_combo'):
            return
        names = [cam['name'] for cam in self.camera_list]
        self.camera_combo.config(values=names)
        if names:
            sel = self._config.get('selected_camera', names[0])
            if sel in names:
                self.camera_name_var.set(sel)
            else:
                self.camera_name_var.set(names[0])
            self._on_camera_select()
        else:
            self.camera_name_var.set('')

    def _on_camera_select(self, event=None):
        """相机下拉选中 → 自动填充各字段"""
        cam = self._get_selected_camera()
        if not cam:
            return
        self._batch_updating = True
        self.brand_var.set(cam.get('brand', '海康威视'))
        self.ip_var.set(cam.get('ip', ''))
        self.port_var.set(cam.get('port', '554'))
        self.user_var.set(cam.get('username', 'admin'))
        self.pwd_var.set(cam.get('password', ''))
        self.channel_var.set(cam.get('channel', '1'))
        self._batch_updating = False
        self._generate_rtsp_url(save_history=False)
        self._config['selected_camera'] = cam['name']
        self._save_config()

    def _open_camera_dialog(self, title, initial_data=None):
        """新增/编辑相机弹窗（模态），返回 dict 或 None"""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("380x350")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        result = [None]
        is_add = initial_data is None
        data = dict(initial_data) if initial_data else {
            'name': '', 'brand': '海康威视', 'ip': '', 'port': '554',
            'username': 'admin', 'password': '', 'channel': '1'
        }

        pad = {"padx": 10, "pady": 3}
        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        def add_row(label, var, width=25, show=None, values=None):
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, **pad)
            ttk.Label(row, text=label, width=8, anchor=tk.E).pack(side=tk.LEFT, padx=(0, 6))
            if values:
                w = ttk.Combobox(row, textvariable=var, values=values, width=width - 5, state="readonly")
            else:
                w = ttk.Entry(row, textvariable=var, width=width, show=show)
            w.pack(side=tk.LEFT, fill=tk.X, expand=True)
            return w

        name_var = tk.StringVar(value=data['name'])
        brand_var = tk.StringVar(value=data['brand'])
        ip_var = tk.StringVar(value=data['ip'])
        port_var = tk.StringVar(value=data['port'])
        user_var = tk.StringVar(value=data['username'])
        pwd_var = tk.StringVar(value=data['password'])
        ch_var = tk.StringVar(value=data['channel'])

        add_row("名称:", name_var)
        add_row("品牌:", brand_var, values=["海康威视", "大华", "宇视", "自定义"])
        add_row("IP地址:", ip_var)
        add_row("端口:", port_var, width=10)
        add_row("用户名:", user_var)
        add_row("密码:", pwd_var, show="*")
        add_row("通道号:", ch_var, width=6)

        def on_ok():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("错误", "相机名称不能为空", parent=dialog)
                return
            if is_add and any(cam['name'] == name for cam in self.camera_list):
                messagebox.showerror("错误", f"相机名称 '{name}' 已存在", parent=dialog)
                return
            if not ip_var.get().strip():
                messagebox.showerror("错误", "IP地址不能为空", parent=dialog)
                return
            result[0] = {
                'name': name,
                'brand': brand_var.get(),
                'ip': ip_var.get().strip(),
                'port': port_var.get().strip() or '554',
                'username': user_var.get().strip() or 'admin',
                'password': pwd_var.get(),
                'channel': ch_var.get().strip() or '1',
            }
            dialog.destroy()

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(15, 0))
        ttk.Button(btn_row, text="确定", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)
        return result[0]

    def _add_camera(self):
        cam = self._open_camera_dialog("添加相机")
        if cam:
            self.camera_list.append(cam)
            self._populate_camera_combo()
            self.camera_name_var.set(cam['name'])
            self._on_camera_select()
            self.log(f"已添加相机: {cam['name']}")
            self._save_config()

    def _edit_camera(self):
        old = self._get_selected_camera()
        if not old:
            messagebox.showinfo("提示", "请先选择要编辑的相机")
            return
        cam = self._open_camera_dialog("编辑相机", initial_data=old)
        if cam:
            idx = next(i for i, c in enumerate(self.camera_list) if c['name'] == old['name'])
            self.camera_list[idx] = cam
            self._populate_camera_combo()
            if cam['name'] == old['name']:
                self._on_camera_select()
            self.log(f"已编辑相机: {cam['name']}")
            self._save_config()

    def _delete_camera(self):
        cam = self._get_selected_camera()
        if not cam:
            messagebox.showinfo("提示", "请先选择要删除的相机")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除相机 '{cam['name']}' 吗?"):
            return
        self.camera_list = [c for c in self.camera_list if c['name'] != cam['name']]
        self._populate_camera_combo()
        self.log(f"已删除相机: {cam['name']}")
        self._save_config()
        # 异步清除 OSD（ONVIF → HTTP 回退）
        def _task():
            brand = self.brand_var.get()
            ip = self.ip_var.get().strip()
            port = self.port_var.get().strip() or "554"
            user = self.user_var.get().strip()
            pwd = self.pwd_var.get().strip()
            channel = self.channel_var.get().strip() or "1"
            if not ip:
                self.root.after(0, lambda: self.log("请先填写IP地址"))
                return
            self.root.after(0, lambda: self.log("正在清除OSD..."))
            self.root.after(0, lambda: self.osd_status_var.set("OSD: 清除中..."))
            ok = self.osd_ctrl.disable_osd(brand, ip, port, user, pwd, channel)
            if ok:
                self.root.after(0, lambda: self.log(f"OSD清除成功 ({brand})"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 已清除"))
            else:
                self.root.after(0, lambda: self.log("OSD清除失败，请检查相机连接或使用ROI裁剪"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 清除失败"))
        threading.Thread(target=_task, daemon=True).start()

    def _disable_osd(self):
        """清除 OSD 文字叠加"""
        def _task():
            brand = self.brand_var.get()
            ip = self.ip_var.get().strip()
            port = self.port_var.get().strip() or "554"
            user = self.user_var.get().strip()
            pwd = self.pwd_var.get().strip()
            channel = self.channel_var.get().strip() or "1"
            if not ip:
                self.root.after(0, lambda: self.log("请先填写IP地址"))
                return
            self.root.after(0, lambda: self.log("正在清除OSD..."))
            self.root.after(0, lambda: self.osd_status_var.set("OSD: 清除中..."))
            ok = self.osd_ctrl.disable_osd(brand, ip, port, user, pwd, channel)
            if ok:
                self.root.after(0, lambda: self.log(f"OSD清除成功 ({brand})"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 已清除"))
            else:
                self.root.after(0, lambda: self.log("OSD清除失败，请检查相机连接或使用ROI裁剪"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 清除失败"))
        threading.Thread(target=_task, daemon=True).start()

    def _enable_osd(self):
        def _task():
            brand = self.brand_var.get()
            ip = self.ip_var.get().strip()
            port = self.port_var.get().strip() or "554"
            user = self.user_var.get().strip()
            pwd = self.pwd_var.get().strip()
            channel = self.channel_var.get().strip() or "1"
            if not ip:
                self.root.after(0, lambda: self.log("请先填写IP地址"))
                return
            self.root.after(0, lambda: self.log("正在恢复OSD..."))
            ok = self.osd_ctrl.enable_osd(brand, ip, port, user, pwd, channel)
            if ok:
                self.root.after(0, lambda: self.log(f"OSD恢复成功 ({brand})"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 已恢复"))
            else:
                self.root.after(0, lambda: self.log("OSD恢复失败"))
                self.root.after(0, lambda: self.osd_status_var.set("OSD: 恢复失败"))
        threading.Thread(target=_task, daemon=True).start()

    def _build_photo_list(self, parent):
        self._photo_list_frame = ttk.LabelFrame(parent, text="已拍照片 (0/0)", padding=5)
        frame = self._photo_list_frame
        frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        frame.pack_propagate(False)
        frame.configure(width=240)

        canvas = tk.Canvas(frame, bg="#1e1e1e", highlightthickness=0, width=220)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
        self._thumb_container = ttk.Frame(canvas)

        self._thumb_container.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self._thumb_container, anchor="nw", width=218)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def _build_status_bar(self, parent):
        self.status_var = tk.StringVar(value="就绪")
        bar = ttk.Label(parent, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2))
        bar.pack(fill=tk.X, pady=2)

    def _build_log_area(self, parent):
        frame = ttk.LabelFrame(parent, text="运行日志", padding=5)
        frame.pack(fill=tk.BOTH, expand=True, pady=2)
        self.log_text = scrolledtext.ScrolledText(frame, height=8, font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_preview_area(self, parent):
        frame = ttk.LabelFrame(parent, text="预览", padding=5)
        frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="实时画面", font=("", 9)).grid(row=0, column=0, pady=(0, 2))
        self.live_preview_label = ttk.Label(frame, background="#1a1a1a", anchor=tk.CENTER)
        self.live_preview_label.grid(row=1, column=0, sticky="nsew", padx=(0, 2))

        ttk.Label(frame, text="抓图结果", font=("", 9)).grid(row=0, column=1, pady=(0, 2))
        self.capture_preview_label = ttk.Label(frame, background="#1a1a1a", anchor=tk.CENTER)
        self.capture_preview_label.grid(row=1, column=1, sticky="nsew", padx=(2, 0))

    def log(self, message):
        def _log():
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{ts}] {message}\n")
            self.log_text.see(tk.END)
        self.root.after(0, _log)

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def parse_chessboard_size(self):
        try:
            w = int(self.cb_w_var.get().strip())
            h = int(self.cb_h_var.get().strip())
            if w < 2 or h < 2:
                raise ValueError
            return w, h
        except ValueError:
            messagebox.showerror("输入错误", "棋盘格内角点尺寸必须是大于1的整数")
            return None

    def parse_interval(self):
        try:
            val = float(self.interval_var.get().strip())
            if val <= 0:
                raise ValueError
            return val
        except ValueError:
            messagebox.showerror("输入错误", "截图间隔必须是正数")
            return None

    def test_connection(self):
        """
        测试 RTSP 流连接
        
        在后台线程中尝试连接指定的 RTSP 地址，
        获取一帧画面验证连接是否正常。
        测试完成后自动释放连接。
        """
        url = self.rtsp_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入RTSP地址")
            return

        self.log("正在测试连接...")
        self.set_status("测试连接中...")
        self.test_btn.config(state=tk.DISABLED, text="测试中")

        def _test():
            """后台测试线程"""
            self.log("正在连接RTSP流...")
            ok = self.snapshotter.connect(url)
            if ok:
                frame = self.snapshotter.capture_frame()
                if frame is not None:
                    self.log(f"连接成功，分辨率: {frame.shape[1]}x{frame.shape[0]}")
                    self.set_status("连接成功")
                else:
                    self.log("连接成功但无法获取画面")
                    self.set_status("连接成功(无画面)")
            else:
                self.log("连接失败，请检查RTSP地址")
                self.set_status("连接失败")
            self.snapshotter.release()
            self.root.after(0, lambda: self.test_btn.config(state=tk.NORMAL, text="测试连接"))

        threading.Thread(target=_test, daemon=True).start()

    def start_capture(self):
        """
        开始截图任务
        
        流程：
        1. 校验输入参数（RTSP地址、棋盘格尺寸、间隔、序列号、目标数量）
        2. 加载已有照片数据
        3. 保存配置
        4. 禁用 UI 控件，防止重复操作
        5. 启动后台截图线程
        6. 启动实时预览
        7. 语音播报任务开始
        """
        # 参数校验
        url = self.rtsp_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入RTSP地址")
            return

        cb_size = self.parse_chessboard_size()
        if cb_size is None:
            return

        interval = self.parse_interval()
        if interval is None:
            return

        serial = self.serial_var.get().strip()
        if not serial:
            messagebox.showerror("错误", "请输入序列号")
            return

        try:
            self._target_count = int(self.target_count_var.get().strip())
            if self._target_count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "目标拍照数量必须是正整数")
            return

        # 设置保存目录，加载已有照片
        self.save_dir = os.path.join("screenshots", serial)
        self._reprojection_errors.clear()
        self.new_capture_count = 0
        self._load_photos_sync(self.save_dir)
        
        # 根据已有照片数量决定是否启用重投影筛选按钮
        if self.current_photo_count >= self._target_count:
            self.reproject_btn.config(state=tk.NORMAL)
        else:
            self.reproject_btn.config(state=tk.DISABLED)

        # 保存配置和历史记录
        self._add_to_rtsp_history(url)
        self._save_config()

        # 禁用 UI 控件
        self._stop_event.clear()
        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.test_btn.config(state=tk.DISABLED)
        self.rtsp_entry.config(state=tk.DISABLED)
        self.serial_entry.config(state=tk.DISABLED)
        self.camera_combo.config(state=tk.DISABLED)
        self.backend_combo.config(state=tk.DISABLED)
        for b in [self.add_cam_btn, self.edit_cam_btn, self.del_cam_btn]:
            b.config(state=tk.DISABLED)

        # 设置棋盘格检测器
        self.detector.set_pattern_size(*cb_size)

        # 启动后台截图线程
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(url, interval),
            daemon=True
        )
        self._capture_thread.start()
        
        # 启动实时预览
        self._start_live_preview()
        
        # 语音播报
        self.announcer.announce_start()
        self.log(f"开始截图任务 | 序列号: {serial} | 保存目录: {self.save_dir} | 棋盘格: {cb_size[0]}x{cb_size[1]} | 间隔: {interval}秒")

    def stop_capture(self):
        self._stop_event.set()
        self.running = False
        self.log("正在停止截图任务...")
        self.set_status("正在停止...")

    def _capture_loop(self, url, interval):
        """
        截图主循环（在后台线程中运行）
        
        定时抓取帧并执行截图流程，支持断线自动重连。
        
        Args:
            url (str): RTSP 流地址
            interval (float): 截图间隔（秒）
            
        流程：
        1. 连接 RTSP 流
        2. 循环检查是否到达截图时间
        3. 调用 _do_capture() 执行单次截图
        4. 如果截图失败，等待后重连
        5. 收到停止信号后退出循环
        """
        # 连接 RTSP 流
        if not self.snapshotter.connect(url):
            self.log("无法连接到RTSP流")
            self.set_status("连接失败")
            self._cleanup_after_stop()
            return

        self.log("已连接到RTSP流")
        self.set_status("工作中")

        last_capture_time = 0  # 上次截图时间戳

        # 主循环
        while not self._stop_event.is_set():
            now = time.time()
            elapsed = now - last_capture_time

            # 检查是否到达截图时间
            if elapsed >= interval:
                if self._stop_event.is_set():
                    break

                try:
                    result = self._do_capture()
                except Exception as e:
                    self.log(f"截图异常: {e}")
                    result = None

                # 截图失败时重连
                if result is None:
                    self.log("截图失败，等待重试...")
                    self._stop_event.wait(2)
                    if not self._stop_event.is_set():
                        self.log("重新连接RTSP...")
                        self.snapshotter.release()
                        self.snapshotter.connect(url)
                    continue

                last_capture_time = time.time()

            # 短暂休眠，避免 CPU 空转
            self._stop_event.wait(0.2)

        # 退出循环，释放资源
        self.snapshotter.release()
        self._cleanup_after_stop()

    def _compute_histogram(self, bgr_frame):
        # 4×4×4 降采样 BGR 直方图，用于相似度比较
        small = cv2.resize(bgr_frame, (256, 256))
        hist = cv2.calcHist([small], [0, 1, 2], None, [4, 4, 4], [0, 256, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        return hist

    def _compute_ssim(self, gray1, gray2):
        # 简易 SSIM 实现，在 64×64 灰度图上计算
        g1 = gray1.astype(np.float64)
        g2 = gray2.astype(np.float64)
        mu1 = g1.mean()
        mu2 = g2.mean()
        sigma1_sq = ((g1 - mu1) ** 2).mean()
        sigma2_sq = ((g2 - mu2) ** 2).mean()
        sigma12 = ((g1 - mu1) * (g2 - mu2)).mean()
        C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        num = (2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)
        den = (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
        return np.clip(num / den, 0, 1) if den != 0 else 0.0

    def _compute_pose_signature(self, corners):
        pts = corners.reshape(-1, 2).astype(np.float64)
        center = pts.mean(axis=0)
        centered = pts - center
        scale = max(np.sqrt((centered**2).mean()), 1e-6)
        normalized = centered / scale
        return center, scale, normalized

    def _check_pose_similarity(self, corners):
        if not self.photo_data:
            return False
        center_dist_max = float(self._config.get('pose_center_dist', '50'))
        scale_min = float(self._config.get('pose_scale_min', '0.85'))
        scale_max = float(self._config.get('pose_scale_max', '1.15'))
        shape_diff_max = float(self._config.get('pose_shape_diff', '0.05'))
        new_center, new_scale, new_norm = self._compute_pose_signature(corners)
        for item in self.photo_data:
            saved_corners = item[3]
            if saved_corners is None:
                continue
            old_center, old_scale, old_norm = self._compute_pose_signature(saved_corners)
            center_dist = np.linalg.norm(new_center - old_center)
            if center_dist > center_dist_max:
                continue
            ratio = new_scale / old_scale
            if not (scale_min <= ratio <= scale_max):
                continue
            diff = np.sqrt(((new_norm - old_norm) ** 2).mean())
            if diff < shape_diff_max:
                self.log(f"与 {os.path.basename(item[0])} 姿态相似(差异={diff:.4f}), 舍弃")
                return True
        return False

    def _maybe_flip_frame(self, frame):
        if self.flip_var.get():
            return cv2.rotate(frame, cv2.ROTATE_180)
        return frame

    def _do_capture(self):
        """
        单次截图流程
        
        完整的截图处理流程，包括：
        1. 从 RTSP 流获取帧
        2. 可选的画面翻转
        3. 棋盘格角点检测
        4. 多重质量检测
        5. 姿态去重
        6. 保存图像
        
        Returns:
            bool/None: 
                - True: 截图成功或质量检测失败（已处理）
                - None: 帧获取失败（需要重连）
                
        质量检测流程：
        ┌─────────────┐
        │ 检测到角点？ │
        └──────┬──────┘
               │ 否 → 返回提示
               ▼ 是
        ┌─────────────┐
        │ 清晰度检测  │ → 模糊？→ 舍弃
        └──────┬──────┘
               ▼ 通过
        ┌─────────────┐
        │ 曝光检测    │ → 过曝/欠曝？→ 舍弃
        └──────┬──────┘
               ▼ 通过
        ┌─────────────┐
        │ 贴边检测    │ → 越界？→ 舍弃
        └──────┬──────┘
               ▼ 通过
        ┌─────────────┐
        │ 面积比检测  │ → <10%？→ 舍弃
        └──────┬──────┘
               ▼ 通过
        ┌─────────────┐
        │ 姿态去重    │ → 重复？→ 舍弃
        └──────┬──────┘
               ▼ 通过
        ┌─────────────┐
        │ 保存图像    │
        └─────────────┘
        """
        # 获取帧
        frame = self.snapshotter.capture_frame()
        if frame is None:
            return None
        
        # 可选的画面翻转（相机倒装时使用）
        frame = self._maybe_flip_frame(frame)

        # 棋盘格角点检测
        found, corners = self.detector.detect(frame)

        if found:
            # 质量检测①：清晰度（拉普拉斯方差）
            sharp_ok, sharp_val = self.detector.check_sharpness(frame)
            if not sharp_ok:
                self.log(f"图像模糊: Laplacian方差={sharp_val:.1f}, 判定无效")
                self._update_preview(frame)
                self.announcer.warn_incomplete()
                self.set_status("图像模糊")
                return True

            # 质量检测②：曝光（过暗/过亮像素占比）
            exp_ok, dark_r, bright_r = self.detector.check_exposure(frame)
            if not exp_ok:
                self.log(f"曝光异常: 欠曝{dark_r:.1%} 过曝{bright_r:.1%}, 判定无效")
                self._update_preview(frame)
                self.announcer.warn_incomplete()
                self.set_status("曝光异常")
                return True

            # 质量检测③：贴边检测（角点距图像边缘距离）
            bound_ok = self.detector.check_boundary(corners, frame.shape)
            if not bound_ok:
                self.log("棋盘格贴边或越界, 判定无效")
                self._update_preview(frame)
                self.announcer.warn_incomplete()
                self.set_status("棋盘格越界")
                return True

            # 质量检测④：面积比检测（棋盘格占图像面积比例）
            area_ratio = self.detector.compute_chessboard_area_ratio(frame, corners)
            area_ratio_min = float(self._config.get('area_ratio_min', '0.10'))

            if area_ratio < area_ratio_min:
                self.log(f"棋盘格面积占比: {area_ratio:.1%} < {area_ratio_min:.0%}, 已舍弃")
                drawn = self.detector.draw_corners(frame, corners)
                self._update_preview(drawn)
                self.announcer.warn_area_ratio()
                self.set_status(f"棋盘格占比不足: {area_ratio:.1%}")
                return True

            # 质量检测⑤：姿态去重
            if self._check_pose_similarity(corners):
                self._update_preview(frame)
                self.announcer.warn_duplicate()
                self.set_status("重复姿态")
                return True

            # 保存图像
            serial = self.serial_var.get().strip()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{serial}_{timestamp}.jpg"
            filepath = self.snapshotter.save_snapshot(frame, self.save_dir, filename)

            # 记录角点数量
            total = self.detector.get_expected_corners(self.detector.pattern_size)
            actual = len(corners)
            self.log(f"截图保存: {filename} | 角点: {actual}/{total} | 面积占比: {area_ratio:.1%}")

            # 更新预览和缩略图
            drawn = self.detector.draw_corners(frame, corners)
            self._update_preview(drawn)
            self._add_thumbnail(filepath, frame, corners)
            
            # 更新计数和状态
            self.new_capture_count += 1
            self.current_photo_count = len(self.photo_data)
            self._update_photo_count_display()
            self.announcer.announce_success()
            self.set_status(f"检测成功: {actual}/{total} ({self.current_photo_count}/{self._target_count})")

            # 检查是否达到目标数量
            if self.current_photo_count >= self._target_count:
                self.root.after(0, lambda: self.reproject_btn.config(state=tk.NORMAL))
                self.log(f"已采集{self._target_count}张照片，可点击「重投影误差筛选」进行筛选")
        else:
            # 未检测到完整棋盘格
            self.log("未检测到完整棋盘格角点")
            self._update_preview(frame)
            self.announcer.warn_incomplete()
            self.set_status("未检测到完整棋盘格")

        return True

    def _add_thumbnail(self, filepath, frame, corners=None):
        hist = self._compute_histogram(frame)
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 64))
        self.photo_data.append((filepath, hist, gray, corners, frame.shape))
        self.root.after(0, lambda: self._create_thumbnail_widget(filepath))

    def _create_thumbnail_widget(self, filepath, show_error=False):
        try:
            item_frame = ttk.Frame(self._thumb_container)
            item_frame.pack(fill=tk.X, pady=3, padx=3)
            pil_img = Image.open(filepath)
            pil_img.thumbnail((200, 130), Image.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img)
            self._thumb_refs.append(photo)
            label = ttk.Label(item_frame, image=photo, cursor="hand2")
            label.image = photo
            label.pack()
            label.bind("<Button-1>", lambda e, fp=filepath: self._show_large_image(fp))
            fname = os.path.basename(filepath)
            err = self._reprojection_errors.get(filepath)
            if err is not None and show_error:
                color = "#4caf50" if err <= 1.0 else "#f44336"
                fname += f"  err={err:.3f}px"
                ttk.Label(item_frame, text=fname, font=("", 7), foreground=color).pack()
            else:
                ttk.Label(item_frame, text=fname, font=("", 7), foreground="#aaaaaa").pack()
        except Exception as e:
            self.log(f"缩略图加载失败: {e}")

    def _show_large_image(self, filepath):
        try:
            win = tk.Toplevel(self.root)
            win.title(os.path.basename(filepath))
            win.geometry("900x700")
            win.minsize(500, 400)

            img = Image.open(filepath)
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            max_w = int(sw * 0.85)
            max_h = int(sh * 0.85)
            img.thumbnail((max_w, max_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            label = ttk.Label(win, image=photo)
            label.image = photo
            label.pack(fill=tk.BOTH, expand=True)

            info_frame = ttk.Frame(win)
            info_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(info_frame, text=f"文件: {os.path.basename(filepath)}", font=("", 9)).pack(side=tk.LEFT)
            err = self._reprojection_errors.get(filepath)
            if err is not None:
                color = "#4caf50" if err <= 1.0 else "#f44336"
                ttk.Label(info_frame, text=f"重投影误差: {err:.4f}px", font=("", 9), foreground=color).pack(side=tk.RIGHT, padx=(10, 0))
            ttk.Label(info_frame, text=f"尺寸: {img.width} × {img.height}", font=("", 9)).pack(side=tk.RIGHT)
        except Exception as e:
            self.log(f"查看大图失败: {e}")

    def _show_frame_on_label(self, frame, label):
        try:
            h, w = frame.shape[:2]
            pw = label.winfo_width()
            ph = label.winfo_height()
            max_w = max(pw - 10, 10) if pw > 10 else 320
            max_h = max(ph - 10, 10) if ph > 10 else 240
            scale = min(max_w / w, max_h / h, 1.0)
            new_w, new_h = int(w * scale), int(h * scale)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb).resize((new_w, new_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img)
            label.config(image=photo)
            if label == self.capture_preview_label:
                self._capture_preview_image = photo
            else:
                self._live_preview_image = photo
        except Exception as e:
            self.log(f"预览错误: {e}")

    def _update_preview(self, frame):
        self.root.after(0, lambda: self._show_frame_on_label(frame, self.capture_preview_label))

    def _start_live_preview(self):
        self._live_running = True
        self._live_update()

    def _stop_live_preview(self):
        self._live_running = False

    def _live_update(self):
        """
        实时预览更新
        
        在实时预览中添加辅助功能：
        1. 网格叠加：3×3 网格引导棋盘格放置
        2. 棋盘格检测：实时显示检测到的角点
        3. 距离提示：根据面积比提示距离
        4. 角度提示：根据形变程度提示角度
        5. 质量评分：显示当前帧的综合质量分数
        """
        if not self._live_running:
            return
        if self.running and self.snapshotter:
            frame = self.snapshotter.capture_frame()
            if frame is not None:
                frame = self._maybe_flip_frame(frame)
                
                # 添加实时辅助线
                frame_with_overlay = self._add_overlay_to_frame(frame)
                
                self.root.after(0, lambda f=frame_with_overlay: self._show_frame_on_label(f, self.live_preview_label))
        self.root.after(150, self._live_update)

    def _draw_text_chinese(self, img, text, position, font_size=20, color=(255, 255, 255)):
        """
        在 OpenCV 图像上绘制中文文字
        
        由于 OpenCV 的 putText 不支持中文，使用 PIL/Pillow 来渲染中文文字。
        
        Args:
            img (numpy.ndarray): BGR 格式的图像
            text (str): 要绘制的文字
            position (tuple): 文字位置 (x, y)
            font_size (int): 字体大小
            color (tuple): BGR 颜色
            
        Returns:
            numpy.ndarray: 绘制了文字的图像
        """
        # 将 OpenCV 图像转换为 PIL 图像
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        
        # 创建绘图对象（使用 ImageDraw，不是 ImageTk）
        draw = ImageDraw.Draw(img_pil)
        
        # 尝试使用系统中文字体
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
            "C:/Windows/Fonts/simsun.ttc",  # 宋体
            "C:/Windows/Fonts/simhei.ttf",  # 黑体
        ]
        
        font = None
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except:
                    continue
        
        if font is None:
            # 如果找不到中文字体，使用默认字体
            try:
                font = ImageFont.load_default()
            except:
                font = None
        
        # 将 BGR 颜色转换为 RGB
        color_rgb = (color[2], color[1], color[0])
        
        # 绘制文字
        draw.text(position, text, font=font, fill=color_rgb)
        
        # 转换回 OpenCV 图像
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    def _add_overlay_to_frame(self, frame):
        """
        为实时预览帧添加辅助线叠加层
        
        包含以下功能：
        1. 3×3 网格线（半透明白色）
        2. 棋盘格角点检测（如果检测到）
        3. 距离提示（基于面积比）
        4. 角度提示（基于形变程度）
        5. 综合质量评分
        
        Args:
            frame (numpy.ndarray): BGR 格式的输入帧
            
        Returns:
            numpy.ndarray: 添加了叠加层的帧
        """
        overlay = frame.copy()
        h, w = frame.shape[:2]
        
        # 1. 绘制 3×3 网格线（半透明白色）
        grid_color = (200, 200, 200)  # 浅灰色
        grid_thickness = 1
        # 垂直线
        for i in range(1, 3):
            x = w * i // 3
            cv2.line(overlay, (x, 0), (x, h), grid_color, grid_thickness)
        # 水平线
        for i in range(1, 3):
            y = h * i // 3
            cv2.line(overlay, (0, y), (w, y), grid_color, grid_thickness)
        
        # 2. 尝试检测棋盘格
        found, corners = self.detector.detect(frame)
        
        if found:
            # 绘制检测到的角点
            cv2.drawChessboardCorners(overlay, self.detector.pattern_size, corners, True)
            
            # 3. 计算并显示距离提示（基于面积比）
            area_ratio = self.detector.compute_chessboard_area_ratio(frame, corners)
            if area_ratio < 0.05:
                distance_text = "太远 - 请靠近"
                distance_color = (0, 0, 255)  # 红色
            elif area_ratio < 0.10:
                distance_text = "较远 - 可靠近些"
                distance_color = (0, 165, 255)  # 橙色
            elif area_ratio > 0.50:
                distance_text = "太近 - 请远离"
                distance_color = (0, 0, 255)  # 红色
            elif area_ratio > 0.30:
                distance_text = "较近 - 可远离些"
                distance_color = (0, 165, 255)  # 橙色
            else:
                distance_text = "距离合适"
                distance_color = (0, 255, 0)  # 绿色
            
            # 4. 计算并显示角度提示（基于形变程度）
            distortion_ok, condition_number, level = self.detector.check_distortion(corners, frame.shape)
            if level == "优秀":
                angle_text = f"角度优秀 ({condition_number:.2f})"
                angle_color = (0, 255, 0)  # 绿色
            elif level == "良好":
                angle_text = f"角度良好 ({condition_number:.2f})"
                angle_color = (0, 255, 0)  # 绿色
            elif level == "一般":
                angle_text = f"角度一般 ({condition_number:.2f})"
                angle_color = (0, 165, 255)  # 橙色
            else:
                angle_text = f"角度太大 ({condition_number:.2f})"
                angle_color = (0, 0, 255)  # 红色
            
            # 5. 计算综合质量评分
            quality_score, _ = self.detector.compute_quality_score(frame, corners)
            if quality_score >= 80:
                quality_color = (0, 255, 0)  # 绿色
            elif quality_score >= 60:
                quality_color = (0, 165, 255)  # 橙色
            else:
                quality_color = (0, 0, 255)  # 红色
            
            # 在图像上绘制信息
            font_size = 20
            
            # 绘制背景框
            info_height = 120
            cv2.rectangle(overlay, (0, 0), (w, info_height), (0, 0, 0), -1)
            cv2.rectangle(overlay, (0, 0), (w, info_height), (100, 100, 100), 2)
            
            # 绘制距离提示（使用中文渲染）
            overlay = self._draw_text_chinese(overlay, f"距离: {distance_text}", (10, 5), font_size, distance_color)
            
            # 绘制角度提示（使用中文渲染）
            overlay = self._draw_text_chinese(overlay, f"角度: {angle_text}", (10, 35), font_size, angle_color)
            
            # 绘制质量评分（使用中文渲染）
            overlay = self._draw_text_chinese(overlay, f"质量: {quality_score:.1f}/100", (10, 65), font_size, quality_color)
            
            # 绘制面积比（使用中文渲染）
            overlay = self._draw_text_chinese(overlay, f"面积比: {area_ratio:.1%}", (10, 95), font_size, (200, 200, 200))
            
        else:
            # 未检测到棋盘格，显示提示
            font_size = 22
            
            # 绘制半透明背景
            cv2.rectangle(overlay, (0, 0), (w, 50), (0, 0, 0), -1)
            cv2.rectangle(overlay, (0, 0), (w, 50), (100, 100, 100), 2)
            
            # 使用中文渲染
            overlay = self._draw_text_chinese(overlay, "未检测到棋盘格 - 请将棋盘格放入画面中央", (10, 12), font_size, (0, 165, 255))
        
        return overlay

    def manual_capture(self):
        """
        手动抓图功能
        
        从当前 RTSP 流获取一帧并保存到序列号文件夹下的"手动抓图"子文件夹。
        不进行质量检测，直接保存原始帧。
        """
        url = self.rtsp_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请先填写RTSP地址")
            return
        
        serial = self.serial_var.get().strip()
        if not serial:
            messagebox.showerror("错误", "请先填写序列号")
            return
        
        # 创建保存目录：screenshots/{序列号}/手动抓图/
        save_dir = os.path.join("screenshots", serial, "手动抓图")
        os.makedirs(save_dir, exist_ok=True)
        
        self.log("正在手动抓图...")
        self.manual_capture_btn.config(state=tk.DISABLED, text="抓图中")
        
        def _capture():
            # 连接 RTSP 流
            temp_snapshotter = RTSPSnapshotter(url)
            if not temp_snapshotter.connect():
                self.root.after(0, lambda: self.log("手动抓图失败：无法连接RTSP流"))
                self.root.after(0, lambda: self.manual_capture_btn.config(state=tk.NORMAL, text="手动抓图"))
                return
            
            # 获取帧
            frame = temp_snapshotter.capture_frame()
            temp_snapshotter.release()
            
            if frame is None:
                self.root.after(0, lambda: self.log("手动抓图失败：无法获取画面"))
                self.root.after(0, lambda: self.manual_capture_btn.config(state=tk.NORMAL, text="手动抓图"))
                return
            
            # 可选的画面翻转
            if self.flip_var.get():
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"manual_{serial}_{timestamp}.jpg"
            filepath = os.path.join(save_dir, filename)
            
            # 保存图像
            cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(filepath)
            
            self.root.after(0, lambda: self.log(f"手动抓图保存: {filename}"))
            self.root.after(0, lambda: self.set_status(f"手动抓图已保存: {filename}"))
            self.root.after(0, lambda: self.manual_capture_btn.config(state=tk.NORMAL, text="手动抓图"))
        
        threading.Thread(target=_capture, daemon=True).start()

    def _cleanup_after_stop(self):
        self.running = False
        self._stop_live_preview()
        self.root.after(0, self._reset_controls)
        self.log("截图任务已停止")
        self.set_status("已停止")

    def _reset_controls(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.test_btn.config(state=tk.NORMAL)
        self.serial_entry.config(state=tk.NORMAL)
        self.rtsp_entry.config(state=tk.NORMAL)
        self.camera_combo.config(state="readonly")
        self.backend_combo.config(state="readonly")
        for b in [self.add_cam_btn, self.edit_cam_btn, self.del_cam_btn]:
            b.config(state=tk.NORMAL)
        self.reproject_btn.config(state=tk.DISABLED)

    def _clear_thumbnails(self):
        for w in self._thumb_container.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

    def _update_photo_count_display(self):
        self._photo_list_frame.configure(text=f"已拍照片 ({self.current_photo_count}/{self._target_count})")

    def _run_reprojection_filter(self):
        # 对所有已拍照片做重投影误差检测，不合格照片移至 "不合格" 子目录
        if len(self.photo_data) < 2:
            self.log("至少需要2张照片才能进行重投影误差检测")
            return

        self.reproject_btn.config(state=tk.DISABLED, text="筛选中...")
        self.log("正在进行重投影误差检测...")

        def _task():
            try:
                pattern_size = self.detector.pattern_size
                good, bad, errors = ChessboardDetector.filter_by_reprojection_error(
                    self.photo_data, pattern_size, max_error=1.0
                )

                bad_dir = os.path.join(self.save_dir, "不合格")
                for item in bad:
                    fp = item[0]
                    try:
                        os.makedirs(bad_dir, exist_ok=True)
                        dst = os.path.join(bad_dir, os.path.basename(fp))
                        os.rename(fp, dst)
                    except:
                        pass
                    self.log(f"不合格(重投影误差高): {os.path.basename(fp)}")

                for item in good:
                    fp = item[0]
                    err = errors.get(fp, 0)
                    self.log(f"合格: {os.path.basename(fp)} 重投影误差={err:.3f}px")

                self._reprojection_errors.clear()
                self._reprojection_errors.update(errors)
                self.photo_data.clear()
                self.photo_data.extend(good)
                self.current_photo_count = len(self.photo_data)
                self.root.after(0, self._clear_thumbnails)
                for item in good:
                    fp = item[0]
                    self.root.after(0, lambda fpp=fp: self._create_thumbnail_widget(fpp, show_error=True))
                self.root.after(0, self._update_photo_count_display)
                self.root.after(0, lambda: self.log(f"重投影筛选完成: 合格{len(good)}张, 不合格{len(bad)}张"))
                self.root.after(0, lambda: self.set_status(f"筛选完成: 合格{len(good)}张"))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"重投影误差检测异常: {e}"))
            finally:
                self.root.after(0, lambda: self.reproject_btn.config(state=tk.NORMAL, text="重投影误差筛选"))

        threading.Thread(target=_task, daemon=True).start()

    def _on_serial_change(self, *args):
        if hasattr(self, '_serial_timer') and self._serial_timer:
            self.root.after_cancel(self._serial_timer)
        self._serial_timer = self.root.after(600, self._do_serial_change)

    def _do_serial_change(self):
        self._serial_timer = None
        if not self.running:
            serial = self.serial_var.get().strip()
            if serial:
                self.save_dir = os.path.join("screenshots", serial)
            else:
                self.save_dir = "screenshots"
            self._load_photos_from_directory()

    def _load_photos_sync(self, dirpath):
        # 同步加载目录照片（在调用线程执行，用于 start_capture 时确保数据一致）
        self.save_dir = dirpath
        self.photo_data.clear()
        self._clear_thumbnails()
        self.current_photo_count = 0
        self._reprojection_errors.clear()
        if not os.path.isdir(dirpath):
            self._update_photo_count_display()
            return
        files = sorted([f for f in os.listdir(dirpath) if f.lower().endswith('.jpg')])
        for fname in files:
            fp = os.path.join(dirpath, fname)
            try:
                frame = cv2.imread(fp)
                if frame is None:
                    continue
                found, corners = self.detector.detect(frame)
                hist = self._compute_histogram(frame)
                gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 64))
                self.photo_data.append((fp, hist, gray, corners if found else None, frame.shape))
                self._create_thumbnail_widget(fp)
            except:
                pass
        self.current_photo_count = len(self.photo_data)
        self._update_photo_count_display()
        if self.current_photo_count >= self._target_count and not self.running:
            self.reproject_btn.config(state=tk.NORMAL)

    def _load_photos_from_directory(self):
        # 后台线程加载（用于启动时和序列号切换，不阻塞界面）
        serial = self.serial_var.get().strip()
        if not serial:
            return
        dirpath = os.path.join("screenshots", serial)
        self.log("正在加载历史照片...")
        def _load():
            self._load_photos_sync(dirpath)
            self.root.after(0, lambda: self.log(f"已加载{len(self.photo_data)}张历史照片"))
        threading.Thread(target=_load, daemon=True).start()

    def open_screenshots_dir(self):
        os.makedirs(self.save_dir, exist_ok=True)
        os.startfile(self.save_dir)

    def on_closing(self):
        self._save_config()
        if self.osd_ctrl.is_disabled:
            self.osd_ctrl.enable_osd(
                self.brand_var.get(), self.ip_var.get().strip(),
                self.port_var.get().strip() or "554",
                self.user_var.get().strip(), self.pwd_var.get().strip(),
                self.channel_var.get().strip() or "1"
            )
        if self.running:
            self._stop_event.set()
            if self._capture_thread and self._capture_thread.is_alive():
                self._capture_thread.join(timeout=2)
        self.snapshotter.release()
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()


if __name__ == "__main__":
    app = App()
    app.run()
