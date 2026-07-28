"""
Agent 评估脚本 v2 — 修复轮次截断问题
"""
import time
import json
import requests
from test_queries import TEST_QUERIES

BASE_URL = "http://localhost:8001"
USER_ID = "eval_v2"
POLL_INTERVAL = 1
MAX_WAIT = 180
MAX_TOOL_CALLS = 12


def submit_query(query):
    resp = requests.post(f"{BASE_URL}/agent/invoke", json={
        "user_id": USER_ID,
        "session_id": f"sess_{int(time.time())}",
        "task_id": f"task_{int(time.time()*1000)}",
        "query": query,
    }, timeout=30)
    return resp.json()


def get_status(sid, tid):
    return requests.get(f"{BASE_URL}/agent/status/{USER_ID}/{sid}/{tid}", timeout=10).json()


def resume(sid, tid):
    return requests.post(f"{BASE_URL}/agent/resume", json={
        "user_id": USER_ID, "session_id": sid, "task_id": tid,
        "response_type": "accept",
    }, timeout=10).json()


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
                r["status"] = "completed"; break
            elif s in ("error", "failed"):
                r["status"] = "failed"; break
            elif s == "interrupted":
                r["tool_calls"] += 1
                if r["tool_calls"] > MAX_TOOL_CALLS:
                    r["status"] = "tool_limit"; break
                resume(sid, tid)
            elif time.time() - t0 > MAX_WAIT:
                r["status"] = "timeout"; break

        r["elapsed"] = round(time.time() - t0, 1)
    except Exception as e:
        r["status"] = "exception"; r["error"] = str(e)[:200]
    return r


def main():
    print("Agent 评估 v2 — 20 条测试\n")
    all_r = []
    for cat, queries in TEST_QUERIES.items():
        print(f"── {cat} ──")
        for q in queries:
            print(f"  {q[:45]}...", end=" ", flush=True)
            r = run_one(q)
            all_r.append(r)
            icon = "OK" if r["status"] == "completed" else "XX"
            print(f"{icon} tools={r['tool_calls']}  {r['elapsed']}s")
            if r["error"]: print(f"    err: {r['error'][:100]}")

    total = len(all_r)
    ok = sum(1 for r in all_r if r["status"] == "completed")
    print(f"\n===== 汇总: {ok}/{total} ({ok/total*100:.0f}%) =====")
    for cat in TEST_QUERIES:
        qs = TEST_QUERIES[cat]
        cr = sum(1 for r in all_r if r["query"] in qs and r["status"] == "completed")
        print(f"  {cat}: {cr}/{len(qs)}")

    with open("tests/eval_results.json", "w", encoding="utf-8") as f:
        json.dump(all_r, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
