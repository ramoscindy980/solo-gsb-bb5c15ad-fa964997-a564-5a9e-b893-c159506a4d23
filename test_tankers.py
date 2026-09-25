"""plan_tankers 应急送水分配预览测试: 目标次序 / 字典序决胜 / 多车分担 /
最大可配送量与缺口 / 空调度 / 录入校验 / 随机暴力对拍。"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, plan_tankers, solve, _Graph, validate_network,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(snapshot, closed, demands, cands, keys):
    try:
        plan_tankers(snapshot, closed, demands, cands)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


# ---- 双支路管网: 计划关闭 V1+V2 后普通节点 A、B 断水(需求点) ----
# S --p1(V1)-- A --p3-- L1(漏点)
# S --p2(V2)-- B --p4-- L2(漏点)
# S --p5----- H(必保)
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
BASE2 = solve(NET2)
check(BASE2["feasible"] and BASE2["closed_valves"] == ["V1", "V2"],
      f"net2 plan {BASE2.get('closed_valves')}")
CLOSED2 = BASE2["closed_valves"]


def valid_alloc(res, demands, cands, selected_ids):
    """配送方案合法性: 只走可服务边 / 车不超载 / 点满足需求 / 总量一致。"""
    by_veh = {}
    by_pt = {}
    serve_of = {c["id"]: set(c["serves"]) for c in cands}
    cap_of = {c["id"]: c["capacity"] for c in cands}
    for d in res["deliveries"]:
        check(d["vehicle_id"] in selected_ids, "delivery by selected vehicle")
        check(d["node_id"] in serve_of[d["vehicle_id"]], "delivery on serve edge")
        check(isinstance(d["amount"], int) and d["amount"] > 0, "positive amount")
        by_veh[d["vehicle_id"]] = by_veh.get(d["vehicle_id"], 0) + d["amount"]
        by_pt[d["node_id"]] = by_pt.get(d["node_id"], 0) + d["amount"]
    for vid, tot in by_veh.items():
        check(tot <= cap_of[vid], f"capacity {vid}")
    for nid, need in demands.items():
        check(by_pt.get(nid, 0) == need, f"demand met {nid}")
    check(res["delivered_total"] == sum(d["amount"] for d in res["deliveries"]),
          "delivered_total matches")
    check(res["delivered_total"] == sum(demands.values()), "all demand delivered")


# 1. 基本求解: 需求 A=5,B=3; T1(费4,载6,A,B) 单独不够, T1+T2 刚好
DEM1 = {"A": 5, "B": 3}
CAND1 = [
    {"id": "T1", "dispatch_cost": 4, "capacity": 6, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 3, "capacity": 3, "serves": ["B"]},
]
r = plan_tankers(NET2, CLOSED2, DEM1, CAND1)
check(r["ok"] is True, "t1 ok")
check(r["selected_ids"] == ["T1", "T2"], f"t1 sel {r['selected_ids']}")
check(r["total_dispatch_cost"] == 7, "t1 cost")
check(r["total_demand"] == 8 and r["delivered_total"] == 8, "t1 totals")
check(r["candidate_count"] == 2 and r["combination_count"] == 4, "t1 counts")
check(r["feasible_combination_count"] == 1, "t1 feasible combos")
check([p["node_id"] for p in r["demand_points"]] == ["A", "B"], "t1 demand pts")
check([p["demand"] for p in r["demand_points"]] == [5, 3], "t1 demands")
check(r["closed_valves"] == ["V1", "V2"], "t1 closed")
# A 只能由 T1 送: T1->A=5, T1->B=1, T2->B=2
amap = {(d["vehicle_id"], d["node_id"]): d["amount"] for d in r["deliveries"]}
check(amap == {("T1", "A"): 5, ("T1", "B"): 1, ("T2", "B"): 2},
      f"t1 deliveries {amap}")
check("只读" in r["message"] and "不修改" in r["message"], "t1 readonly msg")
valid_alloc(r, DEM1, CAND1, r["selected_ids"])

# 2. 费用优先: 一辆大车(费5) vs 两辆小车(费2+2=4) -> 选两辆
r = plan_tankers(NET2, CLOSED2, {"A": 3, "B": 3}, [
    {"id": "BIG", "dispatch_cost": 5, "capacity": 6, "serves": ["A", "B"]},
    {"id": "S1", "dispatch_cost": 2, "capacity": 3, "serves": ["A"]},
    {"id": "S2", "dispatch_cost": 2, "capacity": 3, "serves": ["B"]},
])
check(r["ok"] and r["selected_ids"] == ["S1", "S2"], f"t2 sel {r['selected_ids']}")
check(r["total_dispatch_cost"] == 4, "t2 cost")

# 3. 同费字典序: {T1}=3 与 {T2,T3}=3 并列, 取 ["T1"]
r = plan_tankers(NET2, CLOSED2, {"A": 2, "B": 2}, [
    {"id": "T1", "dispatch_cost": 3, "capacity": 4, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 1, "capacity": 2, "serves": ["A"]},
    {"id": "T3", "dispatch_cost": 2, "capacity": 2, "serves": ["B"]},
])
check(r["ok"] and r["selected_ids"] == ["T1"], f"t3 sel {r['selected_ids']}")
check(r["total_dispatch_cost"] == 3, "t3 cost")
check(r["feasible_combination_count"] == 5, "t3 feasible combos")

# 4. 多车分担同一需求点: A=8, 两车各载 5, 都必须出车
r = plan_tankers(NET2, CLOSED2, {"A": 8, "B": 1}, [
    {"id": "T1", "dispatch_cost": 2, "capacity": 5, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 2, "capacity": 5, "serves": ["A", "B"]},
])
check(r["ok"] and r["selected_ids"] == ["T1", "T2"], f"t4 sel {r['selected_ids']}")
check(r["delivered_total"] == 9, "t4 delivered")
valid_alloc(r, {"A": 8, "B": 1}, [
    {"id": "T1", "dispatch_cost": 2, "capacity": 5, "serves": ["A", "B"]},
    {"id": "T2", "dispatch_cost": 2, "capacity": 5, "serves": ["A", "B"]},
], r["selected_ids"])
a_got = sum(d["amount"] for d in r["deliveries"] if d["node_id"] == "A")
check(a_got == 8 and len({d["vehicle_id"] for d in r["deliveries"]
                         if d["node_id"] == "A"}) == 2, "t4 split A")

# 5. 全部候选仍无法满足: 返回最大可配送量与总缺口(各点缺口唯一确定)
r = plan_tankers(NET2, CLOSED2, {"A": 10, "B": 5}, [
    {"id": "T1", "dispatch_cost": 1, "capacity": 6, "serves": ["A"]},
    {"id": "T2", "dispatch_cost": 1, "capacity": 3, "serves": ["B"]},
])
check(r["ok"] is False, "t5 not ok")
check(r["reason_code"] == "INSUFFICIENT_DELIVERY_CAPACITY", "t5 code")
check(r["max_deliverable"] == 9 and r["shortage"] == 6, "t5 max/shortage")
check(r["total_demand"] == 15, "t5 total demand")
check(r["per_point"] == [
    {"node_id": "A", "demand": 10, "delivered": 6, "shortage": 4},
    {"node_id": "B", "demand": 5, "delivered": 3, "shortage": 2},
], f"t5 per_point {r['per_point']}")
check(sum(d["amount"] for d in r["deliveries"]) == 9, "t5 deliveries sum")
check("最大可配送" in r["message"] and "总缺口" in r["message"], "t5 msg")

# 6. 需求点无车可服务: B 的需水量全部成为缺口
r = plan_tankers(NET2, CLOSED2, {"A": 2, "B": 4}, [
    {"id": "T1", "dispatch_cost": 1, "capacity": 9, "serves": ["A"]},
])
check(r["ok"] is False and r["max_deliverable"] == 2 and r["shortage"] == 4,
      f"t6 {r['max_deliverable']}/{r['shortage']}")
pp = {p["node_id"]: p for p in r["per_point"]}
check(pp["B"]["delivered"] == 0 and pp["B"]["shortage"] == 4, "t6 B short")

# 7. 无候选车 -> 最大可配送量 0
r = plan_tankers(NET2, CLOSED2, {"A": 2, "B": 1}, [])
check(r["ok"] is False and r["max_deliverable"] == 0
      and r["shortage"] == 3 and r["candidate_count"] == 0, "t7 no cand")
check(r["combination_count"] == 1 and r["deliveries"] == [], "t7 combos")

# 8. 无断水需求 -> 空调度(不关阀方案; 漏点被隔离也不算需求点)
NET_NOLEAK = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "H"}],
    "valves": [],
}
r = plan_tankers(NET_NOLEAK, [], {}, [{"id": "T1", "dispatch_cost": 1,
                                       "capacity": 1, "serves": ["X"]}])
check(r["ok"] is True and r["selected_ids"] == [], "t8 empty sel")
check(r["total_dispatch_cost"] == 0 and r["deliveries"] == []
      and r["total_demand"] == 0, "t8 empty totals")
check("空调度" in r["message"], "t8 msg")
# 关阀后只有漏点被隔离(非普通节点) -> 同样空调度
NET_LEAKONLY = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
r = plan_tankers(NET_LEAKONLY, ["V1"], {}, [])
check(r["ok"] is True and r["selected_ids"] == []
      and r["demand_points"] == [], "t8b leak isolated only")

# 9. 需求点 = 全部断水普通节点(键必须恰好一致, 由校验保证, 见下)
# ---- 录入校验 ----
expect_errors(NET2, CLOSED2, {"A": 1}, [], ["缺少需求点的需水量: B"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1, "S": 2}, [],
              ["提供了非需求点的需水量(多余编号): S"])
for bad in [0, -1, 1.5, "5", True, None]:
    expect_errors(NET2, CLOSED2, {"A": bad, "B": 1}, [],
                  ["需水量必须是正整数"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": f"T{i}", "dispatch_cost": 1, "capacity": 1,
                "serves": ["A"]} for i in range(7)],
              ["超过上限 6"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1}, [["T1", 1, 1, ["A"]]],
              ["必须是对象"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"dispatch_cost": 1, "capacity": 1, "serves": ["A"]}],
              ["缺少车辆编号"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": ["A"]},
               {"id": "T1", "dispatch_cost": 2, "capacity": 1, "serves": ["B"]}],
              ["车辆编号重复: T1"])
for bad in [0, -1, 1.5, "5", True, None]:
    expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
                  [{"id": "T1", "dispatch_cost": bad, "capacity": 1,
                    "serves": ["A"]}],
                  ["出车费必须是正整数"])
    expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
                  [{"id": "T1", "dispatch_cost": 1, "capacity": bad,
                    "serves": ["A"]}],
                  ["载水量必须是正整数"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": "T1", "dispatch_cost": 1, "capacity": 1, "serves": []}],
              ["必须选择至少一个可服务的需求点"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": "T1", "dispatch_cost": 1, "capacity": 1}],
              ["必须选择至少一个可服务的需求点"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": "T1", "dispatch_cost": 1, "capacity": 1,
                "serves": ["A", "S"]}],
              ["S 不是断水需求点"])
expect_errors(NET2, CLOSED2, {"A": 1, "B": 1},
              [{"id": "T1", "dispatch_cost": 1, "capacity": 1,
                "serves": ["A", "A"]}],
              ["可服务需求点重复: A"])
expect_errors(NET2, ["V9"], {}, [], ["阀门 V9 不存在于方案快照的管网中"])
expect_errors({"nodes": [{"id": "S", "role": "inlet"},
                         {"id": "S", "role": "leak"}],
               "segments": [], "valves": []}, [], {}, [],
              ["方案快照数据不合法"])

# ---- 10. 随机暴力对拍 ----
def brute_max_flow(vehicles, demand_ids, dem):
    """独立实现的 Ford-Fulkerson(DFS 增广), 用于对拍最大配送量。"""
    k, d = len(vehicles), len(demand_ids)
    pidx = {nid: i for i, nid in enumerate(demand_ids)}
    n = k + d + 2
    src, sink = 0, n - 1
    INF = 10 ** 18
    cap = [[0] * n for _ in range(n)]
    for a, veh in enumerate(vehicles):
        cap[src][1 + a] = veh["capacity"]
        for nid in veh["serves"]:
            cap[1 + a][1 + k + pidx[nid]] = INF
    for i in range(d):
        cap[1 + k + i][sink] = dem[i]
    total = 0
    while True:
        seen = [False] * n

        def dfs(u, f):
            if u == sink:
                return f
            seen[u] = True
            for v in range(n):
                if not seen[v] and cap[u][v] > 0:
                    b = dfs(v, min(f, cap[u][v]))
                    if b > 0:
                        cap[u][v] -= b
                        cap[v][u] += b
                        return b
            return 0

        b = dfs(src, INF)
        if b == 0:
            break
        total += b
    return total


def brute_tankers(demand_ids, dem, cands):
    """按定义暴力: 返回 ((出车费, 编号列表) 或 None, 可行组合数, 全候选最大流)。"""
    total = sum(dem)
    m = len(cands)
    best = None
    feasible = 0
    for s in range(1 << m):
        veh = [cands[j] for j in range(m) if (s >> j) & 1]
        f = brute_max_flow(veh, demand_ids, dem)
        if f < total:
            continue
        feasible += 1
        cost = sum(cands[j]["dispatch_cost"] for j in range(m) if (s >> j) & 1)
        ids = tuple(sorted(cands[j]["id"] for j in range(m) if (s >> j) & 1))
        key = (cost, ids)
        if best is None or key < best:
            best = key
    all_flow = brute_max_flow(list(cands), demand_ids, dem)
    return best, feasible, all_flow


def random_network(rng):
    n_nodes = rng.randint(2, 8)
    ids = [f"N{i}" for i in range(n_nodes)]
    nodes = [{"id": ids[0], "role": "inlet"}]
    for nid in ids[1:]:
        nodes.append({"id": nid,
                      "role": rng.choice(["leak", "must_keep", "normal"])})
    segments, valves = [], []
    for i, j in itertools.combinations(range(n_nodes), 2):
        if rng.random() >= 0.45:
            continue
        sid = f"p{len(segments)}"
        segments.append({"id": sid, "endpoint_a": ids[i], "endpoint_b": ids[j]})
        if rng.random() < 0.5 and len(valves) < 6:
            valves.append({"id": f"V{len(valves)}", "segment_id": sid,
                           "cost": rng.randint(1, 9)})
    return {"nodes": nodes, "segments": segments, "valves": valves}


rng = random.Random(20260925)
for trial in range(300):
    net = random_network(rng)
    vids = [v["id"] for v in net["valves"]]
    closed = sorted(v for v in vids if rng.random() < 0.6)
    # 需求点: 关闭 closed 后断水的普通节点(用独立 BFS 计算)
    nodes, segments, valves = validate_network(net)
    g = _Graph(nodes, segments, valves)
    v_idx = {v["id"]: b for b, v in enumerate(g.valves_sorted)}
    mask = 0
    for v in closed:
        mask |= 1 << v_idx[v]
    fed = g.reachable(mask, g.inlet_set)
    dem_ids = sorted(nd["id"] for i, nd in enumerate(nodes)
                     if nd["role"] == "normal" and not (fed >> i) & 1)
    demands = {nid: rng.randint(1, 9) for nid in dem_ids}
    m = rng.randint(0, 6)
    cands = []
    for j in range(m):
        serves = sorted(nid for nid in dem_ids if rng.random() < 0.7)
        if not serves and dem_ids:
            serves = [rng.choice(dem_ids)]
        cands.append({"id": f"T{j}", "dispatch_cost": rng.randint(1, 9),
                      "capacity": rng.randint(1, 12), "serves": serves})
    if dem_ids:
        res = plan_tankers(net, closed, demands, cands)
    else:
        res = plan_tankers(net, closed, {}, cands)
        check(res["ok"] is True and res["selected_ids"] == []
              and res["total_dispatch_cost"] == 0 and res["deliveries"] == [],
              f"t{trial} empty dispatch")
        continue
    check(res["closed_valves"] == closed, f"t{trial} closed")
    check([p["node_id"] for p in res["demand_points"]] == dem_ids,
          f"t{trial} demand ids")
    check(res["total_demand"] == sum(demands.values()), f"t{trial} total")
    check(res["candidate_count"] == m
          and res["combination_count"] == (1 << m), f"t{trial} counts")
    best, feasible, all_flow = brute_tankers(
        dem_ids, [demands[nid] for nid in dem_ids], cands)
    check(res["feasible_combination_count"] == feasible, f"t{trial} feasible")
    if best is None:
        check(res["ok"] is False, f"t{trial} should fail")
        check(res["max_deliverable"] == all_flow, f"t{trial} max deliv")
        check(res["shortage"] == sum(demands.values()) - all_flow,
              f"t{trial} shortage")
        check(sum(d["amount"] for d in res["deliveries"]) == all_flow,
              f"t{trial} deliveries sum")
        check(sum(p["delivered"] for p in res["per_point"]) == all_flow,
              f"t{trial} per_point sum")
        check(sum(p["shortage"] for p in res["per_point"]) == res["shortage"],
              f"t{trial} per_point shortage")
        # 配送方案合法性: 不超载、只走可服务边、不超过需求
        cap_of = {c["id"]: c["capacity"] for c in cands}
        serve_of = {c["id"]: set(c["serves"]) for c in cands}
        by_veh, by_pt = {}, {}
        for d0 in res["deliveries"]:
            check(d0["node_id"] in serve_of[d0["vehicle_id"]],
                  f"t{trial} serve edge")
            by_veh[d0["vehicle_id"]] = by_veh.get(d0["vehicle_id"], 0) + d0["amount"]
            by_pt[d0["node_id"]] = by_pt.get(d0["node_id"], 0) + d0["amount"]
        for vid, tot in by_veh.items():
            check(tot <= cap_of[vid], f"t{trial} cap")
        for nid, tot in by_pt.items():
            check(tot <= demands[nid], f"t{trial} demand cap")
    else:
        check(res["ok"] is True, f"t{trial} should be ok")
        check(tuple(res["selected_ids"]) == best[1],
              f"t{trial} sel {res['selected_ids']} != {best[1]}")
        check(res["total_dispatch_cost"] == best[0], f"t{trial} cost")
        check(res["delivered_total"] == sum(demands.values()),
              f"t{trial} delivered")
        valid_alloc(res, demands, cands, res["selected_ids"])
    # 确定性: 重算一次结果完全一致
    res2 = plan_tankers(net, closed, demands, cands)
    check(res2 == res, f"t{trial} deterministic")

print(f"tankers solver tests: {passed} checks passed")
