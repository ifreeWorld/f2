import os
import subprocess
import platform
from concurrent.futures import ProcessPoolExecutor
import signal
from functools import partial
import time
import select
import io

def get_system_font():
    """
    根据操作系统获取合适的字体
    """
    system = platform.system()
    font_paths = []
    
    if system == 'Windows':
        font_paths = [
            "C:\\Windows\\Fonts\\msyh.ttc",  # 微软雅黑
            "C:\\Windows\\Fonts\\simhei.ttf",  # 黑体
            "C:\\Windows\\Fonts\\simkai.ttf",  # 楷体
            "C:\\Windows\\Fonts\\simsun.ttc",  # 宋体
        ]
    elif system == 'Darwin':  # macOS
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",  # 苹方
            "/System/Library/Fonts/STHeiti Light.ttc",  # 华文黑体
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:  # Linux
        font_paths = [
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]

    # 检查字体文件是否存在
    for font_path in font_paths:
        if os.path.exists(font_path):
            return font_path

    # 如果没有找到合适的字体，返回空字符串
    return ""

def add_watermark_to_video(input_video_path, output_video_path, watermark_text, max_retries=3, timeout=3600):
    """
    使用 ffmpeg 给视频添加文字水印，包含重试机制和超时控制
    """
    retry_count = 0
    while retry_count < max_retries:
        process = None
        try:
            # 获取系统字体
            font_path = get_system_font()
            if not font_path:
                print("警告：未找到合适的字体，将使用默认字体")
                font_settings = "fontfile=Arial"
            else:
                font_settings = f"fontfile='{font_path}'"
            
            # 首先获取视频总帧数
            duration_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=nb_frames',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                input_video_path
            ]
            
            result = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=30)
            total_frames = int(result.stdout.strip())
            
            # 构建 ffmpeg 命令
            cmd = [
                'ffmpeg',
                '-i', input_video_path,
                '-vf', f"drawtext=text='{watermark_text}': \
                        fontcolor=yellow@0.8: \
                        fontsize=h/20: \
                        x=w-tw-20:y=20: \
                        {font_settings}: \
                        box=0: \
                        shadowcolor=black@0.5: \
                        shadowx=2:shadowy=2",
                '-c:v', 'h264',
                '-c:a', 'copy',
                '-y',
                '-progress', 'pipe:1',
                output_video_path
            ]
            
            # 执行 ffmpeg 命令
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                preexec_fn=os.setsid if os.name != 'nt' else None  # 在非Windows系统上使用
            )
            
            # 实时获取处理进度
            current_frame = 0
            start_time = time.time()
            last_progress_time = start_time
            last_frame = 0
            print(f'处理中: {os.path.basename(input_video_path)} - 0.0%', end='', flush=True)
            
            while True:
                current_time = time.time()
                if current_time - start_time > timeout:
                    raise TimeoutError("处理超时")
                
                # 检查进程是否还活着
                if process.poll() is not None:
                    break
                
                # 使用select进行非阻塞读取
                if os.name != 'nt':  # Unix系统
                    reads, _, _ = select.select([process.stderr], [], [], 1.0)
                    if process.stderr in reads:
                        output = process.stderr.readline()
                    else:
                        output = ''
                else:  # Windows系统
                    # Windows下使用简单的轮询
                    output = process.stderr.readline() if process.stderr.readable() else ''
                
                # 检查进度停滞
                if current_time - last_progress_time > 30:
                    if current_frame == last_frame:  # 确认帧数确实没有变化
                        print("\n检测到处理停滞")
                        raise TimeoutError("处理停滞：30秒内没有进度更新")
                    last_frame = current_frame
                    last_progress_time = current_time
                
                if output:
                    if 'frame=' in output:
                        try:
                            current_frame = int(output.split('frame=')[1].split('fps=')[0].strip())
                            progress = (current_frame / total_frames) * 100
                            print(f'\r处理中: {os.path.basename(input_video_path)} - {progress:.1f}%', end='', flush=True)
                            last_progress_time = current_time  # 更新最后进度时间
                        except:
                            continue
            
            # 检查最终处理结果
            if process.returncode != 0:
                stderr_output = process.stderr.read()
                raise Exception(f"视频处理失败，返回码: {process.returncode}, 错误信息: {stderr_output}")
            
            print(f'\r完成: {os.path.basename(input_video_path)} - 100%')
            return  # 成功完成，退出函数
            
        except Exception as e:
            retry_count += 1
            print(f"\n处理视频 {os.path.basename(input_video_path)} 时出错 (尝试 {retry_count}/{max_retries}): {str(e)}")
            
            # 确保进程被终止
            if process:
                try:
                    print(f"正在终止进程...")
                    if os.name != 'nt':  # 非Windows系统
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        except:
                            process.terminate()
                    else:  # Windows系统
                        process.terminate()
                    
                    # 等待进程结束，最多等待5秒
                    for _ in range(50):  # 50 * 0.1 = 5秒
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
                    
                    # 如果进程还在运行，强制结束
                    if process.poll() is None:
                        print("进程未响应SIGTERM，尝试强制终止...")
                        if os.name != 'nt':
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        else:
                            process.kill()
                    
                    process.wait(timeout=1)  # 最后等待一下确保进程已经终止
                    print(f"进程已终止")
                except Exception as term_err:
                    print(f"终止进程时出错: {str(term_err)}")
            
            # 删除失败的输出文件
            try:
                if os.path.exists(output_video_path):
                    os.remove(output_video_path)
                    print(f"已删除失败的输出文件")
            except Exception as del_err:
                print(f"警告：无法删除失败的输出文件: {str(del_err)}")
            
            # 如果不是最后一次重试，等待一段时间后继续
            if retry_count < max_retries:
                wait_time = 5 * retry_count  # 每次重试增加等待时间
                print(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            
            # 最后一次重试失败，抛出异常
            raise Exception(f"处理视频失败，已重试{max_retries}次")

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
            # 移除 'watermarked_' 前缀，直接使用原文件名
            output_path = os.path.join(output_folder, filename)
            tasks.append((input_path, output_path, watermark_text))
    
    if not tasks:
        print(f"在 {input_folder} 中没有找到 视频 文件")
        return
    
    print(f"找到 {len(tasks)} 个 视频 文件")
    
    # 获取CPU核心数
    # max_workers = multiprocessing.cpu_count()
    max_workers = 1
    
    # 使用进程池并行处理视频
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for input_path, output_path, text in tasks:
            print(f'添加任务: {os.path.basename(input_path)}')
            future = executor.submit(
                add_watermark_to_video,
                input_path,
                output_path,
                text,
                max_retries=3,  # 最大重试次数
                timeout=3600    # 超时时间（秒）
            )
            futures.append((future, input_path))
        
        # 等待所有任务完成并处理结果
        for future, input_path in futures:
            try:
                future.result()
            except Exception as e:
                print(f'处理 {os.path.basename(input_path)} 时出错: {str(e)}')

def check_ffmpeg():
    """
    检查系统是否安装了 ffmpeg
    """
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True)
        return True
    except FileNotFoundError:
        return False

if __name__ == "__main__":
    # 检查 ffmpeg 是否已安装
    if not check_ffmpeg():
        print("错误：未检测到 ffmpeg，请先安装 ffmpeg")
        print("Ubuntu/Debian: sudo apt-get install ffmpeg")
        print("MacOS: brew install ffmpeg")
        print("Windows: 请访问 https://ffmpeg.org/download.html 下载安装")
        exit(1)
    
    input_folder = "Download/douyin/mix/铁血影视汇"  # 输入视频文件夹
    output_folder = "Download/douyin/output/铁血影视汇"  # 输出视频文件夹
    watermark_text = "TikTok"  # 水印文字
    
    process_videos_in_folder(input_folder, output_folder, watermark_text)