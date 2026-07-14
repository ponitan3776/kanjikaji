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
    動画ストリーミング（itag指定版・確実に動画を選ぶ）
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
        
        # 2. 全ストリームを結合
        streams = video_info.get("formatStreams", []) + video_info.get("adaptiveFormats", [])
        
        if not streams:
            return jsonify({"error": "ストリームがありません"}), 404
        
        # 3. ★ itag指定で確実に選ぶ（18=360p, 22=720p, 36=240p）
        selected_stream = None
        target_itags = [22, 18, 36]  # 高画質→低画質の順
        
        for itag in target_itags:
            for stream in streams:
                if stream.get("itag") == itag:
                    selected_stream = stream
                    break
            if selected_stream:
                break
        
        # 見つからなければ、video/mp4 を探す（フォールバック）
        if not selected_stream:
            for stream in streams:
                if "video/mp4" in stream.get("type", ""):
                    selected_stream = stream
                    break
        
        if not selected_stream:
            return jsonify({"error": "再生可能なストリームが見つかりません"}), 404
        
        # 4. ストリームURL取得
        stream_url = selected_stream.get("url")
        if not stream_url:
            return jsonify({"error": "URLが取得できません"}), 404
        
        # 相対パス変換
        if stream_url.startswith("/"):
            stream_url = instance + stream_url
        
        # ★ デバッグ用（Renderのログに出力）
        print(f"🎬 動画ID: {video_id}")
        print(f"🔗 ストリームURL: {stream_url}")
        print(f"📁 itag: {selected_stream.get('itag')}")
        print(f"📄 Content-Type: {selected_stream.get('type')}")
        
        # 5. ストリーミング転送（エラー時に空を返さないようにする）
        def generate():
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Range": "bytes=0-"
                }
                with requests.get(stream_url, stream=True, timeout=30, headers=headers) as r:
                    # ステータスコードが200か206でない場合はエラーとして扱う
                    if r.status_code not in [200, 206]:
                        error_msg = f"ストリーム取得失敗: HTTP {r.status_code}"
                        print(error_msg)
                        yield b""
                        return
                    
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
            except Exception as e:
                error_msg = f"ストリーミング例外: {str(e)}"
                print(error_msg)
                # エラーが起きたら空を返す（ブラウザ側でコード4になる）
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
        
    except Exception as e:
        error_msg = f"動画取得エラー: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
