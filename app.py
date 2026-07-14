import os
import random
import requests
from flask import Flask, request, render_template, jsonify, Response, stream_with_context

app = Flask(__name__)

DEFAULT_INSTANCES = [
    "https://invidious.privacydev.net",
    "https://y.com.sb",
    "https://inv.in.projectsegfau.lt",
    "https://invidious.nerdvpn.de"
]

def get_instance():
    env_url = os.environ.get("INVIDIOUS_INSTANCE")
    if env_url:
        return env_url.rstrip("/")
    return random.choice(DEFAULT_INSTANCES)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/search")
def search():
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "検索ワードを入力してください"}), 400

    limit = int(request.args.get("limit", 20))
    instances_to_try = DEFAULT_INSTANCES.copy()
    env_instance = os.environ.get("INVIDIOUS_INSTANCE")
    if env_instance:
        instances_to_try = [env_instance] + [i for i in instances_to_try if i != env_instance]
    
    for instance in instances_to_try:
        try:
            resp = requests.get(
                f"{instance}/api/v1/search",
                params={"q": query, "type": "video", "limit": min(limit, 20)},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            results = [{
                "title": item.get("title", "タイトルなし"),
                "author": item.get("author", "不明"),
                "videoId": item.get("videoId", ""),
                "lengthSeconds": item.get("lengthSeconds", 0),
                "viewCount": item.get("viewCount", 0)
            } for item in data]
            return jsonify(results)
        except Exception as e:
            continue
    return jsonify({"error": "全てのインスタンスが失敗しました"}), 500

@app.route("/stream/<video_id>")
def stream_video(video_id):
    """動画ストリーミング（デバッグ強化版）"""
    instance = get_instance()
    
    try:
        # 1. 動画情報取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(info_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 2. ストリーム一覧取得（formatStreams優先）
        streams = video_info.get("formatStreams", [])
        if not streams:
            streams = video_info.get("adaptiveFormats", [])
        
        if not streams:
            return jsonify({"error": "ストリームなし"}), 404
        
        # 3. 最適ストリーム選択（音声＋動画が確実なもの）
        selected = None
        for s in streams:
            # video/mp4 または video/webm で、かつ音声を含むもの優先
            if "video/mp4" in s.get("type", "") or "video/webm" in s.get("type", ""):
                if "audio" in s.get("type", "") or "formatStreams" in str(streams):
                    selected = s
                    break
        # 見つからなければ最初のvideo
        if not selected:
            for s in streams:
                if "video" in s.get("type", ""):
                    selected = s
                    break
        
        if not selected:
            return jsonify({"error": "再生可能なストリームなし"}), 404
        
        # 4. URL取得（絶対パスに変換）
        stream_url = selected.get("url")
        if not stream_url:
            return jsonify({"error": "URL取得失敗"}), 404
        
        if stream_url.startswith("/"):
            stream_url = instance + stream_url
        
        # ★ デバッグ情報をレスポンスヘッダに追加（画面上で見えないけどログには出る）
        print(f"[DEBUG] video_id: {video_id}")
        print(f"[DEBUG] stream_url: {stream_url}")
        print(f"[DEBUG] content_type: {selected.get('type', 'unknown')}")
        
        # 5. ストリーミング転送
        def generate():
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Range": "bytes=0-"
                }
                with requests.get(stream_url, stream=True, timeout=30, headers=headers) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
            except Exception as e:
                # エラーが起きたら空じゃなくてエラーメッセージを返す
                error_msg = f"ストリーミングエラー: {str(e)}"
                print(error_msg)
                yield b""
        
        content_type = selected.get("type", "video/mp4")
        if ";" in content_type:
            content_type = content_type.split(";")[0]
        
        return Response(
            stream_with_context(generate()),
            content_type=content_type,
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": f'inline; filename="{video_id}.mp4"'
            }
        )
        
    except Exception as e:
        error_msg = f"動画取得失敗: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
