import threading
import queue
import time


class VoiceAnnouncer:
    def __init__(self):
        self._queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        # 后台 TTS 线程：使用 Windows SAPI 语音合成
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        while True:
            text = self._queue.get()
            if text is None:
                break
            try:
                voice.Speak(text)
            except Exception as e:
                print(f"[TTS错误] {e}")

    def say(self, text):
        # 将播报文本放入队列，由后台线程消费
        self._queue.put(text)

    def announce_time(self):
        now = time.localtime()
        time_str = time.strftime("%Y年%m月%d日 %H点%M分%S秒", now)
        text = f"现在时间 {time_str}"
        self.say(text)
        return time_str

    def warn_incomplete(self):
        self.say("角点未拍全，请调整棋盘格位置")

    def announce_success(self):
        self.say("棋盘格检测成功")

    def announce_start(self):
        self.say("开始截图任务")

    def warn_duplicate(self):
        self.say("图片与已拍照片重复，已自动舍弃")

    def warn_area_ratio(self):
        self.say("棋盘格在画面中面积占比不足，请靠近棋盘格")
