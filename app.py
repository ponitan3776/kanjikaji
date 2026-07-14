import os
import random
import requests
from flask import Flask, request, render_template, jsonify, Response, stream_with_context

app = Flask(__name__)

# 安定版インスタンスリスト
DEFAULT_INSTANCES = [
    "https://invidious.privacydev.net",
    "https://y.com.sb",
    "https://inv.in.projectsegfau.lt",
    "https://invidious.nerdvpn.de",
    "https://iv.ggtyler.dev"
]

def get_instance():
    """環境変数があればそれを使う。なければランダム選択"""
    env_url = os.environ.get("INVIDIOUS_INSTANCE")
    if env_url:
        return env_url.rstrip("/")
    return random.choice(DEFAULT_INSTANCES)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/search")
def search():
    """検索エンドポイント（複数インスタンス自動切替）"""
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "検索ワードを入力してください"}), 400

    limit = int(request.args.get("limit", 20))
    
    # 環境変数があれば優先、なければリストを順に試す
    env_instance = os.environ.get("INVIDIOUS_INSTANCE")
    instances_to_try = [env_instance] if env_instance else DEFAULT_INSTANCES.copy()
    
    if env_instance and env_instance in DEFAULT_INSTANCES:
        instances_to_try = [env_instance] + [i for i in DEFAULT_INSTANCES if i != env_instance]
    elif not env_instance:
        instances_to_try = DEFAULT_INSTANCES.copy()
    
    last_error = None
    for instance in instances_to_try:
        try:
            url = f"{instance}/api/v1/search"
            params = {
                "q": query,
                "type": "video",
                "limit": min(limit, 20)
            }
            
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            results = []
            for item in data:
                results.append({
                    "title": item.get("title", "タイトルなし"),
                    "author": item.get("author", "不明なチャンネル"),
                    "videoId": item.get("videoId", ""),
                    "channelId": item.get("channelId", ""),
                    "lengthSeconds": item.get("lengthSeconds", 0),
                    "published": item.get("publishedText", ""),
                    "viewCount": item.get("viewCount", 0)
                })
            return jsonify(results)
            
        except Exception as e:
            last_error = str(e)
            continue
    
    return jsonify({"error": f"全てのインスタンスが失敗しました: {last_error}"}), 500

@app.route("/stream/<video_id>")
def stream_video(video_id):
    """
    動画ストリーミング（完全版）
    - 高さ制限なし
    - 相対URL対応
    - User-Agent偽装
    - エラーハンドリング強化
    """
    instance = get_instance()
    
    try:
        # 1. 動画情報取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(info_url, timeout=10)
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 2. 全ストリームを結合
        streams = video_info.get("formatStreams", []) + video_info.get("adaptiveFormats", [])
        
        if not streams:
            return jsonify({"error": "ストリームが見つかりません"}), 404
        
        # 3. 最適なストリームを選択（高さ制限なし！）
        selected_stream = None
        
        # 優先1: video/mp4 または video/webm
        for stream in streams:
            stream_type = stream.get("type", "")
            if stream_type.startswith("video/mp4") or stream_type.startswith("video/webm"):
                selected_stream = stream
                break
        
        # 優先2: 音声付き動画（formatStreamsに多い）
        if not selected_stream:
            for stream in streams:
                if "video" in stream.get("type", "") and "audio" in stream.get("type", ""):
                    selected_stream = stream
                    break
        
        # 優先3: とにかくvideoが含まれるもの
        if not selected_stream:
            for stream in streams:
                if "video" in stream.get("type", ""):
                    selected_stream = stream
                    break
        
        if not selected_stream:
            return jsonify({"error": "再生可能なストリームが見つかりません"}), 404
        
        # 4. ストリームURL取得（相対パス対応）
        stream_url = selected_stream.get("url")
        if not stream_url:
            return jsonify({"error": "ストリームURLが取得できません"}), 404
        
        # 相対パス（/から始まる）ならベースURLと結合
        if stream_url.startswith("/"):
            stream_url = instance + stream_url
        
        # 5. User-Agent偽装（ブロック回避）
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # 6. ストリーミング転送
        def generate():
            try:
                with requests.get(stream_url, stream=True, timeout=30, headers=headers) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
            except Exception as e:
                print(f"ストリーミングエラー: {e}")
                yield b""
        
        # コンテンツタイプ設定
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
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "動画取得がタイムアウトしました"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"動画取得失敗: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"予期せぬエラー: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
