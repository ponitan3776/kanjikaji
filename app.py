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
    動画ストリーミング（完全版・エラー処理徹底）
    """
    instance = get_instance()
    
    try:
        print(f"🎬 動画ID: {video_id}")
        print(f"🌐 使用インスタンス: {instance}")
        
        # 1. 動画情報を取得
        info_url = f"{instance}/api/v1/videos/{video_id}"
        info_resp = requests.get(
            info_url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        info_resp.raise_for_status()
        video_info = info_resp.json()
        
        # 2. 全ストリームを結合（formatStreams + adaptiveFormats）
        streams = video_info.get("formatStreams", []) + video_info.get("adaptiveFormats", [])
        print(f"📊 取得したストリーム数: {len(streams)}")
        
        if not streams:
            print("❌ ストリームが0件")
            return jsonify({"error": "ストリームがありません"}), 404
        
        # 3. ストリームを選択（itag指定）
        selected_stream = None
        
        # itag優先順位: 22(720p) → 18(360p) → 34(360p) → 35(480p) → 36(240p) → 17(144p)
        target_itags = [22, 18, 34, 35, 36, 17]
        
        for itag in target_itags:
            for s in streams:
                if s.get("itag") == itag:
                    selected_stream = s
                    print(f"✅ itag {itag} のストリームを選択")
                    break
            if selected_stream:
                break
        
        # 4. itagで見つからなければ video/mp4 を探す（フォールバック）
        if not selected_stream:
            print("⚠️ itag指定で見つからなかったので video/mp4 を探します")
            for s in streams:
                if "video/mp4" in s.get("type", ""):
                    selected_stream = s
                    print(f"✅ video/mp4 ストリームを選択 (itag: {s.get('itag')})")
                    break
        
        # 5. それでもなければ video/ を含む最初のものを探す
        if not selected_stream:
            print("⚠️ video/mp4もないので最初のvideoストリームを探します")
            for s in streams:
                if "video" in s.get("type", ""):
                    selected_stream = s
                    print(f"✅ 最初のvideoストリームを選択 (itag: {s.get('itag')})")
                    break
        
        # 6. それでもなければ失敗
        if not selected_stream:
            print("❌ どのストリームも選択できませんでした")
            return jsonify({"error": "再生可能なストリームがありません"}), 404
        
        # 7. ストリームURLを取得
        stream_url = selected_stream.get("url")
        if not stream_url:
            print("❌ URLが取得できません")
            return jsonify({"error": "URLが取得できません"}), 404
        
        # 相対パスを絶対パスに変換
        if stream_url.startswith("/"):
            stream_url = instance.rstrip("/") + stream_url
        print(f"🔗 最終的なストリームURL: {stream_url}")
        
        # 8. 事前確認（HEADリクエストでURLが有効かチェック）
        try:
            head_resp = requests.head(
                stream_url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            if head_resp.status_code not in [200, 206]:
                print(f"❌ HEADリクエスト失敗: HTTP {head_resp.status_code}")
                return jsonify({
                    "error": f"ストリームURLが無効 (HTTP {head_resp.status_code})",
                    "url": stream_url
                }), 404
            else:
                print(f"✅ HEADリクエスト成功: HTTP {head_resp.status_code}")
        except Exception as e:
            print(f"❌ HEADリクエスト例外: {str(e)}")
            return jsonify({
                "error": f"ストリームURLへの接続に失敗: {str(e)}",
                "url": stream_url
            }), 500
        
        # 9. ストリーミング転送
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
                print(f"❌ ストリーミング例外: {str(e)}")
                yield b""
        
        # コンテンツタイプを設定
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
        # 予期せぬエラーはスタックトレースをログに出力
        print("=" * 60)
        print("❌ stream_video で予期せぬエラーが発生しました")
        traceback.print_exc()
        print("=" * 60)
        return jsonify({"error": f"動画取得エラー: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
