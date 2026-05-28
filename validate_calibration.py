"""
相机标定图像验证工具

独立的验证脚本，用于批量检查标定图像质量并生成可视化报告。

功能：
- 批量检查目录中的所有图像
- 多维度质量评估（清晰度、曝光、贴边、面积比、形变、置信度）
- 重投影误差计算
- 生成 HTML 可视化报告
- 提供修复建议

使用示例：
    python validate_calibration.py --input screenshots/A001/
    python validate_calibration.py --input screenshots/A001/ --report report.html
    python validate_calibration.py --input screenshots/A001/ --pattern-size 9x6
"""

import os
import sys
import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# 导入项目模块
from chessboard_detector import ChessboardDetector


class CalibrationValidator:
    """
    相机标定图像验证器
    
    提供多维度的图像质量评估和验证功能。
    """
    
    def __init__(self, pattern_size=(9, 6)):
        """
        初始化验证器
        
        Args:
            pattern_size (tuple): 棋盘格内角点尺寸 (width, height)
        """
        self.pattern_size = pattern_size
        self.detector = ChessboardDetector(pattern_size)
        
    def validate_directory(self, input_dir):
        """
        验证目录中的所有图像
        
        Args:
            input_dir (str): 输入目录路径
            
        Returns:
            dict: 验证结果
        """
        input_path = Path(input_dir)
        if not input_path.exists():
            raise FileNotFoundError(f"目录不存在: {input_dir}")
        
        # 查找所有图像文件
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
        image_files = []
        for ext in image_extensions:
            image_files.extend(input_path.glob(f"*{ext}"))
            image_files.extend(input_path.glob(f"*{ext.upper()}"))
        
        if not image_files:
            raise ValueError(f"目录中没有找到图像文件: {input_dir}")
        
        print(f"找到 {len(image_files)} 个图像文件")
        
        # 验证每张图像
        results = []
        for i, img_path in enumerate(sorted(image_files)):
            print(f"正在验证 [{i+1}/{len(image_files)}]: {img_path.name}")
            result = self.validate_image(str(img_path))
            results.append(result)
        
        # 汇总结果
        summary = self._generate_summary(results)
        
        return {
            'input_dir': str(input_path.absolute()),
            'pattern_size': self.pattern_size,
            'total_images': len(image_files),
            'results': results,
            'summary': summary
        }
    
    def validate_image(self, image_path):
        """
        验证单张图像
        
        Args:
            image_path (str): 图像文件路径
            
        Returns:
            dict: 验证结果
        """
        # 读取图像
        image = cv2.imread(image_path)
        if image is None:
            return {
                'file': os.path.basename(image_path),
                'path': image_path,
                'status': 'error',
                'message': '无法读取图像文件',
                'checks': {},
                'score': 0
            }
        
        # 检测棋盘格
        found, corners = self.detector.detect(image)
        
        if not found:
            return {
                'file': os.path.basename(image_path),
                'path': image_path,
                'status': 'failed',
                'message': '未检测到完整棋盘格',
                'checks': {'chessboard': False},
                'score': 0
            }
        
        # 执行所有检查
        checks = {}
        
        # 1. 清晰度检查
        sharp_ok, sharp_val = self.detector.check_sharpness(image)
        checks['sharpness'] = {
            'passed': sharp_ok,
            'value': float(sharp_val),
            'threshold': 80.0,
            'message': f"清晰度: {sharp_val:.1f} ({'通过' if sharp_ok else '不通过'})"
        }
        
        # 2. 曝光检查
        exp_ok, dark_r, bright_r = self.detector.check_exposure(image)
        checks['exposure'] = {
            'passed': exp_ok,
            'dark_ratio': float(dark_r),
            'bright_ratio': float(bright_r),
            'threshold': 0.15,
            'message': f"曝光: 欠曝{dark_r:.1%} 过曝{bright_r:.1%} ({'通过' if exp_ok else '不通过'})"
        }
        
        # 3. 贴边检查
        bound_ok = self.detector.check_boundary(corners, image.shape)
        checks['boundary'] = {
            'passed': bound_ok,
            'message': f"贴边: {'通过' if bound_ok else '不通过'}"
        }
        
        # 4. 面积比检查
        area_ratio = self.detector.compute_chessboard_area_ratio(image, corners)
        area_ok = area_ratio >= 0.10
        checks['area_ratio'] = {
            'passed': area_ok,
            'value': float(area_ratio),
            'threshold': 0.10,
            'message': f"面积比: {area_ratio:.1%} ({'通过' if area_ok else '不通过'})"
        }
        
        # 5. 形变检查
        dist_ok, condition_number, level = self.detector.check_distortion(corners, image.shape)
        checks['distortion'] = {
            'passed': dist_ok,
            'condition_number': float(condition_number),
            'level': level,
            'message': f"形变: {level} (条件数={condition_number:.2f}) ({'通过' if dist_ok else '不通过'})"
        }
        
        # 6. 角点置信度检查
        confidence_score, mean_offset, low_conf_count = self.detector.check_corner_confidence(image, corners)
        conf_ok = confidence_score >= 0.7
        checks['corner_confidence'] = {
            'passed': conf_ok,
            'score': float(confidence_score),
            'mean_offset': float(mean_offset),
            'low_confidence_count': int(low_conf_count),
            'message': f"置信度: {confidence_score:.2f} (偏移={mean_offset:.2f}px) ({'通过' if conf_ok else '不通过'})"
        }
        
        # 7. 综合质量评分
        quality_score, score_details = self.detector.compute_quality_score(image, corners)
        
        # 判断整体状态
        all_passed = all(check['passed'] for check in checks.values())
        status = 'passed' if all_passed else 'warning' if quality_score >= 60 else 'failed'
        
        return {
            'file': os.path.basename(image_path),
            'path': image_path,
            'status': status,
            'message': '所有检查通过' if all_passed else '部分检查未通过',
            'checks': checks,
            'quality_score': float(quality_score),
            'score_details': {k: float(v) for k, v in score_details.items()},
            'corners_count': len(corners)
        }
    
    def _generate_summary(self, results):
        """
        生成验证结果汇总
        
        Args:
            results (list): 验证结果列表
            
        Returns:
            dict: 汇总信息
        """
        total = len(results)
        passed = sum(1 for r in results if r['status'] == 'passed')
        warning = sum(1 for r in results if r['status'] == 'warning')
        failed = sum(1 for r in results if r['status'] == 'failed')
        error = sum(1 for r in results if r['status'] == 'error')
        
        # 计算平均质量分数
        scores = [r['quality_score'] for r in results if r['status'] != 'error']
        avg_score = np.mean(scores) if scores else 0
        
        # 找出问题图像
        problem_images = []
        for r in results:
            if r['status'] in ('failed', 'error'):
                problem_images.append({
                    'file': r['file'],
                    'status': r['status'],
                    'message': r['message']
                })
            elif r['status'] == 'warning':
                # 找出具体哪些检查未通过
                failed_checks = [k for k, v in r['checks'].items() if not v['passed']]
                if failed_checks:
                    problem_images.append({
                        'file': r['file'],
                        'status': 'warning',
                        'message': f"未通过: {', '.join(failed_checks)}"
                    })
        
        return {
            'total': total,
            'passed': passed,
            'warning': warning,
            'failed': failed,
            'error': error,
            'pass_rate': passed / total if total > 0 else 0,
            'average_score': float(avg_score),
            'problem_images': problem_images
        }
    
    def generate_html_report(self, validation_result, output_path):
        """
        生成 HTML 可视化报告
        
        Args:
            validation_result (dict): 验证结果
            output_path (str): 输出 HTML 文件路径
        """
        summary = validation_result['summary']
        results = validation_result['results']
        
        html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>相机标定图像验证报告</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin-bottom: 10px;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .summary-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .summary-card h3 {{
            color: #666;
            margin-bottom: 10px;
            font-size: 14px;
        }}
        .summary-card .value {{
            font-size: 32px;
            font-weight: bold;
        }}
        .summary-card .value.green {{ color: #4caf50; }}
        .summary-card .value.orange {{ color: #ff9800; }}
        .summary-card .value.red {{ color: #f44336; }}
        .summary-card .value.blue {{ color: #2196f3; }}
        .section {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .section h2 {{
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }}
        .image-list {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 15px;
        }}
        .image-item {{
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 15px;
            transition: transform 0.2s;
        }}
        .image-item:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .image-item.passed {{
            border-left: 4px solid #4caf50;
        }}
        .image-item.warning {{
            border-left: 4px solid #ff9800;
        }}
        .image-item.failed {{
            border-left: 4px solid #f44336;
        }}
        .image-item.error {{
            border-left: 4px solid #9e9e9e;
        }}
        .image-item h4 {{
            margin-bottom: 10px;
            color: #333;
        }}
        .image-item .status {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            margin-bottom: 10px;
        }}
        .status.passed {{ background: #e8f5e9; color: #2e7d32; }}
        .status.warning {{ background: #fff3e0; color: #ef6c00; }}
        .status.failed {{ background: #ffebee; color: #c62828; }}
        .status.error {{ background: #f5f5f5; color: #616161; }}
        .checks {{
            font-size: 12px;
            color: #666;
        }}
        .checks div {{
            margin: 3px 0;
        }}
        .check-pass {{ color: #4caf50; }}
        .check-fail {{ color: #f44336; }}
        .score-bar {{
            height: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin-top: 10px;
            overflow: hidden;
        }}
        .score-bar .fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}
        .score-bar .fill.green {{ background: #4caf50; }}
        .score-bar .fill.orange {{ background: #ff9800; }}
        .score-bar .fill.red {{ background: #f44336; }}
        .recommendations {{
            background: #fff3e0;
            border-left: 4px solid #ff9800;
            padding: 15px;
            border-radius: 0 8px 8px 0;
            margin-top: 15px;
        }}
        .recommendations h3 {{
            color: #ef6c00;
            margin-bottom: 10px;
        }}
        .recommendations ul {{
            margin-left: 20px;
        }}
        .recommendations li {{
            margin: 5px 0;
        }}
        .footer {{
            text-align: center;
            color: #999;
            padding: 20px;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>相机标定图像验证报告</h1>
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>输入目录: {validation_result['input_dir']}</p>
            <p>棋盘格尺寸: {validation_result['pattern_size'][0]}x{validation_result['pattern_size'][1]}</p>
        </div>
        
        <div class="summary-grid">
            <div class="summary-card">
                <h3>总图像数</h3>
                <div class="value blue">{summary['total']}</div>
            </div>
            <div class="summary-card">
                <h3>通过</h3>
                <div class="value green">{summary['passed']}</div>
            </div>
            <div class="summary-card">
                <h3>警告</h3>
                <div class="value orange">{summary['warning']}</div>
            </div>
            <div class="summary-card">
                <h3>失败</h3>
                <div class="value red">{summary['failed']}</div>
            </div>
            <div class="summary-card">
                <h3>通过率</h3>
                <div class="value green">{summary['pass_rate']:.1%}</div>
            </div>
            <div class="summary-card">
                <h3>平均质量分</h3>
                <div class="value {'green' if summary['average_score'] >= 80 else 'orange' if summary['average_score'] >= 60 else 'red'}">{summary['average_score']:.1f}</div>
            </div>
        </div>
        
        <div class="section">
            <h2>详细结果</h2>
            <div class="image-list">
"""
        
        # 添加每张图像的详细信息
        for result in results:
            status_class = result['status']
            status_text = {'passed': '通过', 'warning': '警告', 'failed': '失败', 'error': '错误'}[result['status']]
            
            checks_html = ""
            if result['status'] != 'error' and 'checks' in result:
                for check_name, check_data in result['checks'].items():
                    check_class = 'check-pass' if check_data.get('passed', False) else 'check-fail'
                    checks_html += f'<div class="{check_class}">{check_data.get("message", "")}</div>'
            
            # 质量分数进度条
            score = result.get('quality_score', 0)
            score_class = 'green' if score >= 80 else 'orange' if score >= 60 else 'red'
            
            html_content += f"""
                <div class="image-item {status_class}">
                    <h4>{result['file']}</h4>
                    <span class="status {status_class}">{status_text}</span>
                    <div class="checks">
                        {checks_html}
                    </div>
                    <div class="score-bar">
                        <div class="fill {score_class}" style="width: {score}%"></div>
                    </div>
                    <div style="text-align: right; font-size: 12px; color: #666; margin-top: 5px;">
                        质量分: {score:.1f}/100
                    </div>
                </div>
"""
        
        html_content += """
            </div>
        </div>
"""
        
        # 添加问题图像建议
        if summary['problem_images']:
            html_content += """
        <div class="section">
            <h2>问题图像与修复建议</h2>
            <div class="recommendations">
                <h3>建议处理以下图像：</h3>
                <ul>
"""
            for img in summary['problem_images']:
                html_content += f'                    <li><strong>{img["file"]}</strong>: {img["message"]}</li>\n'
            
            html_content += """
                </ul>
            </div>
        </div>
"""
        
        # 添加使用建议
        html_content += """
        <div class="section">
            <h2>标定建议</h2>
            <div class="recommendations">
                <h3>基于验证结果的建议：</h3>
                <ul>
                    <li><strong>通过率 ≥ 80%</strong>: 标定数据质量良好，可以进行标定</li>
                    <li><strong>通过率 60%~80%</strong>: 建议删除失败图像后重新标定</li>
                    <li><strong>通过率 < 60%</strong>: 建议重新采集图像</li>
                    <li><strong>平均质量分 ≥ 80</strong>: 图像质量优秀</li>
                    <li><strong>平均质量分 60~80</strong>: 图像质量一般，可考虑优化采集条件</li>
                    <li><strong>平均质量分 < 60</strong>: 图像质量较差，建议重新采集</li>
                </ul>
            </div>
        </div>
"""
        
        html_content += f"""
        <div class="footer">
            <p>相机标定图像验证工具 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>
"""
        
        # 写入 HTML 文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"HTML 报告已生成: {output_path}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='相机标定图像验证工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python validate_calibration.py --input screenshots/A001/
  python validate_calibration.py --input screenshots/A001/ --report report.html
  python validate_calibration.py --input screenshots/A001/ --pattern-size 9x6
        """
    )
    
    parser.add_argument('--input', '-i', required=True,
                       help='输入图像目录路径')
    parser.add_argument('--output', '-o', default=None,
                       help='JSON 输出文件路径（可选）')
    parser.add_argument('--report', '-r', default='validation_report.html',
                       help='HTML 报告输出路径（默认: validation_report.html）')
    parser.add_argument('--pattern-size', '-p', default='9x6',
                       help='棋盘格尺寸，格式: widthxheight（默认: 9x6）')
    
    args = parser.parse_args()
    
    # 解析棋盘格尺寸
    try:
        w, h = args.pattern_size.split('x')
        pattern_size = (int(w), int(h))
    except ValueError:
        print(f"错误: 无效的棋盘格尺寸格式 '{args.pattern_size}'，应为 'widthxheight'")
        sys.exit(1)
    
    # 创建验证器
    validator = CalibrationValidator(pattern_size)
    
    try:
        # 执行验证
        print(f"开始验证目录: {args.input}")
        print(f"棋盘格尺寸: {pattern_size[0]}x{pattern_size[1]}")
        print("-" * 50)
        
        result = validator.validate_directory(args.input)
        
        # 输出 JSON 结果（如果指定了输出路径）
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"JSON 结果已保存: {args.output}")
        
        # 生成 HTML 报告
        validator.generate_html_report(result, args.report)
        
        # 输出摘要
        print("-" * 50)
        summary = result['summary']
        print(f"验证完成!")
        print(f"总图像数: {summary['total']}")
        print(f"通过: {summary['passed']}")
        print(f"警告: {summary['warning']}")
        print(f"失败: {summary['failed']}")
        print(f"通过率: {summary['pass_rate']:.1%}")
        print(f"平均质量分: {summary['average_score']:.1f}")
        
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
