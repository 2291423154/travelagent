"""从中文维基百科批量拉取热门旅游城市攻略，存为 txt 供 RAG 索引"""
import requests, time, os, re

API = "https://zh.wikipedia.org/w/api.php"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
os.makedirs(OUT_DIR, exist_ok=True)

CITIES = [
    "杭州", "西安", "成都", "北京", "上海", "重庆", "广州", "深圳",
    "武汉", "长沙", "厦门", "三亚", "昆明", "大理", "丽江",
    "桂林", "黄山", "拉萨", "哈尔滨", "青岛", "张家界", "敦煌",
    "九寨沟", "苏州", "南京", "洛阳", "开封", "呼和浩特",
]

HEADERS = {"User-Agent": "TravelAgent-RAG/1.0 (chinese-travel-knowledge-base)"}


def fetch_page(title: str) -> str | None:
    """拉取单个页面的纯文本内容"""
    params = {
        "action": "query", "titles": title, "prop": "extracts",
        "exlimit": 1, "explaintext": 1, "format": "json", "formatversion": 2,
    }
    try:
        resp = requests.get(API, params=params, headers=HEADERS, timeout=30)
        data = resp.json()
        pages = data.get("query", {}).get("pages", [])
        if not pages or "missing" in pages[0]:
            print(f"  [SKIP] {title} — 页面不存在或已被删除")
            return None
        text = pages[0].get("extract", "")
        if len(text) < 200:
            print(f"  [SKIP] {title} — 内容过短 ({len(text)} 字)")
            return None
        return text
    except Exception as e:
        print(f"  [ERR] {title}: {e}")
        return None


def clean_text(text: str) -> str:
    """清理 Wikipedia 格式噪音：去除参考文献标记、多余空行、短行"""
    text = re.sub(r'\[\d+\]', '', text)           # 去掉 [1][2] 引用标记
    text = re.sub(r'\n{3,}', '\n\n', text)         # 压缩多余空行
    text = re.sub(r'={2,}', '', text)             # 去掉 == 标题标记 ==
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 20]
    return '\n\n'.join(lines)


def main():
    print(f"输出目录: {OUT_DIR}")
    print(f"共 {len(CITIES)} 个城市\n")

    success, skip = 0, 0
    for city in CITIES:
        print(f"拉取: {city}...", end=" ", flush=True)
        text = fetch_page(city)
        time.sleep(0.5)  # 避免被 Wikipedia 限流

        if text is None:
            skip += 1
            continue

        text = clean_text(text)
        filename = f"wiki_{city}.txt"
        filepath = os.path.join(OUT_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"=== {city}旅游攻略（来源：维基百科） ===\n\n{text}")

        print(f"OK ({len(text)} 字)")
        success += 1

    print(f"\n完成: {success} 个成功, {skip} 个跳过")
    print(f"文件已写入 {OUT_DIR}/")


if __name__ == "__main__":
    main()
