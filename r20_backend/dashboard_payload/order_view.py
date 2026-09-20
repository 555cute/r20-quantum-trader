"""OKX 挂单 → 仪表盘挂单行（结构优化阶段 4·B3 第二十三刀）。

原样搬自 `r20_backend/dashboard_cache.py::update_cache_cycle` 的「挂单遍历」段（71 行）。
与 `position_view.py` 同构：把交易所原始行规范化成前端直接消费的行。

## 这段在做什么

OKX 的挂单字段是"机器口径"，前端要的是"人话口径"，转换集中在两处：

1. **方向语义反转**：是否 `reduceOnly` 决定「买」到底是**开多**还是**平空**。
   同一个 `side="buy"`，reduceOnly 为真时是**平空**（不是开多）—— 判错会让
   前端把平仓单显示成开仓单，颜色和文案全反。
2. **价格显示三态**：无价/`"0"` → 市价单显示 `"市价"`、限价单显示 `"--"`；
   有价 → 转 float 后用 `%g` 格式化（去掉多余小数位），转不动则原样透出。

## 四处易错点（均原样保留）

1. **`reduceOnly` 判定**：`str(o.get("reduceOnly", "false")).lower() == "true"` ——
   用**字符串比较**而不是真值判断。OKX 返回的是字符串 `"true"`/`"false"`，
   若写成 `if o.get("reduceOnly")`，字符串 `"false"` 是 **truthy** →
   所有单子都被当成平仓单。这是本段最容易写错的一处。
2. **四象限文案**由 `reduce_only × side_raw × ord_type` 共同决定，共 8 种组合：
   平多/平空/买多/卖空 × 市价/限价。漏一个分支就会显示错文案。
3. **`px` 缺失或为 `"0"`**：市价单显示 `"市价"`，限价单显示 `"--"`；
   转 float 失败（非数值字符串）时**原样透出**而不是丢弃。
4. **附加保护单只取第一条**：`attachAlgoOrds[0]` 的 `tpTriggerPx`/`slTriggerPx`
   挂到行上，缺失时为 `"--"`。

## 与前一块（position_view）的取舍差异

`position_view` 里 `lever` 的兜底 `or 3.0` 有**除零缺陷**（字符串 `"0"` 是 truthy），
本轮已修。**本段不需要修**：它只做 `f"{o.get('lever', '3')}x"` 的**字符串拼接**，
不参与任何算术，`lever` 为 `"0"` 时显示 `"0x"` 是**正确**的（如实反映交易所给的杠杆），
不存在崩溃或口径问题。故此处**不做**"顺手对齐" —— 对齐反而会把真实的 0x 改成 3x。

## 与门面的分工

- `pending_orders_list` 由调用方传入并**原地 append**（与 `position_view`、
  `algo_protection` 同约定）。
- `tz_beijing` 与 `datetime` 由调用方注入：门面同名名字会被测试重定向。
"""
from __future__ import annotations

__all__ = ["collect_pending_order_rows"]


def collect_pending_order_rows(orders_data, pending_orders_list, *, tz_beijing, datetime):
    """把 OKX 挂单原始行规范化后 append 进 `pending_orders_list`。

    `orders_data` 不是 list 时什么都不做，与原文 `if isinstance(orders_data, list):`
    守卫等价。

    返回本段追加的行数（便于调用方/测试断言），不影响既有语义。
    """
    added = 0

    if isinstance(orders_data, list):
        for o in orders_data:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_beijing).strftime("%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            inst_clean = inst_id.replace("-USDT-SWAP", "").replace("-SWAP", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()
            raw_px = str(o.get("px") or "").strip()

            if reduce_only:
                if side_raw == "sell":
                    side_label = "市价平多" if ord_type == "market" else "限价平多"
                    is_long = False
                    side_color = "rose"
                else:
                    side_label = "市价平空" if ord_type == "market" else "限价平空"
                    is_long = True
                    side_color = "emerald"
            else:
                if side_raw == "buy":
                    side_label = "市价买多" if ord_type == "market" else "限价买多"
                    is_long = True
                    side_color = "emerald"
                else:
                    side_label = "市价卖空" if ord_type == "market" else "限价卖空"
                    is_long = False
                    side_color = "rose"

            if not raw_px or raw_px == "0":
                px_display = "市价" if ord_type == "market" else "--"
            else:
                try:
                    px_float = float(raw_px)
                    px_display = f"{px_float:g}"
                except ValueError:
                    px_display = raw_px

            attach_list = o.get("attachAlgoOrds", [])
            tp_px = "--"
            sl_px = "--"
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_px = str(att.get("tpTriggerPx") or "--")
                sl_px = str(att.get("slTriggerPx") or "--")

            try:
                from scripts.okx_runtime import current_environment
                _okx_env = current_environment()
                _acc_mode = "DEMO" if _okx_env.simulated else "LIVE"
                _env_mode = _okx_env.mode.lower()
            except Exception:
                _acc_mode = "DEMO"
                _env_mode = "demo"

            _margin_usdt = None
            try:
                from scripts.instrument_pool import load_instruments
                # 面值只认池子（单一事实源）。池子里没有该标的 ⇒ 不猜 1.0/其它面值，
                # 直接给 None（前端回落成原生张数）—— 捏造的保证金比没有更危险。
                ct_val = 0.0
                for target_item in load_instruments():
                    if target_item.get("instId") == inst_id or target_item.get("name") == inst_clean:
                        ct_val = float(target_item.get("ctVal", 0.0) or 0.0)
                        break
                _px_float = float(raw_px) if (raw_px and raw_px != "0") else 0.0
                _sz_float = abs(float(o.get("sz", 0) or 0))
                _lev_num = float(str(o.get("lever", "3")).replace("x", "") or 3.0)
                if _lev_num <= 0:
                    _lev_num = 3.0
                if _px_float > 0 and _sz_float > 0 and ct_val > 0:
                    _margin_usdt = round((_sz_float * ct_val * _px_float) / _lev_num, 2)
            except Exception:
                _margin_usdt = None

            pending_orders_list.append({
                "venue": "okx",
                "exchange": "okx",
                "ordId": str(o.get("ordId", "")),
                "name": inst_clean,
                "inst": inst_clean,
                "instId": inst_id,
                "side": "buy" if side_raw == "buy" else "sell",
                "side_label": side_label,
                "side_raw": side_raw,
                "posSide": pos_side,
                "is_long": is_long,
                "side_color": side_color,
                "ord_type": ord_type,
                "lever": f"{o.get('lever', '3')}x",
                "px": px_display,
                "sz": str(o.get("sz", "--")),
                "margin_usdt": _margin_usdt,
                "cTime": str(o.get("cTime", "")),
                "time": c_time_str,
                "state": str(o.get("state", "live")),
                "tp_px": tp_px,
                "sl_px": sl_px,
                "account_mode": _acc_mode,
                "environment": _env_mode,
            })
            added += 1

    return added
