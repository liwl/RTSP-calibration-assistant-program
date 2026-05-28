"""
语音播报模块

本模块提供 TTS (Text-to-Speech) 语音播报功能，
使用 Windows SAPI (Speech API) 进行语音合成。

主要特点：
- 后台线程异步播报，不阻塞主线程
- 使用队列模型，支持并发调用
- 自动初始化 COM 线程

使用示例：
    announcer = VoiceAnnouncer()
    announcer.say("你好，世界")
    announcer.announce_success()
    
注意：
    仅支持 Windows 系统，需要 Microsoft Speech API。
    Windows 10/11 默认自带。
"""

import threading
import queue
import time


class VoiceAnnouncer:
    """
    语音播报器
    
    通过 Windows SAPI 进行语音合成播报。
    使用后台线程和队列模型，避免阻塞 UI。
    
    线程模型：
    - 调用线程：调用 say() 方法，将文本放入队列
    - 播报线程：从队列取出文本，调用 SAPI.Speak() 播报
    
    使用流程：
    1. 创建实例（自动启动后台线程）
    2. 调用 say() 或预定义的 announce_xxx/warn_xxx 方法
    3. 程序退出时后台线程自动终止
    """
    
    def __init__(self):
        """初始化语音播报器，启动后台播报线程"""
        self._queue = queue.Queue()
        # 启动守护线程（程序退出时自动终止）
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        """
        后台播报线程
        
        初始化 Windows COM 线程和 SAPI 语音对象，
        然后持续从队列中取文本并播报。
        """
        import pythoncom
        pythoncom.CoInitialize()
        import win32com.client
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        
        while True:
            text = self._queue.get()
            if text is None:
                break  # 退出信号
            try:
                voice.Speak(text)
            except Exception as e:
                print(f"[TTS错误] {e}")

    def say(self, text):
        """
        播报指定文本
        
        将文本放入队列，由后台线程异步播报。
        
        Args:
            text (str): 要播报的文本内容
        """
        self._queue.put(text)

    def announce_time(self):
        """
        播报当前时间
        
        格式：现在时间 2024年01月01日 12点00分00秒
        
        Returns:
            str: 格式化的时间字符串
        """
        now = time.localtime()
        time_str = time.strftime("%Y年%m月%d日 %H点%M分%S秒", now)
        text = f"现在时间 {time_str}"
        self.say(text)
        return time_str

    def warn_incomplete(self):
        """播报：角点未拍全"""
        self.say("角点未拍全，请调整棋盘格位置")

    def announce_success(self):
        """播报：检测成功"""
        self.say("棋盘格检测成功")

    def announce_start(self):
        """播报：任务开始"""
        self.say("开始截图任务")

    def warn_duplicate(self):
        """播报：重复姿态"""
        self.say("图片与已拍照片重复，已自动舍弃")

    def warn_area_ratio(self):
        """播报：面积占比不足"""
        self.say("棋盘格在画面中面积占比不足，请靠近棋盘格")
