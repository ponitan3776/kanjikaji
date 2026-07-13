import os
import random
import requests
from flask import Flask, request, render_template, jsonify

app = Flask(__name__)

# パブリックインスタンスのリスト
DEFAULT_INSTANCES = [
    "https://inv.us.projectsegfau.lt",
    "https://y.com.sb",
    "https://invidious.snopyta.org",
    "https://iv.ggtyler.dev"
]

def get_instance():
    env_url = os.environ.get("INVIDIOUS_INSTANCE")
    if env_url:
        return env_url.rstrip("/")
    return random.choice(DEFAULT_INSTANCES)

@app.route("/")
def home():
    # index.html を表示（検索フォーム + 結果表示エリア）
    return render_template("index.html")

@app.route("/search")
def search():
    # 検索キーワードを取得
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
            # チャンネル情報も含める
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

    except Exception as e:
        return jsonify({"error": f"取得失敗: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
