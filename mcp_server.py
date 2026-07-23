import os
import glob
import base64
import json
import tempfile
import subprocess
from typing import Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

MOCK = os.getenv("VIDEO_PLUGIN_MOCK", "false").lower() == "true"
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
VIDEO_GEN_ENDPOINT = os.getenv("VIDEO_GEN_ENDPOINT", "")
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")

client = None
if API_KEY and not MOCK:
    from openai import OpenAI
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

mcp = FastMCP("wecom-video-plugin", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))


def _download(url: str, suffix: str = ".mp4") -> str:
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.write(r.content)
    f.close()
    return f.name


def _extract_audio(video_path: str) -> str:
    audio_path = video_path + ".wav"
    subprocess.run([FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
                    "-f", "wav", audio_path], check=True, capture_output=True)
    return audio_path


def _extract_frames(video_path: str, n: int = 5) -> list:
    out_dir = tempfile.mkdtemp()
    subprocess.run([FFMPEG, "-y", "-i", video_path, "-vf", f"fps=1/4", "-frames:v", str(n),
                    os.path.join(out_dir, "frame_%03d.jpg")], check=True, capture_output=True)
    return sorted(glob.glob(os.path.join(out_dir, "*.jpg")))[:n]


@mcp.tool()
def transcribe_video(video_url: str, language: Optional[str] = None) -> str:
    """将视频中的语音转写成文字。video_url 为公网可访问的视频地址；language 为可选语言代码如 zh。"""
    video_path = _download(video_url)
    audio_path = _extract_audio(video_path)
    return _transcribe(audio_path, language)


@mcp.tool()
def summarize_video(video_url: str, focus: Optional[str] = None) -> str:
    """理解视频内容并生成结构化摘要。返回 JSON 字符串：summary/highlights/todos。focus 为可选关注点。"""
    video_path = _download(video_url)
    audio_path = _extract_audio(video_path)
    transcript = _transcribe(audio_path)
    frames = _extract_frames(video_path, 5)
    return json.dumps(_understand(frames, transcript, focus), ensure_ascii=False)


@mcp.tool()
def generate_video(prompt: str, duration: int = 5, ratio: str = "16:9") -> str:
    """根据文本提示生成视频，返回 JSON 字符串（含 video_url）。需配置 VIDEO_GEN_ENDPOINT。"""
    if MOCK or not VIDEO_GEN_ENDPOINT:
        return json.dumps({"video_url": "https://example.com/demo.mp4", "status": "mock"}, ensure_ascii=False)
    r = requests.post(VIDEO_GEN_ENDPOINT, json={"prompt": prompt, "duration": duration, "ratio": ratio},
                      headers={"Authorization": f"Bearer {API_KEY}"}, timeout=300)
    r.raise_for_status()
    return json.dumps(r.json(), ensure_ascii=False)


def _transcribe(audio_path: str, language: Optional[str] = None) -> str:
    if MOCK or client is None:
        return "（示例转写）本周项目进展顺利，前端完成约 80%，后端接口联调中；下周二前完成上线评审。"
    with open(audio_path, "rb") as f:
        kwargs = {"model": TRANSCRIBE_MODEL, "file": f}
        if language:
            kwargs["language"] = language
        return client.audio.transcriptions.create(**kwargs).text


def _understand(frames: list, transcript: str, focus: Optional[str] = None) -> dict:
    if MOCK or client is None:
        return {"summary": "（示例摘要）项目周会，同步进度并明确上线评审事项。",
                "highlights": ["前端约 80%", "后端联调中", "下周二上线评审"],
                "todos": ["张伟准备上线评审材料", "优化导出功能"]}
    content = [{"type": "text",
                "text": f"下面是视频抽帧与转写文本，请生成结构化总结。关注点：{focus or '整体内容概述'}"}]
    for fr in frames:
        b64 = base64.b64encode(open(fr, "rb").read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": f"【语音转写】\n{transcript}"})
    content.append({"type": "text", "text":
        '请以 JSON 返回：{"summary":"一段话总结","highlights":["要点1"],"todos":["待办1"]}'})
    resp = client.chat.completions.create(model=VISION_MODEL,
                                          messages=[{"role": "user", "content": content}],
                                          response_format={"type": "json_object"})
    return json.loads(resp.choices[0].message.content)


if __name__ == "__main__":
    mcp.run(transport="sse")
