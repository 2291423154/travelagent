"""
混合任务专项测评 — 对比 Workflow 层前后的工具调用步数
"""
import time, json, requests

BASE_URL = "http://localhost:8001"
USER_ID = "eval_workflow_v2"  # 新 user 避免旧 session 干扰
POLL_INTERVAL = 2  # HITL 轮询间隔，松一点避免压垮后端
MAX_WAIT = 300     # 混合查询步数多，给足够时间
MAX_TOOL_CALLS = 15
HTTP_TIMEOUT = 60  # 后端可能在处理 HITL，需要更长超时

MIXED_QUERIES = [
    "推荐南京好吃的餐厅，并告诉我从南京站怎么过去",
    "苏州拙政园附近有什么酒店？拙政园有什么历史背景？",
    "我想去杭州西湖玩，推荐一条路线，并查一下西湖附近有什么餐厅",
    "成都大熊猫基地怎么去？看熊猫有什么注意事项？",
    "西安兵马俑值得去吗？从西安市区怎么过去？",
]


def submit(query):
    r = requests.post(f"{BASE_URL}/agent/invoke", json={
        "user_id": USER_ID,
        "session_id": f"sess_{int(time.time())}",
        "task_id": f"task_{int(time.time()*1000)}",
        "query": query,
    }, timeout=HTTP_TIMEOUT)
    return r.json()


def get_status(sid, tid):
    return requests.get(f"{BASE_URL}/agent/status/{USER_ID}/{sid}/{tid}", timeout=HTTP_TIMEOUT).json()


def resume(sid, tid):
    return requests.post(f"{BASE_URL}/agent/resume", json={
        "user_id": USER_ID, "session_id": sid, "task_id": tid,
        "response_type": "accept",
    }, timeout=HTTP_TIMEOUT).json()


def run_one(query):
    r = {"query": query, "status": "unknown", "tool_calls": 0, "elapsed": 0, "error": None}
    try:
        inv = submit(query)
        sid, tid = inv["session_id"], inv["task_id"]
        t0 = time.time()

        while True:
            time.sleep(POLL_INTERVAL)
            s = get_status(sid, tid)
            status = s.get("status", "?")

            if status == "completed":
                r["status"] = "completed"; break
            elif status in ("error", "failed"):
                r["status"] = "failed"; break
            elif status == "interrupted":
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
    print("=" * 60)
    print("混合任务 Workflow 层测评")
    print("=" * 60)
    results = []
    for q in MIXED_QUERIES:
        print(f"\n▶ {q[:55]}...", flush=True)
        r = run_one(q)
        results.append(r)
        icon = "PASS" if r["status"] == "completed" else "FAIL"
        print(f"  {icon} | tools={r['tool_calls']} | {r['elapsed']}s")
        if r["error"]:
            print(f"  err: {r['error'][:120]}")

    passed = sum(1 for r in results if r["status"] == "completed")
    avg_tools = sum(r["tool_calls"] for r in results) / len(results) if results else 0
    avg_time = sum(r["elapsed"] for r in results) / len(results) if results else 0

    print(f"\n{'='*60}")
    print(f"通过: {passed}/{len(results)} ({passed/len(results)*100:.0f}%)")
    print(f"平均工具调用: {avg_tools:.1f} 步")
    print(f"平均耗时: {avg_time:.1f}s")
    print(f"{'='*60}")

    with open("tests/mixed_eval_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("结果已保存到 tests/mixed_eval_result.json")


if __name__ == "__main__":
    main()
