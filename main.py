import os
import glob
import base64
import json
import tempfile
import subprocess
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse

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

app = FastAPI(title="WeCom Video Plugin")


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


def _extract_frames(video_path: str, n: int = 5) -> List[str]:
    out_dir = tempfile.mkdtemp()
    subprocess.run([FFMPEG, "-y", "-i", video_path, "-vf", f"fps=1/4", "-frames:v", str(n),
         os.path.join(out_dir, "frame_%03d.jpg")], check=True, capture_output=True)
    return sorted(glob.glob(os.path.join(out_dir, "*.jpg")))[:n]


def _transcribe(audio_path: str, language: Optional[str] = None) -> str:
    if MOCK or client is None:
        return ("示例转写：本周项目进展顺利，前端已完成约80%，后端接口进入联调；"
                "下周二前需完成上线评审，由张伟负责；客户反馈导出功能还需优化。")
    with open(audio_path, "rb") as f:
        kwargs = {"model": TRANSCRIBE_MODEL, "file": f}
        if language:
            kwargs["language"] = language
        resp = client.audio.transcriptions.create(**kwargs)
    return resp.text


def _understand(frames: List[str], transcript: str, focus: Optional[str] = None) -> Dict[str, Any]:
    if MOCK or client is None:
        return {
            "summary": "示例摘要：这是一段项目周会录像，主要同步了研发进度并明确了下周上线评审事项。",
            "highlights": ["前端开发完成约80%", "后端接口进入联调", "下周二上线评审"],
            "todos": ["张伟负责上线评审材料（下周二前）", "优化客户导出功能"],
        }
    content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": f"下面是视频的抽帧图片与语音转写文本，请据此生成结构化总结。关注点：{focus or '整体内容概述'}",
    }]
    for fr in frames:
        b64 = base64.b64encode(open(fr, "rb").read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": f"【语音转写】\n{transcript}"})
    content.append({"type": "text", "text":
        '请以JSON返回：{"summary":"一段话总结","highlights":["要点1","要点2"],"todos":["待办1","待办2"]}'})
    resp = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _generate(prompt: str, duration: int = 5, ratio: str = "16:9") -> Dict[str, Any]:
    if MOCK or not VIDEO_GEN_ENDPOINT:
        return {"video_url": "https://example.com/demo.mp4", "status": "mock"}
    r = requests.post(VIDEO_GEN_ENDPOINT,
                      json={"prompt": prompt, "duration": duration, "ratio": ratio},
                      headers={"Authorization": f"Bearer {API_KEY}"}, timeout=300)
    r.raise_for_status()
    return r.json()


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(request.query_params)


@app.get("/healthz")
def health():
    return {"ok": True, "mock": MOCK}


@app.post("/video/transcribe")
async def video_transcribe(request: Request):
    body = await _body(request)
    url = body.get("video_url") or body.get("videoUrl")
    if not url:
        raise HTTPException(400, "缺少参数 video_url")
    video_path = _download(url)
    audio_path = _extract_audio(video_path)
    text = _transcribe(audio_path, body.get("language"))
    return {"text": text}


@app.post("/video/summarize")
async def video_summarize(request: Request):
    body = await _body(request)
    url = body.get("video_url") or body.get("videoUrl")
    if not url:
        raise HTTPException(400, "缺少参数 video_url")
    video_path = _download(url)
    audio_path = _extract_audio(video_path)
    transcript = _transcribe(audio_path, body.get("language"))
    frames = _extract_frames(video_path, 5)
    return _understand(frames, transcript, body.get("focus"))


@app.post("/video/generate")
async def video_generate(request: Request):
    body = await _body(request)
    prompt = body.get("prompt") or body.get("video_prompt")
    if not prompt:
        raise HTTPException(400, "缺少参数 prompt")
    return _generate(prompt, int(body.get("duration", 5)), body.get("ratio", "16:9"))


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>视频插件测试</title>
<style>body{font-family:system-ui;max-width:780px;margin:40px auto;padding:0 16px}
input{width:100%;padding:9px;border:1px solid #d0d0d5;border-radius:8px;box-sizing:border-box}
button{margin:14px 10px 6px 0;padding:9px 18px;border:0;border-radius:8px;background:#07c160;color:#fff;cursor:pointer}
pre{background:#f4f4f5;padding:14px;border-radius:10px;white-space:pre-wrap}</style>
</head><body>
<h2>视频插件测试</h2>
<input id="url" placeholder="视频URL">
<div><button onclick="run('transcribe')">转写</button><button onclick="run('summarize')">理解</button></div>
<pre id="out">等待...</pre>
<script>async function run(t){const u=document.getElementById('url').value.trim();if(!u){alert('请填视频URL');return;}const o=document.getElementById('out');o.textContent='请求中...';try{const r=await fetch('/video/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:u})});o.textContent=JSON.stringify(await r.json(),null,2);}catch(e){o.textContent='错误:'+e;}}</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
