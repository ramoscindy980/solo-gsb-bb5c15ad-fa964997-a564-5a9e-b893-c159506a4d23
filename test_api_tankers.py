"""应急送水分配预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、结果字段完整、录入校验 422、以及**只读**——
不修改当前管网、不写入历史方案、不改变原方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 双支路管网: 计划关闭 V1+V2 后普通节点 A、B 断水(需求点)
NET2 = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "A", "role": "normal"}, {"id": "B", "role": "normal"},
        {"id": "L1", "role": "leak"}, {"id": "L2", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "B"},
        {"id": "p3", "endpoint_a": "A", "endpoint_b": "L1"},
        {"id": "p4", "endpoint_a": "B", "endpoint_b": "L2"},
        {"id": "p5", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 2},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
DEMANDS = {"A": 5, "B": 3}
CANDS = [
    {"id": "T1", "dispatch_cost": 4, "capacity": 6, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 3, "capacity": 3, "serves": ["B"]},
]

# 健康检查
r = client.get("/api/health")
check(r.status_code == 200, "health")

# ---- 常规可行方案上的应急送水分配预览 ----
r = client.post("/api/plan", json=NET2)
check(r.status_code == 200, f"plan {r.status_code}")
base = r.json()
check(base["feasible"] and base["closed_valves"] == ["V1", "V2"], "plan saved")
pid = base["id"]
before = client.get(f"/api/plans/{pid}").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
net_before = client.get("/api/network").json()

r = client.post(f"/api/plans/{pid}/tankers",
                json={"demands": DEMANDS, "candidates": CANDS})
check(r.status_code == 200, f"tankers status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["plan_id"] == pid, "tankers meta")
check(res["solver"] == "base", "tankers solver field")
check(res["closed_valves"] == ["V1", "V2"], "tankers closed valves")
check(res["selected_ids"] == ["T1", "T2"], f"selected {res['selected_ids']}")
check(res["total_dispatch_cost"] == 7, "total cost")
check(res["total_demand"] == 8 and res["delivered_total"] == 8, "totals")
check(res["candidate_count"] == 2 and res["combination_count"] == 4, "counts")
check(res["feasible_combination_count"] == 1, "feasible combos")
check(res["demand_points"] == [
    {"node_id": "A", "label": "A", "demand": 5},
    {"node_id": "B", "label": "B", "demand": 3},
], f"demand points {res['demand_points']}")
check(res["selected_vehicles"] == [
    {"id": "T1", "dispatch_cost": 4, "capacity": 6, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 3, "capacity": 3, "serves": ["B"]},
], f"selected vehicles {res['selected_vehicles']}")
amap = {(d["vehicle_id"], d["node_id"]): d["amount"] for d in res["deliveries"]}
check(amap == {("T1", "A"): 5, ("T1", "B"): 1, ("T2", "B"): 2},
      f"deliveries {amap}")
check("只读" in res["message"] and "不修改" in res["message"], "readonly note")

# ---- 只读: 原方案、历史列表、当前管网均未改变 ----
check(client.get(f"/api/plans/{pid}").json() == before, "plan unchanged")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by tankers")
check(client.get("/api/network").json() == net_before, "network untouched")

# ---- 全部候选车仍无法满足: 最大可配送量与总缺口 ----
r = client.post(f"/api/plans/{pid}/tankers",
                json={"demands": {"A": 10, "B": 5}, "candidates": [
                    {"id": "T1", "dispatch_cost": 1, "capacity": 6,
                     "serves": ["A"]},
                    {"id": "T2", "dispatch_cost": 1, "capacity": 3,
                     "serves": ["B"]},
                ]})
res = r.json()
check(r.status_code == 200 and res["ok"] is False, "insufficient ok false")
check(res["reason_code"] == "INSUFFICIENT_DELIVERY_CAPACITY", "insufficient code")
check(res["max_deliverable"] == 9 and res["shortage"] == 6, "max/shortage")
check(res["per_point"] == [
    {"node_id": "A", "demand": 10, "delivered": 6, "shortage": 4},
    {"node_id": "B", "demand": 5, "delivered": 3, "shortage": 2},
], f"per_point {res['per_point']}")
check(sum(d["amount"] for d in res["deliveries"]) == 9, "deliveries sum")

# ---- 备援方案上的送水预览(普通节点 M、X 断水) ----
NET_FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "X", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p4", "endpoint_a": "M", "endpoint_b": "X"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
r = client.post("/api/plan-failover", json=NET_FO)
fo = r.json()
check(fo["feasible"] and fo["closed_valves"] == ["V1", "V2"], "failover saved")
fid = fo["id"]
r = client.post(f"/api/plans/{fid}/tankers",
                json={"demands": {"M": 4, "X": 2}, "candidates": [
                    {"id": "T1", "dispatch_cost": 5, "capacity": 6,
                     "serves": ["M", "X"]},
                    {"id": "T2", "dispatch_cost": 2, "capacity": 4,
                     "serves": ["M"]},
                    {"id": "T3", "dispatch_cost": 2, "capacity": 2,
                     "serves": ["X"]},
                ]})
res = r.json()
check(r.status_code == 200 and res["ok"], "fo tankers ok")
check(res["solver"] == "failover" and res["plan_id"] == fid, "fo meta")
# {T1}=5 与 {T2,T3}=4 -> 选费用更低的 [T2,T3]
check(res["selected_ids"] == ["T2", "T3"], f"fo sel {res['selected_ids']}")
check(res["total_dispatch_cost"] == 4, "fo cost")
check([p["node_id"] for p in res["demand_points"]] == ["M", "X"],
      "fo demand points")

# ---- 无断水需求: 空调度 ----
NET_NOLEAK = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "H"}],
    "valves": [],
}
r = client.post("/api/plan", json=NET_NOLEAK)
nl = r.json()
check(nl["feasible"] and nl["closed_valves"] == [], "noleak plan saved")
nid = nl["id"]
r = client.post(f"/api/plans/{nid}/tankers", json={})
res = r.json()
check(r.status_code == 200 and res["ok"], "noleak ok")
check(res["selected_ids"] == [] and res["total_dispatch_cost"] == 0
      and res["deliveries"] == [] and res["total_demand"] == 0,
      "noleak empty dispatch")
check("空调度" in res["message"], "noleak msg")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/tankers", json={})
check(r.status_code == 404, f"404 got {r.status_code}")

# ---- 不可行方案: 不能规划应急送水分配 ----
BAD = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = client.post("/api/plan", json=BAD)
bad_plan = r.json()
check(bad_plan["feasible"] is False, "infeasible plan saved")
bad_id = bad_plan["id"]
r = client.post(f"/api/plans/{bad_id}/tankers", json={})
check(r.status_code == 422, f"infeasible tankers {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bad_id) in e for e in errs),
      f"infeasible msg {errs}")

# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post(f"/api/plans/{pid}/tankers", json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 需水量: 遗漏 / 多余 / 非正整数
expect_422({"demands": {"A": 1}, "candidates": []}, ["缺少需求点的需水量: B"])
expect_422({"demands": {"A": 1, "B": 1, "S": 1}, "candidates": []},
           ["提供了非需求点的需水量(多余编号): S"])
for bad in [0, -1, 1.5, "5", True, None]:
    expect_422({"demands": {"A": bad, "B": 1}, "candidates": []},
               ["需水量必须是正整数"])
# 候选车: 超过 6 辆 / 编号重复 / 缺少编号 / 费用非法 / 可服务点非法
expect_422({"demands": DEMANDS, "candidates": [
    {"id": f"T{i}", "dispatch_cost": 1, "capacity": 1, "serves": ["A"]}
    for i in range(7)]}, ["超过上限 6"])
expect_422({"demands": DEMANDS, "candidates": [
    {"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": ["A"]},
    {"id": "T1", "dispatch_cost": 2, "capacity": 1, "serves": ["B"]}]},
    ["车辆编号重复"])
expect_422({"demands": DEMANDS, "candidates": [
    {"dispatch_cost": 1, "capacity": 1, "serves": ["A"]}]}, ["缺少车辆编号"])
for bad in [0, -1, 1.5, "5", True, None]:
    expect_422({"demands": DEMANDS, "candidates": [
        {"id": "T1", "dispatch_cost": bad, "capacity": 1, "serves": ["A"]}]},
        ["出车费必须是正整数"])
    expect_422({"demands": DEMANDS, "candidates": [
        {"id": "T1", "dispatch_cost": 1, "capacity": bad, "serves": ["A"]}]},
        ["载水量必须是正整数"])
expect_422({"demands": DEMANDS, "candidates": [
    {"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": []}]},
    ["必须选择至少一个可服务的需求点"])
expect_422({"demands": DEMANDS, "candidates": [
    {"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": ["A", "S"]}]},
    ["S 不是断水需求点"])
expect_422({"demands": DEMANDS, "candidates": [
    {"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": ["A", "A"]}]},
    ["可服务需求点重复: A"])

# ---- 送水预览未新增任何历史方案(只读) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = sorted(p["id"] for p in plans)
check(ids == sorted(set([pid, fid, nid, bad_id])),
      f"plan ids {ids}")

print(f"tankers API tests: {ok} checks passed")
