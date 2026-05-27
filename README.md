# sglang-log-analyzer

> An openclaw skill that parses sglang server logs and produces an hourly CSV + a self-contained HTML report with charts and anomaly flags. Built for capacity/performance review (e.g., "8-9点报表") and incident root-cause analysis.

---

## 目录

- [1. 这个 skill 能做什么](#1-这个-skill-能做什么)
- [2. 快速开始](#2-快速开始-30-秒)
- [3. 如何让别人使用这个 skill](#3-如何让别人使用这个-skill)
- [4. 使用方式详解](#4-使用方式详解)
- [5. 输出结果怎么看](#5-输出结果怎么看)
- [6. 异常告警 (anomaly flags)](#6-异常告警-anomaly-flags)
- [7. 典型场景示例](#7-典型场景示例)
- [8. 故障排查](#8-故障排查)
- [9. 项目结构](#9-项目结构)
- [10. 参考](#10-参考)

---

## 1. 这个 skill 能做什么

sglang 服务端的日志非常密集，并且不同模式打印的字段集是变化的：默认模式、PD 分离、MTP/speculative decoding、radix cache、`--log-requests` 各有各的格式。靠肉眼或 grep 很难做小时级容量复盘和异常根因定位。

**这个 skill 干一件事**：你把日志路径丢给它，它产出两个文件——

1. **CSV** — 一行一个小时桶，50+ 列指标的「权威数据源」(mean / p50 / p95 / p99 / max + 7 个布尔异常 flag)。
2. **HTML 报告** — 自包含离线可看 (matplotlib PNG 直接 base64 嵌入，无 CDN 依赖)，包含异常摘要表、按小时时间序列图、PD/MTP 专项段、按桶叙事卡片。

### 覆盖的 sglang 模式

| 模式 | 触发条件 | 额外解析字段 |
|------|----------|--------------|
| 默认 (normal) | 任意 `Prefill batch` / `Decode batch` 行 | `#new-seq #new-token #cached-token cache hit rate token usage #running-req #queue-req #pending-token input/gen throughput` |
| PD 分离 | 出现 `#prealloc-req` 或 `#transfer-req` | `#prealloc-req #transfer-req #retracted-req #inflight-req pre-allocated usage` + `KVTransferError` |
| MTP / 投机解码 | decode 行包含 `accept rate` | `accept len, accept rate` |
| Radix cache 激活 | prefill 中曾出现 `cache hit rate > 0` | 已在基础字段中，HTML 中专门成段 |
| `--log-requests` | 出现 `Receive: obj=TokenizedGenerateReqInput` / `Finish: obj=` | 每请求 `rid, e2e_latency, ttft, cached_tokens, completion_tokens` |

模式之间**不互斥**——PD + MTP + log-requests 的日志会同时输出三组列。

### 核心能力

- **自动识别模式** — 扫描前 5000 行做模式探测，可用 `--mode` 强制。
- **整文件分析 / 小时切片 / 自定义时间窗** — `--hour 20` 直接出"8-9 点报表"，`--range 14:30-21:15` 自定义。
- **字段顺序鲁棒** — 两阶段解析 (行型 anchor regex + key-value tokenizer)，sglang 版本升级字段顺序变化不会破坏解析。
- **7 个异常 flag** — token 压力、队列堆积、低接受率、回退尖峰、缓存崩坏、TTFT 退化、吞吐骤降；每个 flag 写进 CSV 的布尔列 + HTML 中红/黄高亮。
- **离线可看** — HTML 是单文件，base64 嵌图，发邮件、丢飞书都行。
- **零安装依赖** — 用 `uv` + PEP 723 内联元数据，首次运行 `uv` 自动建隔离 venv 装 pandas/matplotlib/jinja2，无须 `pip install`。

---

## 2. 快速开始 (30 秒)

前提：你有 `uv` 在 PATH 里 (`which uv` 应该返回路径，没有的话执行 `curl -LsSf https://astral.sh/uv/install.sh | sh`)。

```bash
cd /Users/zhuxinyi/Downloads/skill开发/sglang-log-analyzer

# 整个日志做小时报表
uv run scripts/analyze.py --log /path/to/sglang.log --out reports/

# 只看晚上 8-9 点
uv run scripts/analyze.py --log /path/to/sglang.log --hour 20 --out reports/
```

首次运行 `uv` 会下载 pandas / matplotlib / jinja2 (约 30 秒)，之后每次运行都是秒级。

打开 `reports/sglang_report_<basename>_<时间戳>.html` 看图，打开同名 `.csv` 看数。

### 在 openclaw 里使用 (推荐路径)

skill 已经软链到 `~/.openclaw/plugin-skills/sglang-log-analyzer/`。Claude/openclaw 会在用户提到下列关键词时自动激活：

- "分析 sglang 日志" / "analyze sglang log"
- "做小时报表" / "为什么 20:00 慢" / "8-9 点为什么慢"
- 文件路径以 `.log` 结尾且内容含 `Prefill batch.`

激活后 Claude 会直接调用 `scripts/analyze.py`，你**不需要也不应该**让它手动写解析代码。

---

## 3. 如何让别人使用这个 skill

### 3.1 单机本地安装

把整个 `sglang-log-analyzer/` 目录拷到任意位置，然后软链进 openclaw skill 目录：

```bash
# 1) 拷贝/克隆到自己的位置
cp -r /path/from/somewhere/sglang-log-analyzer ~/myskills/

# 2) 建立软链 (这是 openclaw 识别 skill 的入口)
ln -s ~/myskills/sglang-log-analyzer ~/.openclaw/plugin-skills/sglang-log-analyzer

# 3) 确认 SKILL.md 可读
cat ~/.openclaw/plugin-skills/sglang-log-analyzer/SKILL.md | head
```

确认 `uv` 已安装：

```bash
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

OK，下次和 Claude 对话提到 sglang 日志分析，skill 就会被加载。

### 3.2 通过 Git 分发

```bash
cd /Users/zhuxinyi/Downloads/skill开发/sglang-log-analyzer
git init && git add . && git commit -m "init"
# push 到内网 gitlab / github

# 别人 clone
git clone <repo-url> ~/skills/sglang-log-analyzer
ln -s ~/skills/sglang-log-analyzer ~/.openclaw/plugin-skills/sglang-log-analyzer
```

### 3.3 独立 CLI 使用 (不走 openclaw)

skill 里的脚本是纯 Python，不依赖 openclaw 框架，可以直接当独立 CLI 用：

```bash
uv run /path/to/sglang-log-analyzer/scripts/analyze.py --log mylog.log --out ./
```

放到 alias 或者 PATH 里更方便：

```bash
alias sglang-analyze='uv run /path/to/sglang-log-analyzer/scripts/analyze.py'
sglang-analyze --log mylog.log --hour 20
```

### 3.4 验证安装

```bash
cd ~/.openclaw/plugin-skills/sglang-log-analyzer
bash tests/fixtures/smoke.sh
```

预期输出：5 个 fixture 全部 PASS，每个 flag 在对应 fixture 中触发。失败说明 sglang 升级了字段或 uv 没装好。

---

## 4. 使用方式详解

### 4.1 完整 CLI 参数

```
uv run scripts/analyze.py --log PATH [options]

必需:
  --log PATH                日志文件 (用 "-" 读 stdin)

输出:
  --out DIR                 输出目录 (默认: 日志所在目录)
  --basename NAME           覆盖输出文件 basename
  --no-html                 不生成 HTML
  --no-csv                  不生成 CSV
  --json                    额外输出每事件 JSON (调试用)
  --quiet                   不打印进度

时间窗:
  --hour N                  限定到某个整点 (HH:00 - (HH+1):00)，取日志中最近一天的该小时
  --range HH:MM-HH:MM       自定义窗口
  --date YYYY-MM-DD         多日志日时限定某一天

模式控制:
  --mode {auto,normal,pd,mtp,radix}    强制模式 (auto 默认；逗号分隔可多选)
  --assume-interval SECS    日志缺少 [YYYY-MM-DD HH:MM:SS] 前缀时合成时间戳
  --tz TZNAME               时区 (默认本地)
```

退出码：`0` 成功，`2` 无可解析行，`3` 参数错，`4` 时间窗内无事件。

### 4.2 三种典型调用方式

```bash
# A. 整个文件
uv run scripts/analyze.py --log sglang.log --out reports/

# B. 单小时聚焦 (HTML 自动切换到散点+直方图视图)
uv run scripts/analyze.py --log sglang.log --hour 20 --out reports/

# C. 自定义窗口
uv run scripts/analyze.py --log sglang.log --range 14:30-21:15 --out reports/
```

### 4.3 没有时间戳的日志

部分用户重定向时丢了 systemd 时间戳。这种情况：

```bash
uv run scripts/analyze.py --log sglang.log --assume-interval 1 --out reports/
```

会按"每行间隔 1 秒"合成时间戳。小时桶就不是真实时钟时间，但相对顺序保持，吞吐/队列趋势仍可读。

### 4.4 多日志合并

skill 自身一次只处理一个文件。多文件先合并：

```bash
cat sglang-a.log sglang-b.log | uv run scripts/analyze.py --log - --basename merged --out reports/
```

---

## 5. 输出结果怎么看

每次运行产出两个同名文件：

```
reports/
├── sglang_report_<basename>_<YYYYMMDD-HHMMSS>.csv     ← 数据权威
└── sglang_report_<basename>_<YYYYMMDD-HHMMSS>.html    ← 给人看的
```

### 5.1 CSV — 50+ 列权威数据源

一行一个小时桶。完整字段字典见 [references/csv-schema.md](references/csv-schema.md)，这里只列分组：

| 分组 | 列示例 | 说明 |
|------|--------|------|
| Bucket 记账 | `bucket_start, bucket_end, prefill_events, decode_events, requests_received, requests_finished, pd_error_count` | 桶时间范围 + 各类事件数 |
| 吞吐 | `input_tps_{mean,p50,p95,max}`, `gen_tps_*` | prefill 输入吞吐 + decode 生成吞吐 |
| 队列并发 | `queue_req_*`, `running_req_*`, `pending_token_*` | 排队请求数、运行中请求数、待处理 token |
| KV 占用 | `token_usage_*` | 0-1 的占用率 |
| 缓存 | `cache_hit_rate_*`, `cached_tokens_sum`, `new_tokens_sum` | radix cache 命中率 |
| **PD 专属** | `prealloc_req_*`, `transfer_req_*`, `inflight_req_*`, `prealloc_usage_*`, `retracted_req_delta_sum` | 非 PD 模式下为 NaN |
| **MTP 专属** | `accept_len_*`, `accept_rate_*` | 非 MTP 模式下为 NaN |
| **请求级** | `ttft_ms_p{50,95,99}`, `e2e_latency_ms_*`, `cached_tokens_per_req_mean`, `completion_tokens_per_req_mean` | 仅 `--log-requests` 时有 |
| **异常 flag** | `flag_token_pressure`, `flag_queue_backlog`, `flag_low_accept_rate`, `flag_retraction_spike`, `flag_cache_collapse`, `flag_ttft_regression`, `flag_throughput_drop` | 7 个布尔列，0/1 |

#### 常见 CSV 查询

```bash
# 找最慢的小时
sort -t, -k <gen_tps_mean列> -n reports/*.csv | head

# 只看有任何异常 flag 的桶 (假设 flag_throughput_drop 是第 N 列)
awk -F, 'NR==1 || $N==1' reports/*.csv

# 比较两个小时 (用 csvkit 或直接打开 Excel/飞书表格)
csvcut -c bucket_start,gen_tps_mean,queue_req_p95,retracted_req_delta_sum reports/*.csv
```

#### 关于 `retracted_req_delta_sum`

`#retracted-req` 在 sglang 里是**单调累计计数器** (服务启动以来的总数)。直接看绝对值没意义，所以这一列保存的是**桶内增量** (`groupby.last() - groupby.first()`，遇到服务重启 reset 时会被 clamp 到 ≥0)。任何 ≥1 都触发 `flag_retraction_spike` (critical)。

### 5.2 HTML 报告 — 11 个 section

打开后页面从上到下：

| # | Section | 给你回答的问题 |
|---|---------|----------------|
| 1 | **Header** | 日志文件名、时间范围、检测到的模式 (badge)、事件总数、解析错误数 |
| 2 | **Anomaly summary** | 哪些桶亮红/黄？整篇报告先看这一个表 |
| 3 | **Throughput over time** | `input_tps_mean` + `gen_tps_mean` 实线 + `gen_tps_p95` 虚线，吞吐有没有掉？掉在什么时间？ |
| 4 | **Queue depth over time** | `queue_req_mean` 实线 + `queue_req_p95` 虚线 + 红线 @2000，队列在堆积吗？ |
| 5 | **Token usage over time** | `token_usage_mean/p95` + 红线 @0.95，KV 是否打满？ |
| 6 | **Cache hit rate** (radix 模式) | 命中率 + `prefill_events` 柱状叠加 (低流量桶可见)，缓存有没有崩？ |
| 7 | **PD section** (PD 模式) | `transfer_req_p95` 线 + `retracted_req_delta_sum` 柱状，PD KV 传输健康吗？ |
| 8 | **MTP section** (MTP 模式) | `accept_rate_mean` + 红线 @0.5；`accept_len_mean` 独立图；投机解码工作正常吗？ |
| 9 | **Request-level latency** (--log-requests 模式) | TTFT 和 e2e_latency 的 p50/p95/p99 多线对比 |
| 10 | **Per-bucket narrative** | 每个亮 flag 的桶一张卡片：触发的 flag 列表、top-3 driver、最多 5 条原始 `KVTransferError` |
| 11 | **Appendix** | CSV 直接 inline 成 `<table>` + skill 版本 + 生成时间戳 |

#### 单小时聚焦模式 (`--hour N`) 的 HTML

只有 1 个桶时时间序列图无意义，HTML 自动切换：

- 时间序列 → 散点 (decode 行粒度的 `gen_tps over time`, `token_usage` 散点)
- 多桶趋势 → 直方图 (`ttft_ms`, `e2e_latency_ms` 的请求级分布)
- 异常摘要、PD/MTP 段、按桶叙事保留

### 5.3 报告阅读流程 (推荐)

1. 打开 HTML，先看 **Anomaly summary** (section 2)。空 = 无异常，直接关页。
2. 有异常 → 翻到 **Per-bucket narrative** (section 10)，每个亮 flag 的桶都有卡片，告诉你哪些 flag 触发、top-3 driver 是哪些列、对应原始 `KVTransferError` 是什么。
3. 想看背景趋势 → 看 section 3-9 的时间序列图。
4. 想做对比或导出 → 用 CSV，直接拖进飞书表格 / Excel。

---

## 6. 异常告警 (anomaly flags)

7 个布尔列在每个桶上独立计算。完整阈值和触发逻辑也写进了 [references/csv-schema.md](references/csv-schema.md)。

| flag | 严重度 | 规则 | 你应该怎么读 |
|------|--------|------|--------------|
| `flag_token_pressure` | warn | `token_usage_p95 > 0.95` | KV 缓存接近爆。配合 `queue_req` 看：队列也涨 = 真过载；只是 token usage 高但队列空 = 长上下文场景，正常 |
| `flag_queue_backlog` | warn | `queue_req_p95 > 2000` | 排队请求过多。要么扩容，要么开 PD/限流 |
| `flag_low_accept_rate` | warn | MTP 且 `accept_rate_mean < 0.5` | 投机解码效率低。盯死 0.33：基本是 draft 模型坏了 (`1/(1+--speculative-num-steps=2)`) |
| `flag_retraction_spike` | **critical** | `retracted_req_delta_sum >= 1` | KV 不够、有请求被回退重跑。任何 ≥1 都该看，伴随 `KVTransferError` 几乎必然 |
| `flag_cache_collapse` | warn | radix 且 `cache_hit_rate_mean < 0.10` 且 `prefill_events > 5` | 前缀缓存形同虚设。检查 prompt 模板是不是每次都有动态前缀 |
| `flag_ttft_regression` | warn | `ttft_ms_p95 > 5000` | 首 token 慢于 5 秒。配合 `queue_req` 看：队列高 = 排队；队列空 = prefill 算力不足 |
| `flag_throughput_drop` | **critical** | `gen_tps_mean < 0.5 * rolling_max(gen_tps_mean, 4)` | 相比最近 4 桶的最高吞吐掉了一半以上。GPU 抖动、显存挤压、KV 满都可能 |

**严重度配色**：HTML 中 critical 红、warn 琥珀。

---

## 7. 典型场景示例

### 7.1 "8-9 点为什么慢" (容量复盘)

```bash
uv run scripts/analyze.py --log /var/log/sglang.log --hour 20 --out reports/
open reports/sglang_report_*_*.html
```

读 anomaly summary：

```
20:00 — flag_throughput_drop (critical), flag_retraction_spike (critical), flag_ttft_regression (warn)
```

跳到 per-bucket narrative，看到：

- top-3 driver: `gen_tps_mean=900` (相邻桶 2400), `retracted_req_delta_sum=4`, `ttft_ms_p95=8200ms`
- 原始 KVTransferError 行 5 条，`bootstrap_room` 不同

结论：**PD KV 传输出问题**。20 点有一批回退，KV 抢占导致后续 prefill 排队、TTFT 退化、吞吐掉一半。建议看对应时间段 decode worker 的 RDMA / NCCL 错误。

### 7.2 "对比 14:00 vs 20:00"

```bash
# 整文件跑一次
uv run scripts/analyze.py --log sglang.log --out reports/

# 打开 CSV，比较两行
csvcut -c bucket_start,gen_tps_mean,queue_req_p95,token_usage_p95,retracted_req_delta_sum reports/sglang_report_*.csv | grep -E '14:00|20:00|bucket_start'
```

### 7.3 "投机解码到底工不工作"

`accept_rate_mean` 列趋势：

- 0.80–0.94 健康
- 0.50–0.80 模型不太适配但能用
- < 0.50 触发 `flag_low_accept_rate`
- **恒定 0.33** 几乎 100% 是 draft 模型权重坏了 (或者 DP attention 干扰)

### 7.4 "前缀缓存有没有用"

`cache_hit_rate_mean`：

- > 0.30 持续 = 有效
- 接近 0 + `prefill_events` 高 = `flag_cache_collapse`，看 prompt 模板里有没有时间戳/UUID 这种每次变化的前缀

> ⚠️ 已知 bug：sglang 在 MTP 开启时 `--log-requests` 输出的每请求 `cached_tokens=0` ([sgl-project/sglang#20451](https://github.com/sgl-project/sglang/issues/20451))。这是 sglang 本身的 bug，但 `cache_hit_rate` 来自 prefill 行，仍然准确。

---

## 8. 故障排查

| 症状 | 原因 / 处置 |
|------|-------------|
| **CSV 是空的** / "no parseable lines" | 日志没 `Prefill batch`/`Decode batch` 行。sglang 在 INFO 级别打印这些，检查上游 `--log-level` |
| **模式没识别对** | `--mode pd` 强制 (可逗号分隔多模式) |
| **没有时间戳** | `--assume-interval 1` 合成 |
| **某些列全是 NaN** | 正常 — 该模式当前未激活 |
| **数字看着不对** | sglang 升级了字段。跑 `bash tests/fixtures/smoke.sh`，如果 fixture 全过但实日志解析坏，去 sglang 仓库 diff 最近的 logger 改动 |
| **HTML 在浏览器里图不显示** | 极个别浏览器对超长 base64 data URL 有限制。改用 Chrome 或者用 `--no-html` 加 CSV 自己画 |
| **首次 `uv run` 卡很久** | 在装 pandas/matplotlib (~30s+)。后续运行命中缓存，秒级 |
| **`uv: command not found`** | `curl -LsSf https://astral.sh/uv/install.sh \| sh`，重开 shell |

### 8.1 重新验证 (sglang 升级后)

```bash
cd /Users/zhuxinyi/.openclaw/plugin-skills/sglang-log-analyzer
bash tests/fixtures/smoke.sh
```

预期：5 个 fixture 都 PASS，每个对应 flag 在它该出现的桶里触发。如果失败，对照 [references/log-format.md](references/log-format.md) 检查 sglang 的当前行格式有没有变。

---

## 9. 项目结构

```
sglang-log-analyzer/
├── README.md                          # 本文件
├── SKILL.md                           # openclaw skill 入口 (YAML frontmatter + 简短指引)
├── references/
│   ├── log-format.md                  # 每种日志行格式、每个字段的含义
│   ├── csv-schema.md                  # 每个 CSV 列的单位与解读
│   └── html-report.md                 # HTML 各 section 详解、配色、单窗口模式
├── scripts/
│   ├── analyze.py                     # CLI 入口 (PEP 723 内联依赖)
│   ├── parser.py                      # 两阶段解析器
│   ├── aggregator.py                  # pandas 小时桶聚合 + flag 评估
│   ├── csv_writer.py                  # CSV 输出
│   ├── html_renderer.py               # matplotlib PNG → base64 → jinja2
│   └── templates/report.html.j2       # HTML 模板
└── tests/fixtures/
    ├── normal.log                     # 基线 prefill+decode 交错，无 flag
    ├── pd.log                         # retracted_req 0→3，触发 flag_retraction_spike
    ├── mtp.log                        # accept_rate 跌到 0.41，触发 flag_low_accept_rate
    ├── radix.log                      # cache hit rate 0.04–0.78 跨桶
    ├── log_requests.log               # Receive/Finish 配对，含 ttft + e2e_latency
    └── smoke.sh                       # 5 个 fixture 全跑 + assert 关键列
```

**Skill 解释顺序**：Claude 读 [SKILL.md](SKILL.md) → 知道何时激活 + CLI 怎么调；需要细节时再看 `references/*.md`。普通用户读这份 README。

---

## 10. 参考

### Skill 内部参考

- [SKILL.md](SKILL.md) — openclaw skill 注册入口
- [references/log-format.md](references/log-format.md) — sglang 日志行格式逐字段说明
- [references/csv-schema.md](references/csv-schema.md) — CSV 列字典
- [references/html-report.md](references/html-report.md) — HTML 各 section 详解

### 外部参考

- [sgl-project/sglang](https://github.com/sgl-project/sglang) — sglang 源码 (logger 改动看这里)
- [PEP 723](https://peps.python.org/pep-0723/) — 脚本内联依赖元数据
- [uv 文档](https://docs.astral.sh/uv/) — uv 安装和用法

---

**版本**：v1.0 · **作者**：基于 openclaw skill 规范开发 · **生成日期**：2026-05-27
