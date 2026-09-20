"""OKX 算法单（云端 TP/SL）保护度核验（结构优化阶段 2·B2 第九刀）。

原样搬自 update_cache_cycle 的「Parallel Phase 2」段（51 行）：
- 就地修改传入的 positions（写 exchangeSl/exchangeTp/protectionStatus 等），
  并把失败原因 append 进 source_errors —— 两者都是**入参原地改**，故无需回传；
- fetch_json 与 enrich_risk_fields 由门面注入（分别是 r20_backend/dashboard_cache.py 的
  `_fetch_json` 与 `enrich_position_risk_fields` —— 后者本身是薄壳，
  注入它才能让 POSITION_TRACKER_FILE 的 patch 继续生效）；trackers 是调用方局部量；
- 段内 `else: algo_results = {}` 这支在原文里就是**死赋值**（其后再无使用），
  照抄保留，不在本刀顺手清理。

## 第一百二十二刀：与跨所生产者**统一契约**（`cloud_oco_verified`）

`cloud_oco_verified` 此前**只有跨所路径**（`multi_venue.py`）会写，OKX 路径从来不写；
而前端判据是 `p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected'`
⇒ **同一个 `partially_protected` 状态在 OKX 行算"已保护"、在 binance 行算"未保护"**
（缺字段被当成 not-false）。更糟的是 `unknown`（不可判定）在 OKX 行也会被算作已保护。

现两边同口径：`cloud_oco_verified = (protectionStatus == "fully_protected")`，
并统一补 `protectionLegs`。⇒ 前端**无需改动**即得到一致且更严的判据
（`partially_protected` / `unknown` 一律不再算"已保护"）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from scripts import okx_rest

__all__ = ["collect_algo_protection"]


def _trigger_type_legs(algo):
    """把一条 OKX 算法腿按**它确实带了的触发价**标注 `kind`（供类型字段三态判定）。

    只有带 `slTriggerPx` 才算"有止损腿"，只有带 `tpTriggerPx` 才算"有止盈腿" ——
    否则"没有该类腿"会被误报成"未上报类型"。
    """
    legs = []
    if algo.get("slTriggerPx"):
        legs.append({"kind": "sl", "trigger_px_type": algo.get("slTriggerPxType")})
    if algo.get("tpTriggerPx"):
        legs.append({"kind": "tp", "trigger_px_type": algo.get("tpTriggerPxType")})
    return legs


def collect_algo_protection(positions, source_errors, fetch_json,
                           enrich_risk_fields, trackers):
    # 惰性 import（不在 import 期拉 scripts 侧模块）；提到函数首行是因为下面的三态
    # 类型字段在循环里**多处**使用，曾因 import 写在用点之后触发 UnboundLocalError。
    from scripts.trader.venue_protection import (
        _is_full_close, protection_trigger_type_fields,
    )

    # Parallel Phase 2: Exchange algo orders for live TP/SL protection
    if positions:
        with ThreadPoolExecutor(max_workers=min(len(positions), 6)) as pool:
            futures = {
                pos["instId"]: pool.submit(
                    fetch_json,
                    okx_rest.pending_algo_orders,
                    pos["instId"],
                )
                for pos in positions
            }
            algo_results = {inst_id: f.result() for inst_id, f in futures.items()}

        for position in positions:
            algo_ok, algo_orders, algo_error = algo_results.get(position["instId"], (False, [], "timeout"))
            if not algo_ok:
                source_errors.append(f"algo {position['instId']}: {algo_error}")
                algo_orders = []
            # 第一百六十七刀：触发价类型（读失败/无腿/未上报 三态，**不**用默认值顶替）。
            # 取数失败时先按"unknown"写，下面有腿时会按选中的腿覆盖。
            position.update(protection_trigger_type_fields([], readable=bool(algo_ok)))
            matching_algos = [
                o for o in (algo_orders or [])
                if str(o.get("state", "live")).lower() in {"live", "effective"}
                and str(o.get("posSide", "net")).lower() in {position["posSide"], "net"}
                and str(o.get("reduceOnly", "true")).lower() in {"true", "1", "yes"}
            ]
            # ⚠️ 第一百二十刀修正（真机可达的**假阴性**）：`protected_size` 只累加 `sz`，
            # 于是「整仓平」语义的腿（OKX `closePosition=true`；Gate `close`+`size=0`）
            # 因为**给不出张数**而被算成 0 覆盖 ⇒ 面板/提示词把这笔报成
            # `partially_protected / 0%`，而交易所那条腿其实**平掉整个仓位**。
            # 判定与跨所路径**共用同一个已专测谓词**（`venue_protection._is_full_close`
            # 覆盖 Gate close/auto_size、Binance/OKX closePosition），不另写一套。
            _pos_sz = float(position.get("pos_sz") or position.get("pos") or 0)
            _sl_legs = [o for o in matching_algos if o.get("slTriggerPx")]
            full_close = any(_is_full_close(o) for o in _sl_legs)
            protected_size = sum(float(o.get("sz", 0) or 0) for o in _sl_legs)
            if full_close:
                protected_size = max(protected_size, _pos_sz)
            # 不可判定（**不得给出没有证据的确定结论**，与 `scan_protective_orders`
            # 的 `coverage_ok=None` 同一口径）的两种情形：
            #   a) 持仓规模读不出来（`pos_sz`/`pos` 都缺）⇒ 无从比较；
            #   b) 有止损腿但量读不出来、且不是整仓平语义。
            coverage_unknown = _pos_sz <= 0 or (bool(_sl_legs) and protected_size <= 0
                                                and not full_close)
            full_coverage = (not coverage_unknown) and protected_size >= _pos_sz * 0.999
            # 跨所路径也发这个字段 ⇒ 两边字段齐整（口径见 tests/ui/test_protection_contract.py）
            position["protectionLegs"] = len(matching_algos)
            live_algo = next((o for o in matching_algos if o.get("slTriggerPx") and o.get("tpTriggerPx")), None)
            if live_algo and full_coverage:
                position["exchangeSl"] = float(live_algo.get("slTriggerPx", 0) or 0)
                position["exchangeTp"] = float(live_algo.get("tpTriggerPx", 0) or 0)
                position["protectionStatus"] = "fully_protected"
                position["protectionCoveragePct"] = 100.0
                position["protectionAlgoId"] = live_algo.get("algoId", "")
                position["cloud_oco_verified"] = True
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(live_algo), readable=True))
            elif coverage_unknown:
                sl_algo = _sl_legs[0]
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "unknown"
                position["protectionCoveragePct"] = None
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
                position["cloud_oco_verified"] = False
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(sl_algo), readable=True))
            elif matching_algos:
                sl_algo = next((o for o in matching_algos if o.get("slTriggerPx")), {})
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "partially_protected"
                position["protectionCoveragePct"] = round(min(100.0, protected_size / max(_pos_sz, 1e-12) * 100), 1)
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
                position["cloud_oco_verified"] = False
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(sl_algo), readable=True))
            else:
                position["exchangeSl"] = None
                position["exchangeTp"] = None
                position["protectionStatus"] = "unprotected"
                position["protectionCoveragePct"] = 0.0
                position["protectionAlgoId"] = ""
                position["cloud_oco_verified"] = False
    else:
        algo_results = {}

    enrich_risk_fields(positions, trackers)
