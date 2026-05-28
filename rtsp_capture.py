"""
RTSP 流捕获模块

本模块提供 RTSP 视频流的实时帧抓取功能，支持两种后端实现：
1. FFmpeg 子进程模式：通过 FFmpeg 命令行工具解码 RTSP 流，输出原始视频帧
2. OpenCV VideoCapture 模式：使用 OpenCV 内置的 RTSP 客户端

主要特点：
- 后台线程持续读取，解决缓冲区堆积问题
- 线程安全的帧获取接口
- 自动探测视频分辨率
- 支持 PyInstaller 打包后的 FFmpeg 路径解析

使用示例：
    snapshotter = RTSPSnapshotter()
    if snapshotter.connect("rtsp://user:pass@ip:port/stream"):
        frame = snapshotter.capture_frame()
        if frame is not None:
            # 处理帧
            pass
    snapshotter.release()
"""

import cv2
import numpy as np
import os
import re
import subprocess as sp
import sys
import threading
import time
from datetime import datetime


def _get_ffmpeg_path():
    """
    获取 FFmpeg 可执行文件路径
    
    路径解析优先级：
    1. 项目目录下的 ffmpeg/ffmpeg.exe（本地打包版本）
    2. PyInstaller 打包后的 sys._MEIPASS/ffmpeg/ffmpeg.exe
    3. 系统 PATH 中的 ffmpeg（兜底方案）
    
    Returns:
        str: FFmpeg 可执行文件的完整路径
        
    Raises:
        无异常，找不到时返回 "ffmpeg"，依赖系统 PATH
    """
    # 本地路径：与本文件同级的 ffmpeg 目录
    local_path = os.path.join(os.path.dirname(__file__), "ffmpeg", "ffmpeg.exe")
    if os.path.isfile(local_path):
        return local_path
    
    # PyInstaller 打包后的临时目录
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(sys._MEIPASS, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
    
    # 兜底：依赖系统 PATH
    return "ffmpeg"


class RTSPSnapshotter:
    """
    RTSP 流帧抓取器
    
    支持 FFmpeg 和 OpenCV 双后端的 RTSP 视频流实时帧抓取。
    通过后台线程持续读取最新帧，解决 cv2.VideoCapture 缓冲区堆积问题。
    
    特性：
    - 线程安全：capture_frame() 返回帧的深拷贝，避免竞态条件
    - 自动重连：检测到断流时自动尝试重新连接
    - 双后端支持：FFmpeg 模式（推荐）和 OpenCV 模式可切换
    - 分辨率自动探测：FFmpeg 模式下自动解析视频流分辨率
    
    使用流程：
    1. 创建实例：snapshotter = RTSPSnapshotter()
    2. 连接流：snapshotter.connect(rtsp_url)
    3. 获取帧：frame = snapshotter.capture_frame()
    4. 释放资源：snapshotter.release()
    
    线程模型：
    - 主线程：调用 connect/capture_frame/release
    - 读取线程：后台持续读取最新帧（_reader_thread）
    - 错误处理线程：FFmpeg 模式下排空 stderr（_drain_stderr）
    """
    
    def __init__(self, rtsp_url=None):
        """
        初始化 RTSP 帧抓取器
        
        Args:
            rtsp_url (str, optional): RTSP 流地址，格式：
                rtsp://username:password@ip:port/stream_path
                例如：rtsp://admin:12345@192.168.1.64:554/Streaming/Channels/101
        """
        self.rtsp_url = rtsp_url
        self._use_ffmpeg = True  # 默认使用 FFmpeg 子进程模式
        
        # OpenCV VideoCapture 模式字段
        self._cap = None              # cv2.VideoCapture 实例
        self._cv_reader_thread = None  # OpenCV 读取线程（备用）
        
        # FFmpeg 子进程模式字段
        self._process = None          # subprocess.Popen 实例
        
        # 通用字段（双模式共享）
        self._latest_frame = None     # 最新帧缓存（numpy array）
        self._last_frame_time = 0     # 最新帧的时间戳（time.time()）
        self._lock = threading.Lock() # 帧缓存的线程锁
        self._reading = False         # 读取线程运行标志
        self._reader_thread = None    # 后台读取线程
        self._width = 0               # 视频宽度（像素）
        self._height = 0              # 视频高度（像素）

    def set_backend(self, use_ffmpeg):
        """
        设置视频流后端类型
        
        Args:
            use_ffmpeg (bool): True 使用 FFmpeg 子进程，False 使用 OpenCV VideoCapture
            
        Raises:
            RuntimeError: 如果正在读取中调用此方法
            
        注意：
            必须在连接前调用，正在读取时不能切换后端
        """
        if self._reading:
            raise RuntimeError("请在断开连接后切换后端")
        self._use_ffmpeg = use_ffmpeg

    def connect(self, rtsp_url=None):
        """
        连接到 RTSP 流
        
        自动释放之前的连接，建立新的连接并启动后台读取线程。
        
        Args:
            rtsp_url (str, optional): RTSP 流地址。如果提供，将更新实例的 rtsp_url 属性。
            
        Returns:
            bool: 连接成功返回 True，失败返回 False
            
        连接流程：
        1. 释放现有连接
        2. 根据后端类型选择连接方法
        3. FFmpeg 模式：探测分辨率 → 启动 FFmpeg 进程 → 启动读取线程
        4. OpenCV 模式：创建 VideoCapture → 启动读取线程
        5. 等待首帧到达（超时 10 秒）
        """
        if rtsp_url:
            self.rtsp_url = rtsp_url
        self.release()

        if self._use_ffmpeg:
            return self._connect_ffmpeg()
        else:
            return self._connect_opencv()

    # ── FFmpeg 子进程模式 ──

    def _connect_ffmpeg(self):
        """
        通过 FFmpeg 子进程连接 RTSP 流
        
        流程：
        1. 运行探测命令获取视频分辨率
        2. 构建 FFmpeg 命令，输出 BGR24 原始帧到 stdout
        3. 启动子进程和后台读取线程
        4. 等待首帧到达
        
        FFmpeg 参数说明：
        -rtsp_flags +prefer_tcp: 优先使用 TCP 传输（更稳定）
        -allowed_media_types video: 仅处理视频流
        -f rawvideo: 输出原始视频帧
        -pix_fmt bgr24: BGR 颜色格式（与 OpenCV 兼容）
        -an: 禁用音频处理
        
        Raises:
            RuntimeError: FFmpeg 未找到或无法执行
            
        Returns:
            bool: 连接成功返回 True，超时或失败返回 False
        """
        # 探测分辨率：运行短时 FFmpeg 命令解析 stderr 输出
        probe_cmd = [
            _get_ffmpeg_path(), "-rtsp_flags", "+prefer_tcp",
            "-allowed_media_types", "video",
            "-i", self.rtsp_url,
            "-t", "0.5", "-f", "null", "-",
        ]
        try:
            probe_result = sp.run(probe_cmd, stdout=sp.PIPE, stderr=sp.PIPE, timeout=15)
        except FileNotFoundError:
            raise RuntimeError(
                "系统中未找到 ffmpeg，请安装 ffmpeg 并将其加入 PATH "
                "或将 ffmpeg.exe 放入项目 ffmpeg/ 目录"
            )
        except sp.TimeoutExpired:
            pass  # 超时也可能已获取到足够信息
        
        # 解析分辨率：从 stderr 中匹配 "1920x1080" 格式
        stderr_text = probe_result.stderr.decode("utf-8", errors="replace")
        size_match = re.search(r"(\d+)x(\d+)", stderr_text)
        if size_match:
            self._width = int(size_match.group(1))
            self._height = int(size_match.group(2))
        else:
            # 默认分辨率
            self._width, self._height = 1280, 720

        # 计算单帧字节数：width × height × 3（BGR 三通道）
        self._frame_size = self._width * self._height * 3
        
        # 构建 FFmpeg 命令：输出原始帧到 stdout
        cmd = [
            _get_ffmpeg_path(), "-rtsp_flags", "+prefer_tcp",
            "-allowed_media_types", "video",
            "-i", self.rtsp_url,
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-an", "-"
        ]
        
        # 启动子进程，stdout 用于读取帧数据，stderr 用于日志
        # bufsize=1MB，避免频繁系统调用
        self._process = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=2**20)
        
        # 启动守护线程持续排空 stderr，防止进程阻塞
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        
        # 启动帧读取线程
        self._reading = True
        self._reader_thread = threading.Thread(target=self._read_loop_ffmpeg, daemon=True)
        self._reader_thread.start()
        
        # 等待首帧到达，超时 10 秒
        return self._wait_for_frame(10)

    def _drain_stderr(self):
        """
        后台排空 FFmpeg 进程的 stderr 输出
        
        FFmpeg 会将日志和错误信息输出到 stderr。如果不持续读取，
        缓冲区满后会导致 FFmpeg 进程阻塞。
        
        此方法在守护线程中运行，持续读取直到进程退出。
        """
        try:
            for _ in iter(lambda: self._process.stderr.read(4096), b""):
                pass
        except:
            pass  # 进程已退出或管道已关闭

    def _read_loop_ffmpeg(self):
        """
        FFmpeg 模式的帧读取循环
        
        从 FFmpeg 进程的 stdout 读取原始 BGR24 帧数据，
        解析为 numpy 数组并更新到帧缓存。
        
        数据格式：
        - 每帧大小：width × height × 3 字节
        - 字节顺序：BGR（与 OpenCV 一致）
        - 内存布局：逐行存储，先 B 后 G 再 R
        
        线程安全：
        使用 threading.Lock 保护帧缓存的读写操作
        """
        stdout = self._process.stdout
        frame_size = self._frame_size
        buffer = b""  # 帧数据缓冲区，处理不完整帧
        
        while self._reading:
            try:
                # 读取 2 帧大小的数据（减少系统调用频率）
                chunk = stdout.read(frame_size * 2)
                if not chunk:
                    break  # 流结束
                
                buffer += chunk
                
                # 处理缓冲区中所有完整帧
                while len(buffer) >= frame_size:
                    raw = buffer[:frame_size]
                    buffer = buffer[frame_size:]
                    
                    # 将原始字节转换为 BGR 图像数组
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                        (self._height, self._width, 3)
                    ).copy()
                    
                    # 更新帧缓存（线程安全）
                    with self._lock:
                        self._latest_frame = frame
                        self._last_frame_time = time.time()
            except:
                break  # 进程退出或读取异常

    # ── OpenCV 原生模式 ──

    def _connect_opencv(self):
        """
        通过 OpenCV VideoCapture 连接 RTSP 流
        
        使用 OpenCV 内置的 FFmpeg 后端（cv2.CAP_FFMPEG）读取 RTSP 流。
        
        优缺点：
        - 优点：无需单独管理 FFmpeg 进程，API 简洁
        - 缺点：缓冲区可能导致帧延迟，实时性不如直接 FFmpeg 子进程
        
        Returns:
            bool: 连接成功返回 True，失败返回 False
        """
        # 创建 VideoCapture，指定使用 FFmpeg 后端
        self._cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        
        # 设置缓冲区大小为 1 帧，减少延迟
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not self._cap.isOpened():
            return False
        
        # 获取实际视频分辨率
        self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # 启动帧读取线程
        self._reading = True
        self._reader_thread = threading.Thread(target=self._read_loop_opencv, daemon=True)
        self._reader_thread.start()
        
        # 等待首帧到达
        return self._wait_for_frame(10)

    def _read_loop_opencv(self):
        """
        OpenCV 模式的帧读取循环
        
        周期性调用 cap.read() 获取最新帧。
        注意：OpenCV 的 read() 会返回缓冲区中的旧帧，
        因此循环间隔设为 10ms 以保持实时性。
        """
        while self._reading:
            if self._cap and self._cap.isOpened():
                ret, frame = self._cap.read()
                if ret:
                    with self._lock:
                        self._latest_frame = frame
                        self._last_frame_time = time.time()
            else:
                time.sleep(0.5)  # 连接断开，等待重连
            time.sleep(0.01)  # 10ms 间隔，平衡 CPU 使用和实时性

    # ── 通用 ──

    def _wait_for_frame(self, timeout):
        """
        等待首帧到达
        
        在指定超时时间内持续检查帧缓存，直到收到第一帧。
        同时检测进程异常退出。
        
        Args:
            timeout (float): 超时时间（秒）
            
        Returns:
            bool: 收到首帧返回 True，超时或进程退出返回 False
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 检测 FFmpeg 进程是否已退出
            if self._process and self._process.poll() is not None:
                self._reading = False
                return False
            
            # 检测 OpenCV 连接是否已断开
            if self._cap and not self._cap.isOpened():
                self._reading = False
                return False
            
            # 检查帧缓存是否已有数据
            with self._lock:
                if self._latest_frame is not None:
                    return True
            
            time.sleep(0.2)  # 200ms 检查间隔
        
        self._reading = False
        return False

    def capture_frame(self, max_age=5.0):
        """
        获取最新帧（线程安全）
        
        返回帧缓存中最新帧的深拷贝。如果帧太旧（超过 max_age 秒）或不存在，
        返回 None。
        
        Args:
            max_age (float): 帧最大存活时间（秒），默认 5.0 秒。
                超过此时间的帧视为过期，返回 None。
            
        Returns:
            numpy.ndarray or None: BGR 格式的图像帧，或 None
            
        线程安全说明：
        返回帧的深拷贝，确保调用者持有独立副本，
        不会与后台读取线程产生竞态条件。
        """
        with self._lock:
            if self._latest_frame is None:
                return None
            # 检查帧是否过期
            if time.time() - self._last_frame_time > max_age:
                return None
            # 返回深拷贝，避免竞态条件
            return self._latest_frame.copy()

    def save_snapshot(self, frame, save_dir="screenshots", filename=None):
        """
        将帧保存为 JPEG 图像文件
        
        Args:
            frame (numpy.ndarray): BGR 格式的图像帧
            save_dir (str): 保存目录，默认 "screenshots"
            filename (str, optional): 文件名。为 None 时自动生成时间戳文件名。
            
        Returns:
            str: 保存的文件完整路径
            
        文件命名规则：
        - 自动生成：snapshot_YYYYMMDD_HHMMSS_mmm.jpg
        - 质量：JPEG 95%（高质量）
        """
        os.makedirs(save_dir, exist_ok=True)
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"snapshot_{timestamp}.jpg"
        filepath = os.path.join(save_dir, filename)
        # 编码为 JPEG 并写入文件
        cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(filepath)
        return filepath

    def release(self):
        """
        释放所有资源
        
        停止读取线程，终止 FFmpeg 进程（如有），
        释放 VideoCapture（如有），清空帧缓存。
        
        此方法可安全多次调用，内部有状态检查。
        """
        self._reading = False
        
        # 等待读取线程退出
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        
        # 终止 FFmpeg 进程
        if self._process:
            self._process.kill()
            try:
                self._process.wait(timeout=3)
            except:
                pass
            self._process = None
        
        # 释放 VideoCapture
        if self._cap:
            self._cap.release()
            self._cap = None
        
        # 清空帧缓存
        self._latest_frame = None

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.release()
