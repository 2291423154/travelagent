"""TravelAgent CI 评估脚本 — 20 条测试，输出 JSON + 退出码

用法：
  python -m tests.eval_ci                    # 本地运行
  python -m tests.eval_ci --json-only         # 只输出 JSON（CI 用）
  BASE_URL=http://localhost:8001 python -m tests.eval_ci

退出码：0=全部通过，1=有失败
"""
import time, json, sys, os, argparse
import requests
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_queries import TEST_QUERIES

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")
USER_ID = f"eval_ci_{int(time.time())}"
POLL_INTERVAL = 2
MAX_WAIT = 300
MAX_TOOL_CALLS = 12
HTTP_TIMEOUT = 120

# CI 精简测试集：每类取 2 条代表，共 8 条（全量 20 条耗时太长）
CI_TEST_QUERIES = {
    "纯RAG": [
        "南京有什么特色小吃推荐？",
        "冬天去哈尔滨应该准备什么？",
    ],
    "纯MCP": [
        "帮我查一下南京新街口附近有什么商场",
        "帮我查一下杭州西湖区现在的天气",
    ],
    "混合RAG+MCP": [
        "推荐南京好吃的餐厅，并告诉我从南京站怎么过去",
        "西安兵马俑值得去吗？从西安市区怎么过去？",
    ],
    "边缘Case": [
        "火星上有什么好吃的餐厅？",
        "这个周末有什么活动？（缺少具体城市）",
    ],
}


def submit_query(query):
    resp = requests.post(f"{BASE_URL}/agent/invoke", json={
        "user_id": USER_ID,
        "session_id": f"sess_{int(time.time())}",
        "task_id": f"task_{int(time.time()*1000)}",
        "query": query,
    }, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_status(sid, tid):
    resp = requests.get(
        f"{BASE_URL}/agent/status/{USER_ID}/{sid}/{tid}",
        timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def resume(sid, tid):
    resp = requests.post(f"{BASE_URL}/agent/resume", json={
        "user_id": USER_ID, "session_id": sid, "task_id": tid,
        "response_type": "accept",
    }, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def run_one(query):
    r = {"query": query, "status": "unknown", "tool_calls": 0, "elapsed": 0, "error": None}
    try:
        inv = submit_query(query)
        sid, tid = inv["session_id"], inv["task_id"]
        t0 = time.time()

        while True:
            time.sleep(POLL_INTERVAL)
            s = get_status(sid, tid).get("status", "?")

            if s == "completed":
                r["status"] = "completed"
                break
            elif s in ("error", "failed"):
                r["status"] = "failed"
                break
            elif s == "interrupted":
                r["tool_calls"] += 1
                if r["tool_calls"] > MAX_TOOL_CALLS:
                    r["status"] = "tool_limit"
                    break
                resume(sid, tid)
            elif time.time() - t0 > MAX_WAIT:
                r["status"] = "timeout"
                break

        r["elapsed"] = round(time.time() - t0, 1)
    except Exception as e:
        r["status"] = "exception"
        r["error"] = str(e)[:200]
    return r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-only", action="store_true", help="只输出 JSON，不打印进度")
    parser.add_argument("--full", action="store_true", help="跑全量 20 条（默认跑 8 条精简集）")
    args = parser.parse_args()

    queries_dict = TEST_QUERIES if args.full else CI_TEST_QUERIES
    all_r = []

    for cat, queries in queries_dict.items():
        if not args.json_only:
            print(f"\n── {cat} ──")
        for q in queries:
            if not args.json_only:
                print(f"  {q[:50]}...", end=" ", flush=True)
            r = run_one(q)
            all_r.append(r)
            icon = "OK" if r["status"] == "completed" else "XX"
            if not args.json_only:
                print(f"{icon} tools={r['tool_calls']} {r['elapsed']}s")
                if r["error"]:
                    print(f"    err: {r['error'][:120]}")

    total = len(all_r)
    ok = sum(1 for r in all_r if r["status"] == "completed")
    rate = ok / total * 100 if total > 0 else 0

    # ── JSON 报告 ──
    report = {
        "total": total,
        "passed": ok,
        "failed": total - ok,
        "rate": f"{rate:.0f}%",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "breakdown": {}
    }
    for cat in queries_dict:
        qs = queries_dict[cat]
        cr = sum(1 for r in all_r if r["query"] in qs and r["status"] == "completed")
        report["breakdown"][cat] = f"{cr}/{len(qs)}"

    report["details"] = [
        {"query": r["query"], "status": r["status"], "tool_calls": r["tool_calls"], "elapsed": r["elapsed"]}
        for r in all_r
    ]

    # 确保目录存在
    os.makedirs("tests", exist_ok=True)
    with open("tests/eval_ci_result.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ── 汇总输出 ──
    print(f"\n{'='*60}")
    print(f"CI 评估汇总: {ok}/{total} ({rate:.0f}%)")
    for cat, score in report["breakdown"].items():
        print(f"  {cat}: {score}")
    print(f"{'='*60}")

    # ── 退出码 ──
    if ok == total:
        sys.exit(0)   # 全部通过
    else:
        sys.exit(1)   # 有失败，CI 会拦截


if __name__ == "__main__":
    main()
