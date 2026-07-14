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
    """
    動画ストリーミング（事前確認付き・最終版）
    """
    instance = get_instance()
    
    try:
        # 1. 動画情報取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(info_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 2. 全ストリーム
        streams = video_info.get("formatStreams", []) + video_info.get("adaptiveFormats", [])
        if not streams:
            return jsonify({"error": "ストリームがありません"}), 404
        
        # 3. itag指定で選択（22=720p, 18=360p, 36=240p）
        selected_stream = None
        for itag in [22, 18, 36]:
            for s in streams:
                if s.get("itag") == itag:
                    selected_stream = s
                    break
            if selected_stream:
                break
        if not selected_stream:
            for s in streams:
                if "video/mp4" in s.get("type", ""):
                    selected_stream = s
                    break
        if not selected_stream:
            return jsonify({"error": "再生可能なストリームがありません"}), 404
        
        # 4. URL取得（絶対パス変換）
        stream_url = selected_stream.get("url")
        if not stream_url:
            return jsonify({"error": "URLが取得できません"}), 404
        if stream_url.startswith("/"):
            stream_url = instance + stream_url
        
        # ★ 5. 事前確認：HEADリクエストでURLが有効かチェック
        try:
            head_resp = requests.head(stream_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if head_resp.status_code not in [200, 206]:
                return jsonify({
                    "error": f"ストリームURLが無効です (HTTP {head_resp.status_code})",
                    "url": stream_url
                }), 404
        except Exception as e:
            return jsonify({
                "error": f"ストリームURLへの接続に失敗しました: {str(e)}",
                "url": stream_url
            }), 500
        
        # 6. デバッグログ（Renderのログに出力）
        print(f"🎬 動画ID: {video_id}")
        print(f"🔗 ストリームURL: {stream_url}")
        print(f"📁 itag: {selected_stream.get('itag')}")
        
        # 7. ストリーミング転送
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
                print(f"ストリーミング例外: {str(e)}")
                yield b""
        
        content_type = selected_stream.get("type", "video/mp4")
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
        return jsonify({"error": f"動画取得エラー: {str(e)}"}), 500
