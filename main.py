import os,glob,base64,json,tempfile,subprocess
from typing import Optional,List,Dict,Any
import requests
from dotenv import load_dotenv
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse

load_dotenv()
MOCK=os.getenv("VIDEO_PLUGIN_MOCK","false").lower()=="true"
API_KEY=os.getenv("OPENAI_API_KEY","")
BASE_URL=os.getenv("OPENAI_BASE_URL","https://api.openai.com/v1")
TRANSCRIBE_MODEL=os.getenv("TRANSCRIBE_MODEL","whisper-1")
VISION_MODEL=os.getenv("VISION_MODEL","gpt-4o")
VIDEO_GEN_ENDPOINT=os.getenv("VIDEO_GEN_ENDPOINT","")
FFMPEG=os.getenv("FFMPEG_BIN","ffmpeg")
client=None
if API_KEY and not MOCK:
 from openai import OpenAI
 client=OpenAI(api_key=API_KEY,base_url=BASE_URL)

app=FastAPI(title="WeCom Video Plugin")

def _download(url,s=".mp4"):
 r=requests.get(url,timeout=180);r.raise_for_status()
 f=tempfile.NamedTemporaryFile(delete=False,suffix=s);f.write(r.content);f.close();return f.name

def _extract_audio(v):
 a=v+".wav";subprocess.run([FFMPEG,"-y","-i",v,"-vn","-ac","1","-ar","16000","-f","wav",a],check=True,capture_output=True);return a

def _extract_frames(v,n=5):
 d=tempfile.mkdtemp();subprocess.run([FFMPEG,"-y","-i",v,"-vf","fps=1/4","-frames:v",str(n),os.path.join(d,"frame_%03d.jpg")],check=True,capture_output=True);return sorted(glob.glob(os.path.join(d,"*.jpg")))[:n]

def _transcribe(a,l=None):
 if MOCK or client is None:return "示例转写：本周项目进展顺利，前端已完成约80%，后端接口进入联调；下周二前需完成上线评审，由张伟负责。"
 with open(a,"rb") as f:
  k={"model":TRANSCRIBE_MODEL,"file":f}
  if l:k["language"]=l
  return client.audio.transcriptions.create(**k).text

def _understand(frames,t,f=None):
 if MOCK or client is None:return {"summary":"示例摘要：项目周会录像，同步研发进度并明确上线评审事项。","highlights":["前端约80%","后端联调中","下周二上线评审"],"todos":["张伟准备评审材料","优化导出功能"]}
 c=[{"type":"text","text":f"视频抽帧+转写文本，生成结构化总结。关注点：{f or '整体概述'}"}]
 for fr in frames:
  b64=base64.b64encode(open(fr,"rb").read()).decode();c.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}})
 c.append({"type":"text","text":f"【转写】\n{t}"})
 c.append({"type":"text","text":'返回JSON：{"summary":"总结","highlights":["要点"],"todos":["待办"]}'})
 r=client.chat.completions.create(model=VISION_MODEL,messages=[{"role":"user","content":c}],response_format={"type":"json_object"})
 return json.loads(r.choices[0].message.content)

def _generate(p,d=5,r="16:9"):
 if MOCK or not VIDEO_GEN_ENDPOINT:return {"video_url":"https://example.com/demo.mp4","status":"mock"}
 r=requests.post(VIDEO_GEN_ENDPOINT,json={"prompt":p,"duration":d,"ratio":r},headers={"Authorization":f"Bearer {API_KEY}"},timeout=300);r.raise_for_status();return r.json()

async def _body(req):
 try:d=await req.json();return d if isinstance(d,dict) else {}
 except:return dict(req.query_params)

@app.get("/healthz")
def health():return{"ok":True,"mock":MOCK}

@app.post("/video/transcribe")
async def vt(req):
 b=await _body(req);u=b.get("video_url")or b.get("videoUrl")
 if not u:raise HTTPException(400,"缺少video_url")
 return{"text":_transcribe(_extract_audio(_download(u)),b.get("language"))}

@app.post("/video/summarize")
async def vs(req):
 b=await _body(req);u=b.get("video_url")or b.get("videoUrl")
 if not u:raise HTTPException(400,"缺少video_url")
 v=_download(u);a=_extract_audio(v);t=_transcribe(a,b.get("language"));f=_extract_frames(v,5)
 return _understand(f,t,b.get("focus"))

@app.post("/video/generate")
async def vg(req):
 b=await _body(req);p=b.get("prompt")or b.get("video_prompt")
 if not p:raise HTTPException(400,"缺少prompt")
 return _generate(p,int(b.get("duration",5)),b.get("ratio","16:9"))

INDEX_HTML="""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>测试</title>
<style>body{font-family:system-ui;max-width:780px;margin:40px auto}input{width:100%;padding:9px;border:1px solid #d0d0d5;border-radius:8px}button{margin:14px 10px 6px 0;padding:9px 18px;border:0;border-radius:8px;background:#07c160;color:#fff;cursor:pointer}pre{background:#f4f4f5;padding:14px;border-radius:10px}</style></head><body>
<h2>视频插件测试</h2><input id="url" placeholder="视频URL">
<div><button onclick="go('transcribe')">转写</button><button onclick="go('summarize')">理解</button></div>
<pre id="out">等待...</pre>
<script>async function go(t){const u=document.getElementById('url').value.trim();if(!u){alert('填URL');return;}const o=document.getElementById('out');o.textContent='请求中...';try{o.textContent=JSON.stringify(await(await fetch('/video/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:u})}).json(),null,2)}catch(e){o.textContent='错误:'+e}}</script></body></html>"""

@app.get("/",response_class=HTMLResponse)
def index():return INDEX_HTML

if __name__=="__main__":
 import uvicorn;uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT",8000)))
