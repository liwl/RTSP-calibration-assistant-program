import cv2
import os
import threading
import time
from datetime import datetime


class RTSPSnapshotter:
    def __init__(self, rtsp_url=None):
        self.rtsp_url = rtsp_url
        self.cap = None
        self._latest_frame = None
        self._last_frame_time = 0
        self._lock = threading.Lock()
        self._reading = False
        self._reader_thread = None

    def connect(self, rtsp_url=None):
        # 连接 RTSP 流并启动后台读帧线程
        if rtsp_url:
            self.rtsp_url = rtsp_url
        self.release()
        self.cap = cv2.VideoCapture(self.rtsp_url)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            return False
        self._latest_frame = None
        self._last_frame_time = 0
        self._reading = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        time.sleep(0.5)
        return self._latest_frame is not None

    def _read_loop(self):
        # 后台循环：持续读取最新帧，避免 VideoCapture 缓冲区堆积
        while self._reading:
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret:
                    with self._lock:
                        self._latest_frame = frame
                        self._last_frame_time = time.time()
            else:
                time.sleep(0.5)
            time.sleep(0.01)

    def capture_frame(self, max_age=5.0):
        # 返回最新帧的副本（线程安全）；超过 max_age 未更新返回 None
        with self._lock:
            if self._latest_frame is None:
                return None
            if time.time() - self._last_frame_time > max_age:
                return None
            return self._latest_frame.copy()

    def save_snapshot(self, frame, save_dir="screenshots", filename=None):
        # 保存 JPEG（品质 95）
        os.makedirs(save_dir, exist_ok=True)
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"snapshot_{timestamp}.jpg"
        filepath = os.path.join(save_dir, filename)
        cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(filepath)
        return filepath

    def release(self):
        # 释放所有资源
        self._reading = False
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        if self.cap:
            self.cap.release()
            self.cap = None
        self._latest_frame = None

    def __del__(self):
        self.release()
