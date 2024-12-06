import cv2
import numpy as np
import os
from PIL import Image, ImageDraw, ImageFont
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

def add_watermark_to_video(input_video_path, output_video_path, watermark_text):
    """
    给视频添加文字水印
    
    参数:
    input_video_path: 输入视频路径
    output_video_path: 输出视频路径
    watermark_text: 水印文字内容
    """
    # 读取视频
    video = cv2.VideoCapture(input_video_path)
    
    # 获取视频属性
    fps = int(video.get(cv2.CAP_PROP_FPS))
    frame_width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # 设置输出视频编码器
    if os.name == 'nt':  # Windows系统
        fourcc = cv2.VideoWriter_fourcc(*'H264')
    else:  # Linux/Mac系统
        fourcc = cv2.VideoWriter_fourcc(*'avc1')
    
    # 创建临时输出文件
    temp_output_path = output_video_path.rsplit('.', 1)[0] + '_temp.mp4'
    
    out = cv2.VideoWriter(temp_output_path, fourcc, fps, (frame_width, frame_height))
    
    if not out.isOpened():
        print("警告：编码器初始化失败，尝试备用编码器")
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        temp_output_path = output_video_path.rsplit('.', 1)[0] + '_temp.avi'
        out = cv2.VideoWriter(temp_output_path, fourcc, fps, (frame_width, frame_height))
        
        if not out.isOpened():
            raise Exception("无法初始化视频编码器")

    # 计算合适的字体大小（基于视频高度）
    base_font_size = int(frame_height / 16)  # 使用视频高度的1/3作为基准
    font_size = max(base_font_size, 50)  # 确保最小字体大小为100
    print(f"视频分辨率: {frame_width}x{frame_height}, 字体大小: {font_size}")

    # 创建水印图层（使用更大的画布）
    canvas_width = frame_width * 2
    canvas_height = frame_height * 2
    watermark = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark)

    # 加载字体
    try:
        font_paths = [
            "msyh.ttc",  # 微软雅黑
            "simhei.ttf",  # 黑体
            "FZLTXHK.TTF",  # 方正兰亭黑
            "SourceHanSansSC-Bold.otf",  # 思源黑体
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Linux Noto
            "/System/Library/Fonts/PingFang.ttc"  # macOS 苹方
        ]
        
        font = None
        for font_path in font_paths:
            try:
                font = ImageFont.truetype(font_path, font_size)
                print(f"成功加载字体: {font_path}")
                break
            except:
                continue
        
        if font is None:
            raise Exception("未找到合适的字体")
    except Exception as e:
        print(f"警告：字体加载失败 ({str(e)})，使用默认字体")
        font = ImageFont.load_default()

    # 获取文字尺寸
    bbox = draw.textbbox((0, 0), watermark_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # 计算文字位置（右上角）
    margin = 20  # 边距
    x = canvas_width - text_width - margin
    y = margin

    # 绘制水印
    # stroke_width = 15  # 粗描边
    # # 绘制描边
    # draw.text((x, y), watermark_text, font=font, fill=(0, 0, 0, 255), stroke_width=stroke_width)
    # 绘制主体文字
    draw.text((x, y), watermark_text, font=font, fill=(255, 245, 137, 255))

    # 将水印缩放回视频尺寸
    watermark = watermark.resize((frame_width, frame_height), Image.Resampling.LANCZOS)
    watermark_cv = cv2.cvtColor(np.array(watermark), cv2.COLOR_RGBA2BGRA)

    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    processed_frames = 0

    while True:
        ret, frame = video.read()
        if not ret:
            break

        # 转换frame为BGRA
        frame_bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        
        # 叠加水印
        alpha = watermark_cv[:, :, 3] / 255.0
        for c in range(3):
            frame_bgra[:, :, c] = frame_bgra[:, :, c] * (1 - alpha) + \
                                 watermark_cv[:, :, c] * alpha

        # 转回BGR格式并写入
        frame_with_watermark = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
        out.write(frame_with_watermark)

        # 更新进度
        processed_frames += 1
        if frame_count > 0:
            progress = (processed_frames / frame_count) * 100
            print(f'\r处理进度: {progress:.1f}%', end='')

    print('\n')  # 换行

    # 释放资源
    video.release()
    out.release()

    # 处理输出文件
    if os.path.exists(temp_output_path):
        if os.path.exists(output_video_path):
            os.remove(output_video_path)
        os.rename(temp_output_path, output_video_path)

def process_videos_in_folder(input_folder, output_folder, watermark_text):
    """
    并行处理指定文件夹中的所有视频文件
    """
    # 创建输出文件夹（如果不存在）
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    # 支持的视频格式
    video_extensions = ('.mp4', '.avi', '.mov', '.mkv')
    
    # 创建任务列表
    tasks = []
    for filename in os.listdir(input_folder):
        if filename.lower().endswith(video_extensions):
            input_path = os.path.join(input_folder, filename)
            output_path = os.path.join(output_folder, f'watermarked_{filename}')
            tasks.append((input_path, output_path, watermark_text))
    
    # 获取CPU核心数
    max_workers = multiprocessing.cpu_count()
    
    # 使用进程池并行处理视频
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for input_path, output_path, text in tasks:
            print(f'添加任务: {os.path.basename(input_path)}')
            future = executor.submit(add_watermark_to_video, input_path, output_path, text)
            futures.append((future, input_path))
        
        # 等待所有任务完成并处理结果
        for future, input_path in futures:
            try:
                future.result()
                print(f'完成: {os.path.basename(input_path)}')
            except Exception as e:
                print(f'处理 {os.path.basename(input_path)} 时出错: {str(e)}')

# 使用示例
if __name__ == "__main__":
    input_folder = "input_videos"  # 输入视频文件夹
    output_folder = "output_videos"  # 输出视频文件夹
    watermark_text = "tiktok"  # 水印文字
    
    process_videos_in_folder(input_folder, output_folder, watermark_text)