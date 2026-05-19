import cv2
import numpy as np

class ChessboardDetector:
    def __init__(self, pattern_size=(9, 6)):
        self.pattern_size = pattern_size

    def set_pattern_size(self, width, height):
        self.pattern_size = (width, height)

    def detect(self, image):
        # 棋盘格角点检测（亚像素精度）
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
        ret, corners = cv2.findChessboardCorners(gray, self.pattern_size, None, flags)
        if ret:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            return True, corners
        return False, None

    def draw_corners(self, image, corners):
        result = image.copy()
        cv2.drawChessboardCorners(result, self.pattern_size, corners, True)
        return result

    @staticmethod
    def get_expected_corners(pattern_size):
        return pattern_size[0] * pattern_size[1]

    def compute_chessboard_area_ratio(self, image, corners):
        # 棋盘格凸包面积 / 图像总面积
        h, w = image.shape[:2]
        image_area = h * w
        if len(corners) < 3:
            return 0.0
        hull = cv2.convexHull(corners)
        chessboard_area = cv2.contourArea(hull)
        return chessboard_area / image_area

    def check_sharpness(self, image, threshold=80.0):
        # 拉普拉斯方差：值越小图像越模糊
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        variance = lap.var()
        return variance >= threshold, variance

    def check_exposure(self, image, dark_thresh=20, bright_thresh=235, max_ratio=0.15):
        # 统计过暗/过亮像素占比
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        total = gray.size
        dark_ratio = np.sum(gray < dark_thresh) / total
        bright_ratio = np.sum(gray > bright_thresh) / total
        ok = dark_ratio <= max_ratio and bright_ratio <= max_ratio
        return ok, dark_ratio, bright_ratio

    def check_boundary(self, corners, image_shape, margin=10):
        # 棋盘格角点距图像边缘不得小于 margin 像素
        h, w = image_shape[:2]
        pts = corners.reshape(-1, 2)
        x, y = pts[:, 0], pts[:, 1]
        if np.any(x < margin) or np.any(x > w - margin) or np.any(y < margin) or np.any(y > h - margin):
            return False
        return True

    @staticmethod
    def filter_by_reprojection_error(photo_items, pattern_size, max_error=1.0):
        # 用所有图片标定相机，计算每张图的重投影误差，剔除不合格图片
        if len(photo_items) < 2:
            return photo_items, [], {}
        # 棋盘格三维坐标（z=0）
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
        # 标定相机
        image_size = (image_shape[1], image_shape[0])
        ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(
            obj_points, img_points, image_size, None, None
        )
        # 计算每张图的 RMS 重投影误差
        errors = {}
        good_items = []
        bad_items = []
        for i, item in enumerate(valid_items):
            filepath = item[0]
            proj_points, _ = cv2.projectPoints(obj_points[i], rvecs[i], tvecs[i], mtx, dist)
            error = float(np.sqrt(np.mean((img_points[i] - proj_points) ** 2)))
            errors[filepath] = error
            if error <= max_error:
                good_items.append(item)
            else:
                bad_items.append(item)
        return good_items, bad_items, errors
