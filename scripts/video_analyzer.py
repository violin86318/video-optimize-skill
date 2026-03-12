#!/usr/bin/env python3
"""
video_analyzer.py — 爆款视频拆解与优化核心引擎
功能：下载视频 → ffmpeg 压缩 → 豆包大模型原生视频理解 → 8维度爆款拆解 + 5大进阶模块 → 逐场景细拆 → 生成 HTML 报告

Usage:
    python3 video_analyzer.py run "<URL或本地路径>" --title "标题" --archive-dir /Users/wanglingwei/Movies/violinvault/SynologyDrive/Clipping/outputs/reports
    python3 video_analyzer.py download "<URL>" --output video.mp4
    python3 video_analyzer.py compress "<视频路径>" --output compressed.mp4
    python3 video_analyzer.py analyze "<视频路径>" --title "标题"
    python3 video_analyzer.py report "<analysis.json路径>" --video "<视频路径>" --archive-dir /Users/wanglingwei/Movies/violinvault/SynologyDrive/Clipping/outputs/reports
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ─────────────────── API Configuration ───────────────────
# 此处设置默认值，将通过配置管理器动态加载或从配置/环境变量覆盖
API_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"
API_MODEL = "doubao-seed-2-0-pro-260215"
API_KEY = ""
DEFAULT_ARCHIVE_DIR = "./outputs/reports"

# ─────────────────── Constants ───────────────────
TARGET_SIZE_MB = 35          # 压缩目标（base64 后 ≈47MB < 50MB API 限制）
BASE64_LIMIT_MB = 50         # API base64 上限
MAX_HEIGHT = 720             # 最大分辨率高度
MAX_API_RETRIES = 3          # API 最大重试次数
SCREENSHOT_INTERVAL = 20     # 截图间隔（秒）

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent


# ═══════════════════════════════════════════════════════════
# 0. 配置管理
# ═══════════════════════════════════════════════════════════

CONFIG_FILE = os.path.expanduser("~/.video_optimize_config.json")

def load_config():
    global API_KEY, API_MODEL, API_ENDPOINT, DEFAULT_ARCHIVE_DIR
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"[配置] 读取配置失败: {e}")

    # 环境变量优先级最高，其次是配置文件，最后是代码默认值
    API_KEY = os.environ.get("DOUBAO_API_KEY", config.get("API_KEY", ""))
    API_MODEL = os.environ.get("DOUBAO_MODEL", config.get("MODEL", "doubao-seed-2-0-pro-260215"))
    API_ENDPOINT = os.environ.get("DOUBAO_API_ENDPOINT", config.get("ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3/responses"))
    DEFAULT_ARCHIVE_DIR = config.get("DEFAULT_ARCHIVE_DIR", "./outputs/reports")

def save_config(api_key, model, endpoint, archive_dir):
    config = {
        "API_KEY": api_key,
        "MODEL": model,
        "ENDPOINT": endpoint,
        "DEFAULT_ARCHIVE_DIR": archive_dir
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[配置] 已保存至: {CONFIG_FILE}")
    except Exception as e:
        print(f"[配置] 保存配置失败: {e}")

def interactive_setup():
    print("\n" + "═"*60)
    print(" 🛠️  视频分析技能 (video-optimize) 首次配置引导")
    print("═"*60)
    print(f"此配置将保存在 {CONFIG_FILE} 中。")
    print("按回车键使用括号中的默认值或保留当前值。\n")

    load_config()  # 先加载一次获取默认值提示

    api_key = input(f"请输入 Doubao API Key [{API_KEY if API_KEY else '必填'}]: ").strip()
    if not api_key:
        api_key = API_KEY
    while not api_key:
        print("API Key 不能为空！")
        api_key = input("请输入 Doubao API Key: ").strip()

    model = input(f"请输入模型版本 [{API_MODEL}]: ").strip()
    if not model:
        model = API_MODEL

    endpoint = input(f"请输入 API Endpoint [{API_ENDPOINT}]: ").strip()
    if not endpoint:
        endpoint = API_ENDPOINT

    archive_dir = input(f"请输入默认的报告输出目录 [{DEFAULT_ARCHIVE_DIR}]: ").strip()
    if not archive_dir:
        archive_dir = DEFAULT_ARCHIVE_DIR

    save_config(api_key, model, endpoint, archive_dir)
    load_config()  # 更新全局变量
    print("\n✅ 配置完成！\n")

def ensure_config():
    load_config()
    if not API_KEY:
        print("[配置] 未检测到 API_KEY，进入配置引导流程...")
        interactive_setup()


# ═══════════════════════════════════════════════════════════
# 1. 平台检测
# ═══════════════════════════════════════════════════════════

def detect_platform(url: str) -> str:
    """检测视频链接所属平台"""
    url_lower = url.lower()
    if any(x in url_lower for x in ["bilibili.com", "b23.tv"]):
        return "bilibili"
    if any(x in url_lower for x in ["youtube.com", "youtu.be"]):
        return "youtube"
    if any(x in url_lower for x in ["xiaohongshu.com", "xhslink.com"]):
        return "xiaohongshu"
    if any(x in url_lower for x in ["douyin.com", "iesdouyin.com"]):
        return "douyin"
    return "unknown"


# ═══════════════════════════════════════════════════════════
# 2. 下载模块
# ═══════════════════════════════════════════════════════════

def download_video(url: str, output_path: str = None) -> str:
    """
    下载视频。
    - 本地文件直接返回路径
    - B站/YouTube 用 yt-dlp
    - 小红书/抖音 exit code 2，提示走浏览器 fallback
    """
    # 本地文件
    if os.path.isfile(url):
        print(f"[下载] 本地文件: {url}")
        return os.path.abspath(url)

    platform = detect_platform(url)
    print(f"[下载] 检测到平台: {platform}")

    if platform in ("xiaohongshu", "douyin"):
        print(f"[下载] {platform} 不支持 yt-dlp 下载，请使用浏览器 fallback 方案。")
        print(f"[下载] 参见 FALLBACK.md 获取详细步骤。")
        sys.exit(2)

    if platform == "unknown":
        print(f"[下载] 未识别平台，尝试用 yt-dlp 下载...")

    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), f"video_{int(time.time())}.mp4")

    # 检查 yt-dlp 是否可用
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[下载] 错误: yt-dlp 未安装。请运行: brew install yt-dlp 或 pip install yt-dlp")
        sys.exit(1)

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        url
    ]

    print(f"[下载] 执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr
        if "412" in stderr or "403" in stderr:
            print(f"[下载] yt-dlp 被反爬 (HTTP 412/403)，请使用浏览器 fallback 方案。")
            print(f"[下载] 参见 FALLBACK.md 获取详细步骤。")
            sys.exit(2)
        print(f"[下载] yt-dlp 下载失败:\n{stderr}")
        sys.exit(1)

    # yt-dlp 可能修改文件名，搜索实际输出文件
    if not os.path.isfile(output_path):
        # 尝试查找同目录下最新的 mp4 文件
        output_dir = os.path.dirname(output_path) or "."
        candidates = sorted(
            [os.path.join(output_dir, f) for f in os.listdir(output_dir)
             if f.endswith(".mp4")],
            key=os.path.getmtime, reverse=True
        )
        if candidates:
            output_path = candidates[0]
        else:
            print("[下载] 错误: 未找到下载的视频文件")
            sys.exit(1)

    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"[下载] 完成: {output_path} ({file_size_mb:.1f} MB)")
    return output_path


# ═══════════════════════════════════════════════════════════
# 3. 压缩模块
# ═══════════════════════════════════════════════════════════

def get_video_info(video_path: str) -> dict:
    """用 ffprobe 获取视频信息"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[信息] ffprobe 失败: {result.stderr}")
        return {}
    return json.loads(result.stdout)


