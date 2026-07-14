import os
import random
import requests
import traceback
from flask import Flask, request, render_template, jsonify, Response, stream_with_context

app = Flask(__name__)

# 安定版インスタンスリスト
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

# ★★★ デバッグ用：最新コードが動いているか確認するエンドポイント ★★★
@app.route("/test")
def test():
    return "✅ 最新コードが動いています！ (2026-07-14 最終版)"

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
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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
            print(f"検索インスタンス {instance} 失敗: {e}")
            continue
    
    return jsonify({"error": "全てのインスタンスが失敗しました"}), 500

@app.route("/stream/<video_id>")
def stream_video(video_id):
    """
    動画ストリーミング（最終デバッグ版）
    """
    # ★ ここでログに必ず表示されるメッセージを出力 ★
    print("=" * 60)
    print(f"🚀🚀🚀 /stream が呼ばれました！ 動画ID: {video_id} 🚀🚀🚀")
    print("=" * 60)
    
    instance = get_instance()
    
    try:
        # 動画情報を取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(
            info_url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 全ストリームを結合
        streams = video_info.get("formatStreams", []) + video_info.get("adaptiveFormats", [])
        print(f"📊 ストリーム数: {len(streams)}")
        
        if not streams:
            return jsonify({"error": "ストリームがありません"}), 404
        
        # itag指定で選択（22→18→34→35→36→17）
        selected_stream = None
        for itag in [22, 18, 34, 35, 36, 17]:
            for s in streams:
                if s.get("itag") == itag:
                    selected_stream = s
                    print(f"✅ itag {itag} を選択")
                    break
            if selected_stream:
                break
        
        # フォールバック1: video/mp4
        if not selected_stream:
            for s in streams:
                if "video/mp4" in s.get("type", ""):
                    selected_stream = s
                    print(f"✅ video/mp4 を選択 (itag: {s.get('itag')})")
                    break
        
        # フォールバック2: 最初のvideo
        if not selected_stream:
            for s in streams:
                if "video" in s.get("type", ""):
                    selected_stream = s
                    print(f"✅ 最初のvideoを選択 (itag: {s.get('itag')})")
                    break
        
        if not selected_stream:
            print("❌ ストリーム選択失敗")
            return jsonify({"error": "再生可能なストリームがありません"}), 404
        
        # URL取得
        stream_url = selected_stream.get("url")
        if not stream_url:
            return jsonify({"error": "URLが取得できません"}), 404
        
        if stream_url.startswith("/"):
            stream_url = instance.rstrip("/") + stream_url
        
        print(f"🔗 ストリームURL: {stream_url}")
        
        # HEADリクエストで事前確認
        try:
            head_resp = requests.head(stream_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if head_resp.status_code not in [200, 206]:
                print(f"❌ HEAD失敗: HTTP {head_resp.status_code}")
                return jsonify({"error": f"ストリームURLが無効 (HTTP {head_resp.status_code})"}), 404
            print(f"✅ HEAD成功: HTTP {head_resp.status_code}")
        except Exception as e:
            print(f"❌ HEAD例外: {e}")
            return jsonify({"error": f"接続失敗: {str(e)}"}), 500
        
        # ストリーミング転送
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
                print(f"❌ ストリーミング例外: {e}")
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
        print("=" * 60)
        print("❌ 予期せぬエラー発生")
        traceback.print_exc()
        print("=" * 60)
        return jsonify({"error": f"動画取得エラー: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
