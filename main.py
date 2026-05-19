import json
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import os
import cv2
import numpy as np
from PIL import Image, ImageTk
from datetime import datetime

from rtsp_capture import RTSPSnapshotter
from chessboard_detector import ChessboardDetector
from voice import VoiceAnnouncer
from osd_control import OSDController


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RTSP相机标定辅助工具")
        self.root.geometry("1280x800")
        self.root.minsize(1000, 600)

        # 核心组件
        self.snapshotter = RTSPSnapshotter()
        self.detector = ChessboardDetector()
        self.announcer = VoiceAnnouncer()
        self.osd_ctrl = OSDController()

        # 运行状态
        self.running = False
        self._stop_event = threading.Event()
        self._capture_thread = None
        self._live_running = False
        self._capture_preview_image = None
        self._live_preview_image = None

        # 照片数据存储
        self.photo_data = []          # (filepath, hist, gray, corners, image_shape)
        self._thumb_refs = []         # 保持 PhotoImage 引用防 GC
        self._reprojection_errors = {}  # filepath -> error
        self.current_photo_count = 0
        self.new_capture_count = 0
        self._target_count = 20
        self.save_dir = "screenshots"

        # 持久化配置
        self._history_file = os.path.join(self.save_dir, "rtsp_history.json")
        self._rtsp_history = self._load_rtsp_history()
        self._config_file = os.path.join(self.save_dir, "config.json")
        self._config = self._load_config()

        self._build_gui()
        self._center_window()
        self.root.after(500, self._load_photos_from_directory)

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_gui(self):
        # 整体布局：上（控制面板）| 下左（状态/日志/预览）下右（照片列表）
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self._build_control_area(main_frame)

        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        left_frame = ttk.Frame(content_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_status_bar(left_frame)
        self._build_log_area(left_frame)
        self._build_preview_area(left_frame)

        self._build_photo_list(content_frame)

    def _build_control_area(self, parent):
        frame = ttk.LabelFrame(parent, text="控制面板", padding=10)
        frame.pack(fill=tk.X, pady=(0, 5))

        gen_frame = ttk.Frame(frame)
        gen_frame.pack(fill=tk.X, pady=(0, 4))

        top_row = ttk.Frame(gen_frame)
        top_row.pack(fill=tk.X, pady=1)
        ttk.Label(top_row, text="品牌:").pack(side=tk.LEFT)
        self.brand_var = tk.StringVar(value=self._config.get('brand', '海康威视'))
        self.brand_combo = ttk.Combobox(top_row, textvariable=self.brand_var,
                                        values=["海康威视", "大华", "宇视", "自定义"],
                                        width=10, state="readonly")
        self.brand_combo.pack(side=tk.LEFT, padx=(3, 15))
        ttk.Label(top_row, text="IP地址:").pack(side=tk.LEFT)
        self.ip_var = tk.StringVar(value=self._config.get('ip', ''))
        self.ip_entry = ttk.Entry(top_row, textvariable=self.ip_var, width=16)
        self.ip_entry.pack(side=tk.LEFT, padx=(3, 15))
        ttk.Label(top_row, text="端口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=self._config.get('port', '554'))
        self.port_entry = ttk.Entry(top_row, textvariable=self.port_var, width=8)
        self.port_entry.pack(side=tk.LEFT, padx=(3, 0))

        mid_row = ttk.Frame(gen_frame)
        mid_row.pack(fill=tk.X, pady=1)
        ttk.Label(mid_row, text="用户名:").pack(side=tk.LEFT)
        self.user_var = tk.StringVar(value=self._config.get('username', 'admin'))
        self.user_entry = ttk.Entry(mid_row, textvariable=self.user_var, width=12)
        self.user_entry.pack(side=tk.LEFT, padx=(3, 15))
        ttk.Label(mid_row, text="密码:").pack(side=tk.LEFT)
        self.pwd_var = tk.StringVar(value=self._config.get('password', ''))
        self.pwd_entry = ttk.Entry(mid_row, textvariable=self.pwd_var, width=12, show="*")
        self.pwd_entry.pack(side=tk.LEFT, padx=(3, 15))
        ttk.Label(mid_row, text="通道号:").pack(side=tk.LEFT)
        self.channel_var = tk.StringVar(value=self._config.get('channel', '1'))
        self.channel_entry = ttk.Entry(mid_row, textvariable=self.channel_var, width=6)
        self.channel_entry.pack(side=tk.LEFT, padx=(3, 10))
        ttk.Button(mid_row, text="生成", command=self._generate_rtsp_url, width=6).pack(side=tk.LEFT)

        url_row = ttk.Frame(gen_frame)
        url_row.pack(fill=tk.X, pady=1)
        ttk.Label(url_row, text="RTSP地址:").pack(side=tk.LEFT)
        self.rtsp_var = tk.StringVar()
        self.rtsp_entry = ttk.Combobox(url_row, textvariable=self.rtsp_var, values=self._rtsp_history)
        self.rtsp_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        param_frame = ttk.Frame(frame)
        param_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(param_frame, text="棋盘格内角点:").pack(side=tk.LEFT)
        cb_frame = ttk.Frame(param_frame)
        cb_frame.pack(side=tk.LEFT, padx=(3, 15))
        self.cb_w_var = tk.StringVar(value=self._config.get('cb_w', '9'))
        self.cb_h_var = tk.StringVar(value=self._config.get('cb_h', '6'))
        ttk.Entry(cb_frame, textvariable=self.cb_w_var, width=5, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(cb_frame, text=" × ", font=("", 12)).pack(side=tk.LEFT)
        ttk.Entry(cb_frame, textvariable=self.cb_h_var, width=5, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(cb_frame, text="(宽 × 高)").pack(side=tk.LEFT, padx=3)
        ttk.Label(param_frame, text="截图间隔:").pack(side=tk.LEFT)
        self.interval_var = tk.StringVar(value=self._config.get('interval', '5'))
        interval_frame = ttk.Frame(param_frame)
        interval_frame.pack(side=tk.LEFT, padx=(3, 15))
        ttk.Entry(interval_frame, textvariable=self.interval_var, width=6, justify=tk.CENTER).pack(side=tk.LEFT)
        ttk.Label(interval_frame, text="秒").pack(side=tk.LEFT, padx=2)
        ttk.Label(param_frame, text="序列号:").pack(side=tk.LEFT)
        self.serial_var = tk.StringVar(value=self._config.get('serial', 'A001'))
        self.serial_entry = ttk.Entry(param_frame, textvariable=self.serial_var, width=10, justify=tk.CENTER)
        self.serial_entry.pack(side=tk.LEFT, padx=(3, 0))
        self.serial_var.trace_add("write", self._on_serial_change)
        ttk.Label(param_frame, text="目标数量:").pack(side=tk.LEFT, padx=(15, 0))
        self.target_count_var = tk.StringVar(value=self._config.get('target_count', '20'))
        ttk.Entry(param_frame, textvariable=self.target_count_var, width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=(3, 0))
        ttk.Label(param_frame, text="张").pack(side=tk.LEFT, padx=2)

        osd_frame = ttk.LabelFrame(frame, text="OSD清除", padding=5)
        osd_frame.pack(fill=tk.X, pady=(0, 4))
        osd_top = ttk.Frame(osd_frame)
        osd_top.pack(fill=tk.X)
        self.osd_clear_btn = ttk.Button(osd_top, text="清除OSD", command=self._disable_osd, width=10)
        self.osd_clear_btn.pack(side=tk.LEFT, padx=2)
        self.osd_restore_btn = ttk.Button(osd_top, text="恢复OSD", command=self._enable_osd, width=10)
        self.osd_restore_btn.pack(side=tk.LEFT, padx=2)
        self.osd_status_var = tk.StringVar(value="OSD: 未操作")
        ttk.Label(osd_top, textvariable=self.osd_status_var, foreground="#888").pack(side=tk.LEFT, padx=(10, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        self.start_btn = ttk.Button(btn_frame, text="开始截图", command=self.start_capture, width=12)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_capture, state=tk.DISABLED, width=12)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.test_btn = ttk.Button(btn_frame, text="测试连接", command=self.test_connection, width=12)
        self.test_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="打开截图目录", command=self.open_screenshots_dir, width=14).pack(side=tk.LEFT, padx=5)
        self.reproject_btn = ttk.Button(btn_frame, text="重投影误差筛选", command=self._run_reprojection_filter, width=14, state=tk.DISABLED)
        self.reproject_btn.pack(side=tk.LEFT, padx=5)

        self.brand_combo.bind("<<ComboboxSelected>>", self._on_brand_change)
        self.ip_var.trace_add("write", self._on_rtsp_field_change)
        self.port_var.trace_add("write", self._on_rtsp_field_change)
        self.user_var.trace_add("write", self._on_rtsp_field_change)
        self.pwd_var.trace_add("write", self._on_rtsp_field_change)
        self.channel_var.trace_add("write", self._on_rtsp_field_change)

        brand = self.brand_var.get()
        self.rtsp_entry.config(state=tk.NORMAL if brand == "自定义" else "readonly")
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
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/live/channels/channel{channel}"
        else:
            url = ""
        self.rtsp_var.set(url)
        if save_history and url:
            self._add_to_rtsp_history(url)

    def _on_brand_change(self, event=None):
        brand = self.brand_var.get()
        if brand == "自定义":
            self.rtsp_entry.config(state=tk.NORMAL)
        else:
            self.rtsp_entry.config(state="readonly")
            self._generate_rtsp_url()

    def _on_rtsp_field_change(self, *args):
        if self.brand_var.get() != "自定义":
            self._generate_rtsp_url(save_history=False)

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
                return json.load(f)
        except:
            return {}

    def _save_config(self):
        config = {
            'brand': self.brand_var.get(),
            'ip': self.ip_var.get(),
            'port': self.port_var.get(),
            'username': self.user_var.get(),
            'password': self.pwd_var.get(),
            'channel': self.channel_var.get(),
            'cb_w': self.cb_w_var.get(),
            'cb_h': self.cb_h_var.get(),
            'interval': self.interval_var.get(),
            'serial': self.serial_var.get(),
            'target_count': self.target_count_var.get(),
        }
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _disable_osd(self):
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
        url = self.rtsp_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入RTSP地址")
            return

        self.log("正在测试连接...")
        self.set_status("测试连接中...")
        self.test_btn.config(state=tk.DISABLED, text="测试中")

        def _test():
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
        # 开始截图：校验参数 → 初始化状态 → 启动后台截图线程
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

        self.save_dir = os.path.join("screenshots", serial)
        self._reprojection_errors.clear()
        self.new_capture_count = 0
        self._load_photos_sync(self.save_dir)
        if self.current_photo_count >= self._target_count:
            self.reproject_btn.config(state=tk.NORMAL)
        else:
            self.reproject_btn.config(state=tk.DISABLED)

        self._add_to_rtsp_history(url)
        self._save_config()

        self._stop_event.clear()
        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.test_btn.config(state=tk.DISABLED)
        self.rtsp_entry.config(state=tk.DISABLED)
        self.serial_entry.config(state=tk.DISABLED)
        self.target_count_var.set(str(self._target_count))
        self.brand_combo.config(state=tk.DISABLED)
        for e in [self.ip_entry, self.port_entry, self.user_entry, self.pwd_entry, self.channel_entry]:
            e.config(state=tk.DISABLED)

        self.detector.set_pattern_size(*cb_size)

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(url, interval),
            daemon=True
        )
        self._capture_thread.start()
        self._start_live_preview()
        self.announcer.announce_start()
        self.log(f"开始截图任务 | 序列号: {serial} | 保存目录: {self.save_dir} | 棋盘格: {cb_size[0]}x{cb_size[1]} | 间隔: {interval}秒")

    def stop_capture(self):
        self._stop_event.set()
        self.running = False
        self.log("正在停止截图任务...")
        self.set_status("正在停止...")

    def _capture_loop(self, url, interval):
        # 截图主循环：定时调用 _do_capture，断线自动重连
        if not self.snapshotter.connect(url):
            self.log("无法连接到RTSP流")
            self.set_status("连接失败")
            self._cleanup_after_stop()
            return

        self.log("已连接到RTSP流")
        self.set_status("工作中")

        last_capture_time = 0

        while not self._stop_event.is_set():
            now = time.time()
            elapsed = now - last_capture_time

            if elapsed >= interval:
                if self._stop_event.is_set():
                    break

                try:
                    result = self._do_capture()
                except Exception as e:
                    self.log(f"截图异常: {e}")
                    result = None

                if result is None:
                    self.log("截图失败，等待重试...")
                    self._stop_event.wait(2)
                    if not self._stop_event.is_set():
                        self.log("重新连接RTSP...")
                        self.snapshotter.release()
                        self.snapshotter.connect(url)
                    continue

                last_capture_time = time.time()

            self._stop_event.wait(0.2)

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
        new_center, new_scale, new_norm = self._compute_pose_signature(corners)
        for item in self.photo_data:
            saved_corners = item[3]
            if saved_corners is None:
                continue
            old_center, old_scale, old_norm = self._compute_pose_signature(saved_corners)
            center_dist = np.linalg.norm(new_center - old_center)
            if center_dist > 50:
                continue
            ratio = new_scale / old_scale
            if not (0.85 <= ratio <= 1.15):
                continue
            diff = np.sqrt(((new_norm - old_norm) ** 2).mean())
            if diff < 0.05:
                self.log(f"与 {os.path.basename(item[0])} 姿态相似(差异={diff:.4f}), 舍弃")
                return True
        return False

    def _do_capture(self):
        # 单次截图流程：取帧 → 棋盘格检测 → 质量检测(清晰度/曝光/贴边/面积/去重) → 保存
        frame = self.snapshotter.capture_frame()
        if frame is None:
            return None

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

            # Quality check 2: exposure
            exp_ok, dark_r, bright_r = self.detector.check_exposure(frame)
            if not exp_ok:
                self.log(f"曝光异常: 欠曝{dark_r:.1%} 过曝{bright_r:.1%}, 判定无效")
                self._update_preview(frame)
                self.announcer.warn_incomplete()
                self.set_status("曝光异常")
                return True

            # Quality check 3: boundary (checkerboard not touching edges)
            bound_ok = self.detector.check_boundary(corners, frame.shape)
            if not bound_ok:
                self.log("棋盘格贴边或越界, 判定无效")
                self._update_preview(frame)
                self.announcer.warn_incomplete()
                self.set_status("棋盘格越界")
                return True

            area_ratio = self.detector.compute_chessboard_area_ratio(frame, corners)

            if area_ratio < 0.10:
                self.log(f"棋盘格面积占比: {area_ratio:.1%} < 10%, 已舍弃")
                drawn = self.detector.draw_corners(frame, corners)
                self._update_preview(drawn)
                self.announcer.warn_area_ratio()
                self.set_status(f"棋盘格占比不足: {area_ratio:.1%}")
                return True

            if self._check_pose_similarity(corners):
                self._update_preview(frame)
                self.announcer.warn_duplicate()
                self.set_status("重复姿态")
                return True

            serial = self.serial_var.get().strip()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{serial}_{timestamp}.jpg"
            filepath = self.snapshotter.save_snapshot(frame, self.save_dir, filename)

            total = self.detector.get_expected_corners(self.detector.pattern_size)
            actual = len(corners)
            self.log(f"截图保存: {filename} | 角点: {actual}/{total} | 面积占比: {area_ratio:.1%}")

            drawn = self.detector.draw_corners(frame, corners)
            self._update_preview(drawn)
            self._add_thumbnail(filepath, frame, corners)
            self.new_capture_count += 1
            self.current_photo_count = len(self.photo_data)
            self._update_photo_count_display()
            self.announcer.announce_success()
            self.set_status(f"检测成功: {actual}/{total} ({self.current_photo_count}/{self._target_count})")

            if self.current_photo_count >= self._target_count:
                self.root.after(0, lambda: self.reproject_btn.config(state=tk.NORMAL))
                self.log(f"已采集{self._target_count}张照片，可点击「重投影误差筛选」进行筛选")
        else:
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
        if not self._live_running:
            return
        if self.running and self.snapshotter:
            frame = self.snapshotter.capture_frame()
            if frame is not None:
                self.root.after(0, lambda f=frame: self._show_frame_on_label(f, self.live_preview_label))
        self.root.after(150, self._live_update)

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
        self.brand_combo.config(state="readonly")
        for e in [self.ip_entry, self.port_entry, self.user_entry, self.pwd_entry, self.channel_entry]:
            e.config(state=tk.NORMAL)
        brand = self.brand_var.get()
        self.rtsp_entry.config(state=tk.NORMAL if brand == "自定义" else "readonly")
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
