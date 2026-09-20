# R20 观测栈（Prometheus + Grafana）

把后端 `/api/v1/admin/metrics` 的指标变成**会响的入口**：告警规则 + 面板 + 抓取配置。

| 文件 | 作用 |
|---|---|
| `prometheus.yml` | 抓取配置（指标端点需管理员鉴权，令牌走**文件注入**，绝不入仓库） |
| `alerts.yml` | 告警规则（**13 条**，每条对应一次**真实发生过**的故障形态） |
| `grafana-dashboard.json` | Grafana 面板（导入即用；UID `r20-quantum-trader`；**15 个面板 / 26 条查询**） |
| `grafana-datasource.yml` / `grafana-dashboards.yml` | Grafana 自动配置（数据源 + 面板加载） |
| `docker-compose.yml` | Prometheus + Grafana 一键起（端口**只绑 127.0.0.1**） |

## 快速开始

```sh
# 1) 令牌文件：内容 = 后端的 R20_ADMIN_TOKEN（与 .env 里一致），权限收紧
printf '%s' "$R20_ADMIN_TOKEN" > deploy/observability/metrics_token
chmod 600 deploy/observability/metrics_token
printf 'deploy/observability/metrics_token\n' >> .gitignore   # 确保不入库

# 2) 拉起（如后端不在本机，改 prometheus.yml 里的 targets）
R20_GRAFANA_PASSWORD='<自己设>' \
  docker compose -f deploy/observability/docker-compose.yml up -d

# 3) 打开：Prometheus http://127.0.0.1:9090/targets   Grafana http://127.0.0.1:3000
```

## 告警一览（写清"响了要做什么"）

| 告警 | 条件 | 严重度 | 收到后先看什么 |
|---|---|---|---|
| `R20BackendDown` | `up{job="r20-backend"} == 0` 持续 3m | critical | 进程是否活着、端口是否通、401 说明令牌失效/未注入 |
| `R20MetricsSourceMissing` | 某**必需**源 `source_ok{required="1"} == 0` 持续 10m | warning | 该源对应文件是否存在、写入方（worker）是否在跑 |
| `R20MarketDataSnapshotStale` | 快照年龄 > 45m 持续 5m | critical | **worker 周期是否还在跑**（15 分钟一轮，超 3 轮没写就是断了） |
| `R20MarketDataFetchFailures` | 15m 内失败增量 > 0 持续 5m | warning | 失败 kind 与 `last_error`（日志里每类只打一次，含交易所原始 code/msg） |
| `R20MarketDataNoRecentSuccess` | 某 kind 距上次成功 > 30m | critical | 该链路是否全断（ticker/candles 断 ⇒ 主脑会因数据无效停开新仓） |
| `R20MarketDataLatencyHigh` | p95 > 3s 持续 15m | warning | 尾延时抬升通常是失败前兆（超时上限 3.5s） |
| `R20VenueInstrumentsFailing` | 某所失败标的数 > 0 持续 30m | warning | 该所行情不完整 ⇒ 不应作为决策依据 |
| `R20VenueHealthStale` | `venue_health.json` 超 45m 未更新 | warning | 同 worker 断档（与行情快照同一个写入周期） |
| `R20ModelCallFailureRate` | 1h 成功率 < 80% 且样本 > 5 持续 15m | warning | 模型/密钥/额度；连续失败会同时走 AI 健康告警 |
| `R20MarketStreamSilent` | 某所曾收到 tick 但已静默 > 90s 持续 5m | critical | 该所流是否被静默（Binance JSON 订阅实测形态）；**没部署探测时此序列不存在，不会误报** |
| `R20MarketStreamParseErrors` | 15m 内帧解析失败 > 0 持续 5m | warning | 上游是否改了字段/频道格式（REST 取数不受影响） |
| `R20MarketStreamConnectErrors` | 15m 内连接/订阅错误 > 2 持续 5m | warning | 连错域、合约名拼错、『已连接但零数据帧』 |
| `R20RiskLimitMissing` | `count(r20_risk_limit) < 10` 持续 30m | warning | `risk_constants` 是否改名/导入失败（取不到就跳过，不补 0） |

阈值都是可调的，但**别调成"永远不响"**：第 137 刀那次 30 小时无信号，代价是约 30 小时不开新仓。

## 反漂移门（重要）

`tests/ops/test_observability_artifacts.py` 把本目录的三个文件与**真实渲染结果**
（`r20_backend/metrics.py::render_prometheus`）**双向对齐**：

- 面板/告警引用的每个 `r20_*` 族名必须真实产出（否则图静默空白、**告警永不触发**）；
- 真实产出的每个族名必须上面板/告警，或显式登记进测试里的 `INTENTIONALLY_UNUSED`。

所以：**在 `metrics.py` 里改族名，必须同步改这里**，否则 `pytest` 立刻翻红。
新增族时也要同步 `_full_snapshot()` 夹具，否则反漂移门会失去覆盖。

## 本目录的验证边界（诚实说明）

- ✅ 已在本机验证：面板 JSON 结构（stdlib `json`）、族名双向对齐、告警四要素
  （`expr`/`for`/`severity`/`annotations`）、括号平衡、令牌无字面量、compose 引用文件存在、
  端口只绑本机、保留期已设。
- ❌ **未在本机验证**（本机没有 `docker`/`promtool`/`pyyaml`，已实测）：
  YAML 通用语法、PromQL 语义、镜像可用性。部署机上请补：
  ```sh
  promtool check rules deploy/observability/alerts.yml
  docker compose -f deploy/observability/docker-compose.yml config -q
  ```
  本门的 YAML 校验是**窄结构**校验（按这几个文件的实际形态），不是 YAML 解析器。