def get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    info = get_video_info(video_path)
    if "format" in info and "duration" in info["format"]:
        return float(info["format"]["duration"])
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and "duration" in stream:
            return float(stream["duration"])
    return 0.0


def get_video_height(video_path: str) -> int:
    """获取视频高度"""
    info = get_video_info(video_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream.get("height", 0))
    return 0


def file_to_base64_size_mb(file_path: str) -> float:
    """计算文件 base64 编码后的大小（MB）"""
    file_size = os.path.getsize(file_path)
    # base64 编码后大小 ≈ 原始大小 × 4/3
    return (file_size * 4 / 3) / (1024 * 1024)


def compress_video(video_path: str, output_path: str = None, target_mb: float = TARGET_SIZE_MB) -> str:
    """
    压缩视频到目标大小。
    - 如果视频 < target_mb 且 base64 < 50MB，仅做 faststart
    - 否则按目标码率压缩
    """
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), f"compressed_{int(time.time())}.mp4")

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    b64_size_mb = file_to_base64_size_mb(video_path)

    print(f"[压缩] 原始大小: {file_size_mb:.1f} MB, base64: {b64_size_mb:.1f} MB")

    if file_size_mb <= target_mb and b64_size_mb <= BASE64_LIMIT_MB:
        print(f"[压缩] 无需压缩，仅做 faststart 处理")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-c", "copy", "-movflags", "+faststart",
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"[压缩] faststart 完成: {output_path}")
        return output_path

    # 需要压缩
    duration = get_video_duration(video_path)
    if duration <= 0:
        print("[压缩] 警告: 无法获取视频时长，使用默认码率")
        duration = 120  # 默认假设2分钟

    height = get_video_height(video_path)

    # 计算目标码率 (kbps)
    # target_mb * 8 * 1024 kbits / duration_seconds - 128kbps audio
    target_video_bitrate = int((target_mb * 8 * 1024) / duration - 128)
    if target_video_bitrate < 200:
        target_video_bitrate = 200

    print(f"[压缩] 时长: {duration:.1f}s, 目标视频码率: {target_video_bitrate}kbps")

    # 构建 ffmpeg 命令
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", f"{target_video_bitrate}k",
        "-maxrate", f"{int(target_video_bitrate * 1.5)}k",
        "-bufsize", f"{int(target_video_bitrate * 2)}k",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
    ]

    # 如果高度超过 720p，缩放
    if height > MAX_HEIGHT:
        cmd.extend(["-vf", f"scale=-2:{MAX_HEIGHT}"])
        print(f"[压缩] 缩放: {height}p → {MAX_HEIGHT}p")

    cmd.append(output_path)

    print(f"[压缩] 执行压缩 (第1次)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[压缩] ffmpeg 错误: {result.stderr[-500:]}")
        sys.exit(1)

    # 检查压缩结果
    new_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    new_b64_mb = file_to_base64_size_mb(output_path)
    print(f"[压缩] 第1次结果: {new_size_mb:.1f} MB, base64: {new_b64_mb:.1f} MB")

    # 如果 base64 仍然超限，二次压缩
    if new_b64_mb > BASE64_LIMIT_MB:
        print("[压缩] base64 仍超限，执行二次压缩...")
        second_target_mb = target_mb * 0.6  # 更激进
        target_video_bitrate = int((second_target_mb * 8 * 1024) / duration - 96)
        if target_video_bitrate < 100:
            target_video_bitrate = 100

        second_output = output_path + ".2nd.mp4"
        cmd2 = [
            "ffmpeg", "-y", "-i", output_path,
            "-c:v", "libx264", "-preset", "slow",
            "-b:v", f"{target_video_bitrate}k",
            "-maxrate", f"{int(target_video_bitrate * 1.2)}k",
            "-bufsize", f"{int(target_video_bitrate * 1.5)}k",
            "-c:a", "aac", "-b:a", "64k",
            "-vf", f"scale=-2:{min(height, 480)}",
            "-movflags", "+faststart",
            second_output
        ]
        subprocess.run(cmd2, capture_output=True, check=True)

        # 替换输出文件
        os.replace(second_output, output_path)
        final_size = os.path.getsize(output_path) / (1024 * 1024)
        final_b64 = file_to_base64_size_mb(output_path)
        print(f"[压缩] 二次压缩完成: {final_size:.1f} MB, base64: {final_b64:.1f} MB")

    # 如果 base64 仍然超限，三次压缩（超长视频）
    if file_to_base64_size_mb(output_path) > BASE64_LIMIT_MB:
        print("[压缩] 仍超限，执行三次压缩...")
        third_target_mb = 25  # 更小的目标
        target_video_bitrate = int((third_target_mb * 8 * 1024) / duration - 64)
        if target_video_bitrate < 80:
            target_video_bitrate = 80

        third_output = output_path + ".3rd.mp4"
        cmd3 = [
            "ffmpeg", "-y", "-i", output_path,
            "-c:v", "libx264", "-preset", "slow",
            "-b:v", f"{target_video_bitrate}k",
            "-maxrate", f"{int(target_video_bitrate * 1.2)}k",
            "-bufsize", f"{int(target_video_bitrate * 1.5)}k",
            "-c:a", "aac", "-b:a", "48k",
            "-vf", "scale=-2:360",
            "-movflags", "+faststart",
            third_output
        ]
        subprocess.run(cmd3, capture_output=True, check=True)

        os.replace(third_output, output_path)
        final_size = os.path.getsize(output_path) / (1024 * 1024)
        final_b64 = file_to_base64_size_mb(output_path)
        print(f"[压缩] 三次压缩完成: {final_size:.1f} MB, base64: {final_b64:.1f} MB")

    return output_path


