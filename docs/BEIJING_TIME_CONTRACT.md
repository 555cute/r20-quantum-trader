# 北京时间契约

交易系统用户可见的时间与业务自然日统一为 `Asia/Shanghai`（UTC+8），不依赖服务器或浏览器的本地时区。

## 显示与解析

- `frontend/src/utils/format.ts` 是前台/后台页面的唯一时间格式化入口。
- 无时区的历史业务文本默认按北京时间解释（台账、审计、新闻、推演、Gateway）。
- 明确 UTC 来源的旧字段，例如 `updated_utc`、旧策略归档 `archived_at`，使用 `utcStrToBj`；不能对这些字段使用默认的北京时间解析。
- 有 `Z`、`+08:00`、其他 offset 的值保留其真实瞬间，不能再盲目加 8 小时。
- epoch 秒/毫秒只转换显示，不平移数值。CSV 台账时间输出带 `+08:00`。
- K 线刻度、倒计时、图上 VWAP 自然日、推演日期分组、新闻相对年龄、策略快照、故障切换事件及导出文件名都采用北京时间。
- 前台与后台顶栏标注北京时间；中等宽度前台为避免与导航相撞隐藏额外时钟，不改变任何时间显示口径。

## 后端

- `r20_backend/time_utils.py` 负责有时区/无时区业务输入的规范化。
- 日亏损、日报、权益日序列按转换后的北京时间归日。
- 自进化快照关联必须先完整解析时区再转北京，禁止截去输入 offset 再关联。
- Gateway 历史 `last_scheduled_at` 读入后统一为 aware 北京时间，防止 naive/aware 相减失败及跨日重复调度。
- 因子快照、投委会配置、策略归档、模型故障记录、清理报告、QQ/Gate 控制台日志的新时间文本显式北京并携带 `+08:00`。
- Gate 新成交的 SQLite `time` 保持已有字符串格式，但显式按北京钟生成。
- Scheduler 使用局部 formatter，从日志记录的原始 epoch 转换，不修改全局 logging 行为。
- watchdog 的 `date` 单次调用指定 `TZ=Asia/Shanghai`。

## 必须保持原义的字段

OKX UTC 签名、S3 SigV4 日期、交易所 Unix 时间戳、TTL、耗时、鉴权有效期和数据库原始审计时间不平移。历史原始日志与已归档文件不批量改写。

审计发现旧 SQLite Gate 成交有 2 条无时区 UTC `time`；原始值未改写。当前页面的 Gate 历史来自 `gate_lab_ledger.json.close_ts`，按 epoch 显示北京，而非这两条 SQLite 文本。若未来新增 SQLite 导出/页面，必须按来源规范化历史 Gate 记录，不能直接假定全部是北京。

## 回归验证

```sh
for zone in UTC Asia/Shanghai America/Los_Angeles Europe/London Asia/Kolkata; do
  TZ="$zone" node --test frontend/tests/time.test.mjs
done
PYTHONPATH=.:scripts TZ=UTC python -m pytest -q \
  tests/test_beijing_time_contract.py tests/test_beijing_time_producers.py \
  tests/test_gateway_runtime.py tests/test_evolution_observability.py \
  tests/test_leverage_range_and_council.py tests/test_policy_snapshot_isolated.py \
  tests/test_council_alignment_import_export.py
cd frontend && npx vue-tsc --noEmit && npm run build
```

2026-09-10 实测：5 时区各 5 项前端测试通过；后端 99 项及 10 个 subtests 通过；类型检查及构建通过。前台运行页已读到新顶栏北京时钟、08:30 推演记录；自然 factor 周期已生成带 `+08:00` 的快照。

## 部署边界

前端构建直接由当前服务加载；定时脚本在下一自然周期读新代码。常驻后端/Gateway、QQ daemon 与已运行 watchdog 必须经维护重启才加载对应修改。

2026-09-10 用户明确确认重启后，08:51–08:52 北京时间完成维护：
- 确认 08:45 交易周期已成功结束、无交易脚本在运行后才停止旧服务；未手动触发交易或修改持仓。
- 新后端 PID 2456208，托管 Gateway PID 2456212；QQ daemon PID 2456209 已连接并 ONLINE，启动日志为 `08:51:18+08:00`。
- 旧 watchdog 停止后未被 supervisor 自动拉起，已显式补充启动 PID 2457191，单实例确认，启动日志为 `08:51:51 +08:00`。现有 supervisor 配置与实际自动重启行为不一致，不能将本次成功启动视作已验证容器重启恢复。
- `/api/v1/health` HTTP 200/status=ok；`/api/all` HTTP 200，显示 `2026-09-10 08:52:10 (北京时间)`。
- 重启后自然 factor 周期 `08:51:48–08:51:51` success；快照 `2026-09-10 08:51:51+08:00` 与 epoch 对应瞬间一致。
- 维护暂停文件已移除，四个服务各一个进程；历史日志保留原值，新日志使用北京时间。
