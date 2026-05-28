"""
棋盘格检测与质量评价模块

本模块提供棋盘格标定板的角点检测和图像质量评价功能。

主要功能：
- 棋盘格角点检测（亚像素精度）
- 图像清晰度检测（拉普拉斯方差）
- 曝光检测（过暗/过亮像素占比）
- 贴边检测（角点距图像边缘距离）
- 面积比检测（棋盘格占图像面积比例）
- 形变检测（单应性矩阵条件数）
- 角点置信度（亚像素收敛质量）
- 重投影误差筛选（相机标定后计算每张图的误差）

使用示例：
    detector = ChessboardDetector(pattern_size=(9, 6))
    found, corners = detector.detect(image)
    if found:
        sharp_ok, sharp_val = detector.check_sharpness(image)
        if sharp_ok:
            # 继续其他检测...
            pass
"""

import cv2
import numpy as np


class ChessboardDetector:
    """
    棋盘格检测器
    
    提供棋盘格角点检测和图像质量评价功能。
    支持多种质量检测方法，用于筛选高质量的标定图像。
    
    Attributes:
        pattern_size (tuple): 棋盘格内角点尺寸 (width, height)
        
    质量检测方法：
    1. 清晰度检测：拉普拉斯方差 >= 80
    2. 曝光检测：过暗/过亮像素 <= 15%
    3. 贴边检测：角点距边缘 >= 10px
    4. 面积比检测：棋盘格面积 >= 图像面积 10%
    5. 重投影误差：标定后误差 <= 1.0px
    """
    
    def __init__(self, pattern_size=(9, 6)):
        """
        初始化检测器
        
        Args:
            pattern_size (tuple): 棋盘格内角点尺寸 (width, height)
                例如 (9, 6) 表示 9 列 6 行的内角点
        """
        self.pattern_size = pattern_size

    def set_pattern_size(self, width, height):
        """
        设置棋盘格内角点尺寸
        
        Args:
            width (int): 内角点列数
            height (int): 内角点行数
        """
        self.pattern_size = (width, height)

    def detect(self, image):
        """
        棋盘格角点检测（亚像素精度）
        
        使用 OpenCV 的 findChessboardCorners 检测角点，
        然后通过 cornerSubPix 进行亚像素细化。
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            
        Returns:
            tuple: (found, corners)
                - found (bool): 是否检测到完整棋盘格
                - corners (numpy.ndarray): 角点坐标，形状为 (N, 1, 2)
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 检测参数：自适应阈值 + 图像归一化
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
        ret, corners = cv2.findChessboardCorners(gray, self.pattern_size, None, flags)
        
        if ret:
            # 亚像素细化：迭代终止条件
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            return True, corners
        return False, None

    def draw_corners(self, image, corners):
        """
        在图像上绘制检测到的角点
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            corners (numpy.ndarray): 角点坐标
            
        Returns:
            numpy.ndarray: 绘制了角点的图像副本
        """
        result = image.copy()
        cv2.drawChessboardCorners(result, self.pattern_size, corners, True)
        return result

    @staticmethod
    def get_expected_corners(pattern_size):
        """
        获取期望的角点总数
        
        Args:
            pattern_size (tuple): (width, height)
            
        Returns:
            int: 期望的角点数量 (width * height)
        """
        return pattern_size[0] * pattern_size[1]

    def compute_chessboard_area_ratio(self, image, corners):
        """
        计算棋盘格面积占图像面积的比例
        
        通过凸包计算棋盘格区域面积，然后除以图像总面积。
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            corners (numpy.ndarray): 棋盘格角点坐标
            
        Returns:
            float: 面积比例 (0.0 ~ 1.0)
        """
        h, w = image.shape[:2]
        image_area = h * w
        if len(corners) < 3:
            return 0.0
        hull = cv2.convexHull(corners)
        chessboard_area = cv2.contourArea(hull)
        return chessboard_area / image_area

    def check_sharpness(self, image, threshold=80.0):
        """
        检测图像清晰度
        
        使用拉普拉斯算子计算图像方差，值越小表示越模糊。
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            threshold (float): 清晰度阈值，默认 80.0
            
        Returns:
            tuple: (ok, variance)
                - ok (bool): 是否清晰
                - variance (float): 拉普拉斯方差值
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        variance = lap.var()
        return variance >= threshold, variance

    def check_exposure(self, image, dark_thresh=20, bright_thresh=235, max_ratio=0.15):
        """
        检测图像曝光
        
        统计过暗（<dark_thresh）和过亮（>bright_thresh）像素的占比。
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            dark_thresh (int): 过暗阈值，默认 20
            bright_thresh (int): 过亮阈值，默认 235
            max_ratio (float): 最大允许比例，默认 0.15 (15%)
            
        Returns:
            tuple: (ok, dark_ratio, bright_ratio)
                - ok (bool): 曝光是否正常
                - dark_ratio (float): 过暗像素比例
                - bright_ratio (float): 过亮像素比例
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        total = gray.size
        dark_ratio = np.sum(gray < dark_thresh) / total
        bright_ratio = np.sum(gray > bright_thresh) / total
        ok = dark_ratio <= max_ratio and bright_ratio <= max_ratio
        return ok, dark_ratio, bright_ratio

    def check_boundary(self, corners, image_shape, margin=10):
        """
        检测棋盘格是否越界
        
        检查所有角点是否距图像边缘至少 margin 像素。
        
        Args:
            corners (numpy.ndarray): 棋盘格角点坐标
            image_shape (tuple): 图像形状 (height, width, channels)
            margin (int): 边缘距离阈值，默认 10 像素
            
        Returns:
            bool: True 表示不越界，False 表示贴边或越界
        """
        h, w = image_shape[:2]
        pts = corners.reshape(-1, 2)
        x, y = pts[:, 0], pts[:, 1]
        if np.any(x < margin) or np.any(x > w - margin) or np.any(y < margin) or np.any(y > h - margin):
            return False
        return True

    @staticmethod
    def filter_by_reprojection_error(photo_items, pattern_size, max_error=1.0):
        """
        基于重投影误差筛选图像
        
        使用所有图像进行相机标定，然后计算每张图像的重投影误差。
        误差超过阈值的图像被视为不合格。
        
        重投影误差计算流程：
        1. 构建棋盘格三维坐标 (X, Y, Z=0)
        2. 使用 cv2.calibrateCamera 标定相机
        3. 使用 cv2.projectPoints 投影三维点到图像平面
        4. 计算检测角点与投影点的均方根误差 (RMS)
        
        Args:
            photo_items (list): 照片数据列表
                每项格式：(filepath, hist, gray_small, corners, image_shape)
            pattern_size (tuple): 棋盘格尺寸 (width, height)
            max_error (float): 最大允许重投影误差，默认 1.0 像素
            
        Returns:
            tuple: (good_items, bad_items, errors)
                - good_items (list): 合格照片列表
                - bad_items (list): 不合格照片列表
                - errors (dict): 误差映射 {filepath: error_value}
        """
        if len(photo_items) < 2:
            return photo_items, [], {}
        
        # 构建棋盘格三维坐标（Z=0 平面）
        w, h = pattern_size
        objp = np.zeros((w * h, 3), np.float32)
        objp[:, :2] = np.mgrid[0:w, 0:h].T.reshape(-1, 2)
        
        obj_points = []
        img_points = []
        valid_items = []
        
        for item in photo_items:
            filepath, hist, gray_small, corners, image_shape = item
            obj_points.append(objp)
            img_points.append(corners.astype(np.float32))
            valid_items.append(item)
        
        if len(valid_items) < 2:
            return valid_items, [], {}
        
        # 相机标定
        image_size = (image_shape[1], image_shape[0])
        ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(
            obj_points, img_points, image_size, None, None
        )
        
        # 计算每张图的重投影误差
        errors = {}
        good_items = []
        bad_items = []
        
        for i, item in enumerate(valid_items):
            filepath = item[0]
            # 投影三维点到图像平面
            proj_points, _ = cv2.projectPoints(obj_points[i], rvecs[i], tvecs[i], mtx, dist)
            # 计算 RMS 误差
            error = float(np.sqrt(np.mean((img_points[i] - proj_points) ** 2)))
            errors[filepath] = error
            
            if error <= max_error:
                good_items.append(item)
            else:
                bad_items.append(item)
        
        return good_items, bad_items, errors

    def check_distortion(self, corners, image_shape):
        """
        检测棋盘格形变程度
        
        通过计算理想棋盘格点与实际检测角点之间的单应性矩阵，
        评估棋盘格的透视形变程度。条件数越大，形变越严重。
        
        形变等级：
        - 条件数 < 1.5: 优秀（正对相机）
        - 条件数 1.5 ~ 3.0: 良好（轻微倾斜）
        - 条件数 3.0 ~ 5.0: 一般（明显倾斜）
        - 条件数 > 5.0: 差（极端角度，不建议用于标定）
        
        Args:
            corners (numpy.ndarray): 棋盘格角点坐标，形状 (N, 1, 2)
            image_shape (tuple): 图像形状 (height, width, channels)
            
        Returns:
            tuple: (ok, condition_number, level)
                - ok (bool): 形变是否在可接受范围内 (condition_number < 5.0)
                - condition_number (float): 单应性矩阵条件数
                - level (str): 形变等级 ("优秀"/"良好"/"一般"/"差")
        """
        # 生成理想棋盘格点（单位平面）
        w, h = self.pattern_size
        ideal_pts = np.zeros((w * h, 2), np.float32)
        ideal_pts[:, 0] = np.mgrid[0:w, 0:h].T.reshape(-1, 2)[:, 0]
        ideal_pts[:, 1] = np.mgrid[0:w, 0:h].T.reshape(-1, 2)[:, 1]
        
        # 实际检测角点
        actual_pts = corners.reshape(-1, 2).astype(np.float32)
        
        # 计算单应性矩阵
        H, _ = cv2.findHomography(ideal_pts, actual_pts)
        if H is None:
            return False, float('inf'), "差"
        
        # 计算条件数（奇异值分解）
        # 条件数 = max(SVD) / min(SVD)
        # 条件数越大，说明形变越严重
        s = np.linalg.svd(H, compute_uv=False)
        condition_number = s[0] / s[-1] if s[-1] > 0 else float('inf')
        
        # 判断等级
        if condition_number < 1.5:
            level = "优秀"
            ok = True
        elif condition_number < 3.0:
            level = "良好"
            ok = True
        elif condition_number < 5.0:
            level = "一般"
            ok = True
        else:
            level = "差"
            ok = False
        
        return ok, condition_number, level

    def check_corner_confidence(self, image, corners, window_size=11):
        """
        检测角点置信度（亚像素收敛质量）
        
        通过 cornerSubPix 的迭代收敛情况评估角点检测质量。
        如果角点在亚像素细化后偏移量很小，说明检测质量高。
        
        置信度等级：
        - 偏移 < 0.5px: 高置信度
        - 偏移 0.5 ~ 1.0px: 中置信度
        - 偏移 > 1.0px: 低置信度
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            corners (numpy.ndarray): 棋盘格角点坐标
            window_size (int): 亚像素细化窗口大小
            
        Returns:
            tuple: (confidence_score, mean_offset, low_confidence_count)
                - confidence_score (float): 综合置信度分数 (0.0 ~ 1.0)
                - mean_offset (float): 平均偏移量（像素）
                - low_confidence_count (int): 低置信度角点数量
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 保存原始角点
        original_corners = corners.copy()
        
        # 重新进行亚像素细化
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        refined_corners = cv2.cornerSubPix(gray, corners, (window_size, window_size), (-1, -1), criteria)
        
        # 计算每个角点的偏移量
        offsets = np.linalg.norm(refined_corners - original_corners, axis=2)
        mean_offset = float(np.mean(offsets))
        
        # 统计低置信度角点（偏移 > 1.0px）
        low_confidence_count = int(np.sum(offsets > 1.0))
        
        # 计算综合置信度分数
        # 基于平均偏移量，偏移越小分数越高
        if mean_offset < 0.5:
            confidence_score = 1.0
        elif mean_offset < 1.0:
            confidence_score = 1.0 - (mean_offset - 0.5) / 0.5 * 0.3
        elif mean_offset < 2.0:
            confidence_score = 0.7 - (mean_offset - 1.0) / 1.0 * 0.4
        else:
            confidence_score = max(0.3 - (mean_offset - 2.0) * 0.1, 0.0)
        
        return confidence_score, mean_offset, low_confidence_count

    def check_focal_consistency(self, image_list, pattern_size=None):
        """
        检查多帧之间的焦距一致性
        
        如果相机有自动对焦，不同帧之间焦距可能变化，这会导致标定失败。
        本方法通过比较不同帧的清晰度分布来检测焦距变化。
        
        Args:
            image_list (list): 图像列表，每个元素为 BGR 格式的 numpy 数组
            pattern_size (tuple, optional): 棋盘格尺寸，用于角点检测
            
        Returns:
            tuple: (consistent, focal_scores, outlier_indices)
                - consistent (bool): 焦距是否一致
                - focal_scores (list): 每帧的焦距分数（基于清晰度）
                - outlier_indices (list): 异常帧的索引
        """
        if pattern_size is None:
            pattern_size = self.pattern_size
        
        focal_scores = []
        
        for img in image_list:
            # 计算清晰度作为焦距的代理指标
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            lap = cv2.Laplacian(gray, cv2.CV_64F)
            variance = lap.var()
            focal_scores.append(variance)
        
        if len(focal_scores) < 2:
            return True, focal_scores, []
        
        # 计算均值和标准差
        mean_score = np.mean(focal_scores)
        std_score = np.std(focal_scores)
        
        # 变异系数（Coefficient of Variation）
        cv_score = std_score / mean_score if mean_score > 0 else 0
        
        # 判断一致性：变异系数 < 0.15 认为一致
        consistent = cv_score < 0.15
        
        # 找出异常帧（偏离均值 2σ 以上）
        outlier_indices = []
        for i, score in enumerate(focal_scores):
            if abs(score - mean_score) > 2 * std_score:
                outlier_indices.append(i)
        
        return consistent, focal_scores, outlier_indices

    def compute_quality_score(self, image, corners):
        """
        计算图像的综合质量分数
        
        综合考虑清晰度、曝光、贴边、面积比、形变、角点置信度等多个因素，
        输出一个 0-100 的综合分数。
        
        评分权重：
        - 清晰度: 20%
        - 曝光: 15%
        - 贴边: 15%
        - 面积比: 20%
        - 形变: 20%
        - 角点置信度: 10%
        
        Args:
            image (numpy.ndarray): BGR 格式的输入图像
            corners (numpy.ndarray): 棋盘格角点坐标
            
        Returns:
            tuple: (total_score, details)
                - total_score (float): 综合质量分数 (0 ~ 100)
                - details (dict): 各项分数详情
        """
        details = {}
        
        # 1. 清晰度分数 (0-100)
        sharp_ok, sharp_val = self.check_sharpness(image)
        # 基于阈值 80，分数 = min(100, sharp_val / 80 * 100)
        sharp_score = min(100.0, sharp_val / 80.0 * 100.0) if sharp_val > 0 else 0.0
        details['sharpness'] = sharp_score
        
        # 2. 曝光分数 (0-100)
        exp_ok, dark_r, bright_r = self.check_exposure(image)
        # 基于过暗/过亮像素占比，占比越低分数越高
        exp_score = max(0, 100.0 - (dark_r + bright_r) * 200.0)
        details['exposure'] = exp_score
        
        # 3. 贴边分数 (0-100)
        bound_ok = self.check_boundary(corners, image.shape)
        # 如果不越界，根据最小边距计算分数
        h, w = image.shape[:2]
        pts = corners.reshape(-1, 2)
        x, y = pts[:, 0], pts[:, 1]
        min_margin = min(np.min(x), np.min(y), w - np.max(x), h - np.max(y))
        # 10px 为及格线，20px 以上满分
        boundary_score = min(100.0, max(0, (min_margin - 5) / 15 * 100.0))
        details['boundary'] = boundary_score
        
        # 4. 面积比分数 (0-100)
        area_ratio = self.compute_chessboard_area_ratio(image, corners)
        # 10% 为及格线，20% 以上满分
        area_score = min(100.0, max(0, (area_ratio - 0.05) / 0.15 * 100.0))
        details['area_ratio'] = area_score
        
        # 5. 形变分数 (0-100)
        dist_ok, condition_number, level = self.check_distortion(corners, image.shape)
        # 条件数 1.0 为理想，5.0 为及格
        distortion_score = max(0, 100.0 - (condition_number - 1.0) / 4.0 * 100.0)
        details['distortion'] = distortion_score
        
        # 6. 角点置信度分数 (0-100)
        confidence_score, _, _ = self.check_corner_confidence(image, corners)
        details['corner_confidence'] = confidence_score * 100.0
        
        # 加权平均
        weights = {
            'sharpness': 0.20,
            'exposure': 0.15,
            'boundary': 0.15,
            'area_ratio': 0.20,
            'distortion': 0.20,
            'corner_confidence': 0.10
        }
        
        total_score = 0.0
        for key, weight in weights.items():
            total_score += details[key] * weight
        
        return total_score, details