# ═══════════════════════════════════════════════════════════
# 4. 截图模块
# ═══════════════════════════════════════════════════════════

def extract_screenshots(video_path: str, interval: int = SCREENSHOT_INTERVAL) -> list:
    """
    用 ffmpeg 每隔 interval 秒抽一帧，返回 [{time, base64}] 列表
    """
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []

    screenshots = []
    tmpdir = tempfile.mkdtemp(prefix="screenshots_")

    # 使用 fps filter 抽帧
    fps_val = 1.0 / interval
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps_val},scale=320:-1",
        "-q:v", "5",
        os.path.join(tmpdir, "frame_%04d.jpg")
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    # 读取抽帧结果
    frame_files = sorted([f for f in os.listdir(tmpdir) if f.startswith("frame_") and f.endswith(".jpg")])
    for i, fname in enumerate(frame_files):
        fpath = os.path.join(tmpdir, fname)
        with open(fpath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")
        t = i * interval
        screenshots.append({
            "time": t,
            "time_str": f"{int(t // 60):02d}:{int(t % 60):02d}",
            "base64": img_b64
        })
        os.unlink(fpath)

    # 清理
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass

    print(f"[截图] 提取了 {len(screenshots)} 张截图")
    return screenshots


# ═══════════════════════════════════════════════════════════
# 5. API 调用模块
# ═══════════════════════════════════════════════════════════

def video_to_base64_url(video_path: str) -> str:
    """将视频文件转为 base64 data URL"""
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    b64 = base64.b64encode(video_bytes).decode("ascii")
    return f"data:video/mp4;base64,{b64}"


def call_doubao_api(video_data_url: str, prompt: str) -> str:
    """
    调用豆包大模型 API（原生视频理解）
    返回模型的文本响应
    """
    # 豆包 API 格式
    payload = {
        "model": API_MODEL,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_video", "video_url": video_data_url},
                {"type": "input_text", "text": prompt}
            ]
        }]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            print(f"[API] 第 {attempt}/{MAX_API_RETRIES} 次调用（payload {len(data) / 1024 / 1024:.1f}MB）...")
            req = urllib.request.Request(API_ENDPOINT, data=data, headers=headers, method="POST")
            # 设置超时为 5 分钟（视频分析较慢）
            with urllib.request.urlopen(req, timeout=300) as resp:
                response_text = resp.read().decode("utf-8")
            print(f"[API] 响应长度: {len(response_text)} 字符")
            return response_text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"[API] HTTP {e.code} 错误: {body[:500]}")
            if attempt < MAX_API_RETRIES:
                wait = attempt * 10
                print(f"[API] 等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                raise
        except urllib.error.URLError as e:
            print(f"[API] 网络错误: {e.reason}")
            if attempt < MAX_API_RETRIES:
                wait = attempt * 10
                print(f"[API] 等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            print(f"[API] 未知错误: {e}")
            if attempt < MAX_API_RETRIES:
                wait = attempt * 5
                print(f"[API] 等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                raise


def parse_api_response(response_text: str) -> str:
    """
    解析 API 响应，提取模型输出文本。
    兼容 Gemini 和豆包等多种返回格式。
    """
    try:
        resp = json.loads(response_text)
    except json.JSONDecodeError:
        # 如果整个响应不是 JSON，直接返回
        return response_text

    # 格式0: Gemini API — candidates[0].content.parts[0].text
    if "candidates" in resp:
        candidates = resp.get("candidates", [])
        if candidates and isinstance(candidates, list):
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if parts and isinstance(parts, list):
                text = parts[0].get("text", "")
                if text:
                    return text

    # 格式1: responses API — output 为列表
    if "output" in resp:
        output = resp["output"]
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict):
                    if item.get("type") == "message":
                        content = item.get("content", [])
                        if isinstance(content, list):
                            texts = []
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "output_text":
                                    texts.append(c.get("text", ""))
                            if texts:
                                return "\n".join(texts)
                        elif isinstance(content, str):
                            return content
                    # 直接有 text 字段
                    if "text" in item:
                        return item["text"]
            # output 列表无法解析，尝试拼接所有 text
            all_texts = []
            for item in output:
                if isinstance(item, dict):
                    for c in item.get("content", []):
                        if isinstance(c, dict) and "text" in c:
                            all_texts.append(c["text"])
            if all_texts:
                return "\n".join(all_texts)

        # output 为 dict
        elif isinstance(output, dict):
            if "text" in output:
                return output["text"]
            content = output.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                return "\n".join(texts)

        # output 为 str
        elif isinstance(output, str):
            return output

    # 格式2: chat completions — choices[0].message.content
    if "choices" in resp:
        choices = resp["choices"]
        if choices and isinstance(choices, list):
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if content:
                return content

    # 最后尝试：直接返回整个 JSON 字符串
    return response_text


def extract_json_from_text(text: str) -> dict:
    """
    从模型输出文本中提取 JSON 对象。
    处理 ```json ... ``` 代码块和裸 JSON。
    """
    # 方法1: 从 ```json ... ``` 代码块中提取
    pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    # 方法2: 用大括号匹配法提取最外层 JSON object
    brace_start = text.find('{')
    if brace_start >= 0:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(brace_start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[brace_start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # 方法3: 尝试直接解析整段文本
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    print("[解析] 警告: 无法从响应中提取 JSON，返回原始文本包装")
    return {"raw_response": text}


# ═══════════════════════════════════════════════════════════
# 6. 分析 Prompt 设计
# ═══════════════════════════════════════════════════════════

ANALYSIS_PROMPT = r'''你是一位爆款视频分析专家。请对这段视频进行深度拆解分析，严格按照以下 JSON 结构返回结果。

## 评分规则（反锚定指令，必须严格遵守）

你必须基于视频的实际质量独立评分，不要受到示例格式中任何数字的影响。评分应有明显区分度：
- 1-3分：差，明显不足，业余水平
- 4-5分：一般，有基本功但缺乏亮点
- 6-7分：良好，有一定专业度和创意
- 8-9分：优秀，接近头部水平
- 10分：顶级，教科书级别（极少给出）

overall_score 应该是8个维度评分的加权平均（hook和narrative权重更高），不要简单给一个笼统的高分。
差的视频就应该给低分（3-5分），一般的给中间分（5-7分），只有真正优秀的才给8分以上。

## 输出 JSON 结构

```json
{
  "overall_score": <float 1-10>,
  "summary": "<200字以内的总体评价>",

  "hook": {
    "score": <int 1-10>,
    "description": "<分析开头吸引力/黄金3秒>",
    "formula": "<提炼的 hook 公式>",
    "template": "<可复用的 hook 模板>"
  },
  "narrative": {
    "score": <int 1-10>,
    "type": "<叙事类型，如：线性/倒叙/对比/问题解决/故事弧>",
    "description": "<叙事结构分析>",
    "timeline": [
      {"start": "<mm:ss>", "end": "<mm:ss>", "label": "<章节名>", "description": "<这段做了什么>"}
    ],
    "template": "<叙事模板>"
  },
  "pacing": {
    "score": <int 1-10>,
    "description": "<节奏感分析>",
    "cut_points": ["<mm:ss 切换点>"],
    "pattern": "<节奏模式描述>"
  },
  "visual": {
    "score": <int 1-10>,
    "description": "<视觉构图分析>",
    "shots": [{"time": "<mm:ss>", "type": "<镜头类型>", "description": "<描述>"}],
    "color_style": "<色彩风格>",
    "effects": ["<使用的视觉效果>"]
  },
  "text_overlay": {
    "score": <int 1-10>,
    "description": "<字幕/文字设计分析>",
    "has_text": <bool>,
    "style": "<字幕样式描述>",
    "highlights": ["<突出的字幕设计亮点>"]
  },
  "audio": {
    "score": <int 1-10>,
    "description": "<音乐音效分析>",
    "estimated_bpm": <int or null>,
    "sync_evidence": "<音画同步证据>",
    "voice_style": "<人声风格描述>"
  },
  "cta": {
    "score": <int 1-10>,
    "description": "<互动引导分析>",
    "has_cta": <bool>,
    "cta_time": "<mm:ss or null>",
    "cta_type": "<CTA类型：口播/字幕/弹窗/结尾card>"
  },
  "ending": {
    "score": <int 1-10>,
    "description": "<结尾设计分析>",
    "is_loopable": <bool>,
    "has_series_hook": <bool>,
    "ending_type": "<结尾类型：总结/反转/悬念/号召/开放>"
  },

  "emotional_arc": {
    "arc_type": "<弧线类型：U型/倒U/递增/波浪/平缓>",
    "arc_description": "<情绪弧线描述>",
    "curve_points": [
      {"time": "<mm:ss>", "valence": <float -5 to 5>, "arousal": <float 0 to 10>, "label": "<情绪标签>"}
    ],
    "turning_points": [
      {"time": "<mm:ss>", "type": "<转折类型>", "description": "<转折描述>"}
    ]
  },
  "retention_prediction": {
    "hook_rate_3s": <float 0-100>,
    "retention_30s": <float 0-100>,
    "midpoint_retention": <float 0-100>,
    "completion_rate": <float 0-100>,
    "risk_segments": [
      {"time": "<mm:ss-mm:ss>", "risk": "<low/medium/high>", "label": "<风险标签>", "reason": "<原因>", "fix": "<修复建议>"}
    ]
  },
  "viral_formulas": {
    "script_formula": {
      "steps": ["<步骤1>", "<步骤2>"],
      "fill_template": "<可填空的脚本模板>"
    },
    "emotion_formula": {
      "nodes": [{"emotion": "<情绪>", "trigger": "<触发方式>"}],
      "key_principles": ["<原则>"]
    },
    "algorithm_formula": {
      "drivers": ["<算法驱动因素>"],
      "weight_tips": ["<权重提示>"]
    }
  },
  "algorithm_fitness": {
    "metrics": {
      "completion_rate": <float 0-100>,
      "interaction_rate": <float 0-100>,
      "share_rate": <float 0-100>,
      "save_rate": <float 0-100>
    },
    "platform_fit": [
      {"platform": "<B站/抖音/小红书/YouTube>", "score": <int 1-10>, "reason": "<原因>", "recommended": <bool>}
    ]
  },
  "learning_path": [
    {
      "rank": <int>,
      "technique": "<技巧名>",
      "difficulty": "<入门/进阶/高级>",
      "why": "<为什么要学>",
      "exercises": ["<练习任务>"],
      "reference": "<参考案例>"
    }
  ],

  "replicable_template": {
    "structure": "<结构公式描述>",
    "shot_list": [{"order": <int>, "shot": "<镜头>", "duration": "<时长>", "note": "<注意事项>"}],
    "script_template": "<可填空的文案模板>"
  },
  "top3_strengths": ["<亮点1>", "<亮点2>", "<亮点3>"],
  "top3_improvements": ["<改进1>", "<改进2>", "<改进3>"]
}
```

请直接返回 JSON，不要添加任何前缀说明或后缀说明。确保 JSON 格式正确无误可以被解析。
'''


def build_scene_breakdown_prompt(chapters: list) -> str:
    """构建逐场景细拆的 prompt"""
    chapters_text = ""
    for i, ch in enumerate(chapters, 1):
        start = ch.get("start", "00:00")
        end = ch.get("end", "??:??")
        label = ch.get("label", f"章节{i}")
        desc = ch.get("description", "")
        chapters_text += f"  章节{i}: [{start} - {end}] {label} — {desc}\n"

    return f'''你是一位爆款视频分析专家。这是同一段视频的第二次分析。

第一次分析已经识别出以下章节分段：
{chapters_text}

现在请对每个章节进行更细粒度的拆解，按 15-25 秒为一个 scene 进行分析。

每个章节拆分为 2-5 个 scene，输出 JSON 格式如下：

```json
{{
  "chapters": [
    {{
      "chapter_index": 1,
      "label": "<章节名>",
      "start": "<mm:ss>",
      "end": "<mm:ss>",
      "scenes": [
        {{
          "scene_index": 1,
          "start": "<mm:ss>",
          "end": "<mm:ss>",
          "visual": "<画面描述>",
          "audio": "<音频描述>",
          "emotion": "<情绪标签>",
          "emotion_valence": <float -5 to 5>,
          "emotion_arousal": <float 0 to 10>,
          "retention_risk": "<low/medium/high>",
          "risk_reason": "<风险原因，low时可为空>",
          "risk_fix": "<修复建议，low时可为空>",
          "quote": "<台词/旁白原文节选>",
          "techniques": [
            {{
              "name": "<手法名称>",
              "category": "<Hook/留存/节奏/情绪/信任/互动/视觉>",
              "why": "<为什么这个手法有效>"
            }}
          ]
        }}
      ]
    }}
  ]
}}
```

## 场景细拆规则

1. 每个 scene 时长 15-25 秒，允许末尾 scene 稍短
2. emotion_valence 范围 -5 到 +5（负=消极，0=中性，正=积极）
3. emotion_arousal 范围 0 到 10（0=低唤醒/平静，10=高唤醒/激动）
4. retention_risk 判断标准：
   - 画面单一超过15秒 → medium
   - 纯文字无变化超过20秒 → high
   - 抽象概念无类比无举例 → medium
   - 节奏突然变慢 → medium
   - low = 节奏好、画面丰富、信息密度适中
5. techniques 的 category 严格限定为：Hook/留存/节奏/情绪/信任/互动/视觉

请直接返回 JSON，不要添加任何前缀说明或后缀说明。确保 JSON 格式正确无误可以被解析。
'''


# ═══════════════════════════════════════════════════════════
# 7. 完整分析流水线
# ═══════════════════════════════════════════════════════════

def analyze_video(video_path: str, title: str = "未命名视频") -> dict:
    """
    完整分析流水线：
    Step 1: 视频转 base64
    Step 2: 第一次 API 调用 — 8维度 + 5进阶模块
    Step 3: 第二次 API 调用 — 逐场景细拆
    Step 4: 合并结果
    """
    # ── Step 1: 准备 base64 ──
    print(f"\n{'='*60}")
    print(f"[分析] 开始分析: {title}")
    print(f"{'='*60}")

    b64_size = file_to_base64_size_mb(video_path)
    if b64_size > BASE64_LIMIT_MB:
        print(f"[分析] 错误: 视频 base64 ({b64_size:.1f}MB) 超过 API 限制 ({BASE64_LIMIT_MB}MB)")
        print("[分析] 请先压缩视频。")
        sys.exit(1)

    video_data_url = video_to_base64_url(video_path)
    print(f"[分析] base64 data URL 生成完毕 ({b64_size:.1f} MB)")

    # ── Step 2: 第一次 API 调用（8维度 + 5进阶） ──
    print(f"\n[分析] ===== 第一次 API 调用: 8维度分析 + 5进阶模块 =====")
    response_text = call_doubao_api(video_data_url, ANALYSIS_PROMPT)
    model_output = parse_api_response(response_text)
    analysis = extract_json_from_text(model_output)

    if "raw_response" in analysis:
        print("[分析] 警告: 模型未返回有效 JSON，分析可能不完整。")
        print(f"[分析] 原始响应: {model_output[:500]}...")

    # 添加元数据
    analysis["_meta"] = {
        "title": title,
        "video_path": video_path,
        "duration": get_video_duration(video_path),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": API_MODEL
    }

    print(f"[分析] 第一次分析完成。overall_score: {analysis.get('overall_score', 'N/A')}")

    # ── Step 3: 第二次 API 调用（逐场景细拆） ──
    timeline = []
    if "narrative" in analysis and "timeline" in analysis["narrative"]:
        timeline = analysis["narrative"]["timeline"]

    if timeline:
        print(f"\n[分析] ===== 第二次 API 调用: 逐场景细拆 ({len(timeline)} 个章节) =====")
        scene_prompt = build_scene_breakdown_prompt(timeline)
        scene_response = call_doubao_api(video_data_url, scene_prompt)
        scene_output = parse_api_response(scene_response)
        scene_data = extract_json_from_text(scene_output)

        if "chapters" in scene_data:
            analysis["scene_breakdown"] = scene_data["chapters"]
            total_scenes = sum(len(ch.get("scenes", [])) for ch in scene_data["chapters"])
            print(f"[分析] 场景细拆完成: {len(scene_data['chapters'])} 章节, {total_scenes} 个 scene")
        else:
            print("[分析] 警告: 场景细拆未返回有效数据")
            analysis["scene_breakdown"] = []
    else:
        print("[分析] 警告: 未检测到 timeline 章节，跳过场景细拆")
        analysis["scene_breakdown"] = []

    # ── Step 4: 提取截图 ──
    print(f"\n[分析] 提取视频截图...")
    screenshots = extract_screenshots(video_path)
    analysis["_screenshots"] = screenshots

    print(f"\n[分析] ✅ 分析完成!")
    return analysis


# ═══════════════════════════════════════════════════════════
# 8. 报告生成调用
# ═══════════════════════════════════════════════════════════

def generate_report(analysis: dict, video_path: str, archive_dir: str = None) -> str:
    """调用 report_generator.py 生成 HTML 报告"""
    report_gen = SCRIPT_DIR / "report_generator.py"
    if not report_gen.exists():
        print(f"[报告] 错误: 找不到 report_generator.py: {report_gen}")
        sys.exit(1)

    # 保存分析 JSON 到临时文件
    analysis_json_path = os.path.join(tempfile.gettempdir(), f"analysis_{int(time.time())}.json")
    with open(analysis_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    # 构建命令
    cmd = [
        sys.executable, str(report_gen),
        analysis_json_path,
        "--video", video_path
    ]
    if archive_dir:
        cmd.extend(["--archive-dir", archive_dir])

    print(f"[报告] 执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"[报告] 报告生成失败 (exit code {result.returncode})")
        sys.exit(1)

    # 清理临时文件
    try:
        os.unlink(analysis_json_path)
    except OSError:
        pass

    # 从 stdout 中提取报告路径
    for line in result.stdout.strip().split("\n"):
        if line.startswith("[报告] HTML 报告:"):
            return line.split(": ", 1)[1].strip()
        if line.startswith("[报告] 完整报告:") or line.startswith("[报告] report.html:"):
            return line.split(": ", 1)[1].strip()

    return ""


# ═══════════════════════════════════════════════════════════
# 9. 主流水线 (run)
# ═══════════════════════════════════════════════════════════

def run_pipeline(source: str, title: str = None, archive_dir: str = None):
    """
    完整流水线：下载 → 压缩 → 分析 → 报告
    """
    print(f"\n{'═'*60}")
    print(f" 爆款视频拆解与优化引擎")
    print(f"{'═'*60}")
    print(f" 输入: {source}")
    print(f" 标题: {title or '自动检测'}")
    print(f"{'═'*60}\n")

    # Step 1: 下载
    print("━" * 40 + " Step 1: 下载 " + "━" * 40)
    video_path = download_video(source)

    # 自动生成标题
    if not title:
        title = Path(video_path).stem
        title = re.sub(r'[_\-]+', ' ', title).strip()
        if not title:
            title = "未命名视频"

    # Step 2: 压缩
    print("\n" + "━" * 40 + " Step 2: 压缩 " + "━" * 40)
    compressed_path = compress_video(video_path)

    # Step 3 & 4: 分析（8维度 + 场景细拆）
    print("\n" + "━" * 38 + " Step 3-4: AI 分析 " + "━" * 38)
    analysis = analyze_video(compressed_path, title)

    # Step 5: 生成报告
    print("\n" + "━" * 38 + " Step 5: 生成报告 " + "━" * 38)
    report_path = generate_report(analysis, compressed_path, archive_dir)

    # ── 输出总结 ──
    print(f"\n{'═'*60}")
    print(f" ✅ 分析完成!")
    print(f"{'═'*60}")
    print(f" 视频: {video_path}")
    print(f" 标题: {title}")
    print(f" 总评分: {analysis.get('overall_score', 'N/A')}")
    if report_path:
        print(f" 报告: {report_path}")
    print(f"{'═'*60}")

    return analysis, report_path


# ═══════════════════════════════════════════════════════════
# 10. CLI 入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="爆款视频拆解与优化引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  run       完整流水线：下载 → 压缩 → 分析 → 报告
  download  仅下载视频
  compress  仅压缩视频
  analyze   仅分析视频（需要已压缩的视频）
  report    仅生成报告（需要分析 JSON + 视频）
  config    使用交互式向导配置 API Key 和默认输出路径

示例:
<<<<<<< HEAD
  python3 video_analyzer.py config
  python3 video_analyzer.py run "https://www.bilibili.com/video/BV..." --title "测试"
  python3 video_analyzer.py run "/path/to/video.mp4" --archive-dir ./custom/dir
  python3 video_analyzer.py run "https://www.bilibili.com/video/BV..." --title "测试" --archive-dir /Users/wanglingwei/Movies/violinvault/SynologyDrive/Clipping/outputs/reports
  python3 video_analyzer.py download "https://www.youtube.com/watch?v=..." --output video.mp4
  python3 video_analyzer.py analyze compressed.mp4 --title "标题"
  python3 video_analyzer.py report analysis.json --video compressed.mp4 --archive-dir /Users/wanglingwei/Movies/violinvault/SynologyDrive/Clipping/outputs/reports
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ── run ──
    run_parser = subparsers.add_parser("run", help="完整流水线")
    run_parser.add_argument("source", help="视频 URL 或本地路径")
    run_parser.add_argument("--title", "-t", help="视频标题")
    run_parser.add_argument("--archive-dir", "-a", default=None, help="报告归档目录（留空则使用本地配置中的默认值）")

    # ── download ──
    dl_parser = subparsers.add_parser("download", help="仅下载视频")
    dl_parser.add_argument("url", help="视频 URL")
    dl_parser.add_argument("--output", "-o", default=None, help="输出路径")

    # ── compress ──
    comp_parser = subparsers.add_parser("compress", help="仅压缩视频")
    comp_parser.add_argument("video", help="视频路径")
    comp_parser.add_argument("--output", "-o", default=None, help="输出路径")
    comp_parser.add_argument("--target-mb", type=float, default=TARGET_SIZE_MB, help=f"目标大小 MB (默认 {TARGET_SIZE_MB})")

    # ── analyze ──
    ana_parser = subparsers.add_parser("analyze", help="仅分析视频")
    ana_parser.add_argument("video", help="视频路径（应为压缩后的视频）")
    ana_parser.add_argument("--title", "-t", default="未命名视频", help="视频标题")
    ana_parser.add_argument("--output", "-o", default=None, help="输出 JSON 路径")

    # ── report ──
    rpt_parser = subparsers.add_parser("report", help="仅生成报告")
    rpt_parser.add_argument("analysis_json", help="分析 JSON 文件路径")
    rpt_parser.add_argument("--video", "-v", required=True, help="视频文件路径")
    rpt_parser.add_argument("--archive-dir", "-a", default=None, help="报告归档目录（留空则使用本地配置中的默认值）")

    # ── config ──
    subparsers.add_parser("config", help="交互式配置向导（设置 API Key 和输出目录）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "config":
        interactive_setup()

    elif args.command == "run":
        ensure_config()
        run_pipeline(args.source, args.title, args.archive_dir or DEFAULT_ARCHIVE_DIR)

    elif args.command == "download":
        path = download_video(args.url, args.output)
        print(f"下载完成: {path}")

    elif args.command == "compress":
        path = compress_video(args.video, args.output, args.target_mb)
        print(f"压缩完成: {path}")

    elif args.command == "analyze":
        ensure_config()
        analysis = analyze_video(args.video, args.title)
        output_path = args.output or f"analysis_{int(time.time())}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        print(f"分析结果已保存: {output_path}")

    elif args.command == "report":
        ensure_config()
        with open(args.analysis_json, "r", encoding="utf-8") as f:
            analysis = json.load(f)
        report_path = generate_report(analysis, args.video, args.archive_dir or DEFAULT_ARCHIVE_DIR)
        if report_path:
            print(f"报告已生成: {report_path}")


if __name__ == "__main__":
    main()
