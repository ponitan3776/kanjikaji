import os
import random
import requests
from flask import Flask, request, render_template, jsonify, Response, stream_with_context

app = Flask(__name__)

# パブリックインスタンスのリスト（環境変数で上書き可能）
DEFAULT_INSTANCES = [
    "https://y.com.sb",
    "https://inv.us.projectsegfau.lt",
    "https://invidious.privacydev.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.snopyta.org",
    "https://iv.ggtyler.dev"
]

def get_instance():
    """環境変数かランダムでインスタンスURLを返す"""
    env_url = os.environ.get("INVIDIOUS_INSTANCE")
    if env_url:
        return env_url.rstrip("/")
    return random.choice(DEFAULT_INSTANCES)

@app.route("/")
def home():
    """トップページ（検索UI）"""
    return render_template("index.html")

@app.route("/search")
def search():
    """検索エンドポイント： ?q=キーワード&limit=20"""
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "q パラメータが必要です"}), 400

    limit = int(request.args.get("limit", 20))
    instance = get_instance()
    url = f"{instance}/api/v1/search"

    params = {
        "q": query,
        "type": "video",
        "limit": min(limit, 20)
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
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
                "viewCount": item.get("viewCount", 0),
                "description": item.get("description", "")
            })
        return jsonify(results)

    except requests.exceptions.Timeout:
        return jsonify({"error": "インスタンスがタイムアウトしました"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"リクエスト失敗: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"予期せぬエラー: {str(e)}"}), 500

@app.route("/stream/<video_id>")
def stream_video(video_id):
    """
    動画ストリーミングエンドポイント
    Invidiousから動画データを取得してブラウザに転送する
    """
    instance = get_instance()
    
    try:
        # 1. 動画情報を取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(info_url, timeout=10)
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 2. ストリーム一覧を取得
        streams = video_info.get("formatStreams", [])
        if not streams:
            streams = video_info.get("adaptiveFormats", [])
        
        if not streams:
            return jsonify({"error": "ストリームが見つかりません"}), 404
        
        # 3. 最適なストリームを選ぶ（優先順位：720p > 480p > 360p > それ以外）
        selected_stream = None
        target_heights = [720, 480, 360]
        
        for height in target_heights:
            for stream in streams:
                stream_type = stream.get("type", "")
                if stream_type.startswith("video/mp4") or stream_type.startswith("video/webm"):
                    if stream.get("height") == height:
                        selected_stream = stream
                        break
            if selected_stream:
                break
        
        # 見つからなければ最初の動画ストリームを使う
        if not selected_stream:
            for stream in streams:
                if stream.get("type", "").startswith("video/"):
                    selected_stream = stream
                    break
        
        if not selected_stream:
            return jsonify({"error": "再生可能なストリームが見つかりません"}), 404
        
        # 4. ストリームURLを取得
        stream_url = selected_stream.get("url")
        if not stream_url:
            return jsonify({"error": "ストリームURLが取得できません"}), 404
        
        # 5. 動画データをストリーミングで転送
        def generate():
            try:
                with requests.get(stream_url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
            except Exception as e:
                print(f"ストリーミングエラー: {e}")
                yield b""
        
        # コンテンツタイプを設定
        content_type = selected_stream.get("type", "video/mp4")
        # typeに含まれるcodec情報を除去（ブラウザが正しく認識できるように）
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
