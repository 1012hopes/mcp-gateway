# MCP Gateway · 设计文档

> 一句话定位：在 Agent 与多个 MCP server 之间的网关，每轮只准入少量工具，并在证据齐备时确定性截断，全过程留下可校验、可重放的轨迹。
>
> 面试记忆点：**他知道 Agent 该在哪停。**

版本：v0.1（设计阶段）
周期：2–3 周，单人
技术栈：Python 3.11+ / 官方 MCP SDK (FastMCP) / SQLite / asyncio

---

## 0. 为什么做这个

三个具体现象，都是当前 Agent 工程的真实痛点：

1. 工具多了，模型选错。上游挂 6 个 server 就有 40+ 工具，`description` 只有一行，语义重叠严重。
2. 该停的时候不停。一个 3 步能完成的任务跑 11 步：重复查同一资源、换措辞再查、把已有结果重新验证。
3. 事后说不清。跑完只剩一段聊天记录，"为什么在第 7 步停"和"为什么没用那个工具"都无法复盘，更无法证明。

本项目只解决这三件事的交集：**在一个必经的卡点上做准入与截断，并把决策变成可验证的证据。**

三个短语对应的机制：

| 主打 | 对应机制 | 可交付的证据 |
|---|---|---|
| 工具选择 | 每轮生成 offered set 并记录 | Accuracy@K 曲线 |
| 轨迹可验证 | 哈希链 + 确定性重放 | 篡改检测 + 重放一致性 |
| 该在哪停 | 四级判据的守卫式截断 | 调用数下降 / 误停率 |

---

## 0.1 相关工作核查（GitHub，2026-11）

做过一轮检索，结论直接影响了上面那张表的分量分配，必须写下来。

### 已有大量同类：MCP 网关 / 聚合器

| 项目 | 量级 | 覆盖内容 | 与本项目关系 |
|---|---|---|---|
| Kong/kong | 44k★ | API/AI 网关，带 `mcp-gateway` topic | 基础设施层，不碰工具准入与终止 |
| casdoor/casdoor | 15k★ | Agent-first IAM / MCP 网关，鉴权为主 | 本项目的 Non-goal |
| TykTechnologies/tyk | 11k★ | 多协议网关含 MCP | 同上 |
| maximhq/bifrost | 9k★ | LLM + MCP + agent 网关，MCP Code Mode、故障转移、缓存 | 定位重叠在"网关"，但目标是性能与成本 |
| oomol-lab/open-connector | 6k★ | 1500+ SaaS 的 auth 网关 | 本项目的 Non-goal |
| withcoral/coral | 5k★ | Rust，"比 MCP tools/gateways 更精确的执行层" | 概念上最接近"执行面"，但无终止判据 |
| IBM/mcp-context-forge | 4.8k★, Python | registry + proxy + guardrails，"优化 Agent 与工具调用" | **最接近的一个**，但差异化在注册中心/联邦/护栏 |
| archestra-ai/archestra | 4.4k★ | guardrails + MCP registry + gateway + orchestrator | 平台化路线 |
| tbxark/mcp-proxy | 743★ | 纯多上游聚合 | 只做 FR-1 |
| sitbon/magg | 143★ | FastMCP 聚合器 | 只做 FR-1 |
| joshrotenberg/mcp-proxy | 19★ | Rust 反向代理，per-backend 中间件 + 可观测 | 观测有，但无可判定轨迹 |
| **p1s4/mcp-search-proxy** | 9★ | **search-first 代理：4 个固定工具（search/describe/call/refresh），本地 BM25，多上游** | **⚠️ 与本项目 A1 高度重合** |
| arcforgelabs-archive/mcp-proxy | 归档 | progressive tool disclosure，"约 95% context 降幅" | 同一思路的早期实现，已归档 |

### 检索不到同类：终止判定 / 轨迹可验证

用 "agent loop detection stop condition tool calling"、"agent trajectory replay audit" 等组合检索，**没有找到同类 MCP 网关项目**。最接近的 IBM forge 的差异化在注册与护栏，不在"该不该停"。

### 由此调整的三件事

1. **A1 工具准入降级为基础设施**。它的思路已被公开验证（p1s4 的 BM25 准入、arcforgelabs 的渐进披露），照做即可，**不能当卖点**。面试里被问"这不就是 mcp-search-proxy 吗"，正确答法是"是的，那半条路已经有人走通了，我复用它，然后把力气放在它没有的那半条"。
2. **卖点收敛到"终止判定 + 可验证轨迹"**。FR-4 / FR-5 / FR-6 是主线，FR-1 / FR-3 是必要的脚手架。
3. **README 必须有 Related work 章节，并点名 p1s4/mcp-search-proxy**。面试官一搜就能搜到，主动写出来是加分项（说明做过检索、知道边界），装作不知道才是减分。

---

## 1. 需求

### 1.1 功能需求

| ID | 需求 | 优先级 | 验收方式 |
|---|---|---|---|
| FR-1 | 作为单一 MCP server 接入，向下聚合 ≥2 个上游 MCP server | P0 | 单个端点能 `tools/list` 出全部工具 |
| FR-2 | 工具命名空间隔离，冲突自动加前缀；schema 冲突时拒绝启动 | P0 | 两个上游同名工具可共存 |
| FR-3 | 每轮生成 offered set（K 个工具），并记录到轨迹 | P0 | 轨迹里能看到 offered / chosen |
| FR-4 | 四级终止判据：重复调用、无进展、预算耗尽、证据完备 | P0 | 20 条任务上可触发全部四级 |
| FR-5 | 哈希链轨迹写入，`verify` 能定位首个被篡改的位置 | P0 | 改任一字段，verify 报错并指出 seq |
| FR-6 | `replay` 离线重放网关决策序列，断言与原始轨迹一致 | P0 | 重放输出的 decision 序列 hash 相同 |
| FR-7 | 指标报告：停对/停早/停晚、误停率、调用数、Accuracy@K | P0 | 一条命令生成对照表 |
| FR-8 | 策略：工具白名单、危险工具需审批、预算上限 | P1 | 越权调用被拦截并留痕 |
| FR-9 | 结果缓存与超时重试（上游不稳定时的最低保障） | P1 | 上游超时不导致整轮失败 |
| FR-10 | 离线经验层：轨迹 → wiki 教训页 → 冻结 `evidence.json` | P2 | 冻结产物可被网关加载 |
| FR-11 | 人审 UI / 图形看板 | P3 | 不做 |

### 1.2 非功能需求

| 项 | 要求 | 理由 |
|---|---|---|
| 决策延迟 | p95 < 20ms（不含上游耗时） | 网关不能在关键路径上明显拖慢 |
| 确定性 | 同一输入序列下，决策函数的输出完全一致 | 可重放的前提 |
| 可检测篡改 | 修改任一字段必被检出 | "可验证"的最低定义 |
| 依赖 | 单机运行，除 `mcp` / `markdown` 外零重依赖 | 降低演示与环境成本 |
| 可观测 | 结构化日志（JSONL），与轨迹分离 | 日志给人看，轨迹做证据 |
| 幂等 | 同一 `tools/call` 的重复投递不产生两次副作用 | 上游重试时必要 |

### 1.3 非目标（明确不做）

- 鉴权、多租户、RBAC —— 这是"有人做过"的部分，只写进 README 的未来工作
- 分布式部署、高可用、多副本
- 支持 SSE / streamable-http 全协议 —— 只做 stdio 透传
- 向量数据库 —— 工具数量 < 100，BM25 足够且更可解释
- 图形界面 —— 终端表格 + JSON 足够
- 用 LLM 判定任务是否完成 / 是否该停 —— **一旦引入，整个可验证性叙事崩塌**

### 1.4 交付物（Definition of Done）

1. 20 条任务定义（含程序化 checker），其中 ≥6 条是"必然打转"的对抗任务
2. 基线（直连上游）vs 网关的对照实验表：调用数、成本、误停率、Accuracy@K
3. `verify` / `replay` 两个命令可运行，输出可读
4. 3 分钟录屏：篡改轨迹被检出 + 一个 11 步任务被压到 3 步
5. README 含指标口径定义与已知局限

---

## 2. 架构

### 2.1 进程与拓扑

单进程，双角色：

```
Agent (LLM client)
   │  stdio (MCP)
   ▼
[ Gateway 进程 ]
   ├── MCP server 侧：对外暴露 tools/list, tools/call
   ├── MCP client 池：对内连接 N 个上游 server
   └── 旁路：trace.jsonl 写入 + 冻结配置加载
   │  stdio / http
   ▼
上游 MCP servers（github / sqlite / wiki）
```

关键约束：**Agent 只能看到网关这一个端点**。这是"能截断"和"能取证"的物理前提——不经过卡点的调用无法被治理。

### 2.2 模块与目录

```
mcp-gateway/
├── gateway/
│   ├── __main__.py      # stdio 入口，装配依赖
│   ├── transport.py     # MCP server 侧：list / call 的分发
│   ├── upstream.py      # MCP client 池：连接、健康检查、超时、重试
│   ├── registry.py      # 工具聚合、命名空间、schema 归一与冲突检测
│   ├── selector.py      # 工具准入（算法 A1）
│   ├── stop.py          # 终止判定（算法 A2）
│   ├── evidence.py      # 证据集建模与抽取（算法 A3）
│   ├── trace.py         # 哈希链写入 + verify（算法 A4）
│   ├── replay.py        # 确定性重放（算法 A5）
│   ├── policy.py        # 白名单 / 预算 / 审批 / 脱敏
│   └── metrics.py       # 指标计算（算法 A6）
├── mcp_servers/
│   ├── sqlite_server.py # 自写上游：真实可写的第二个 server
│   └── wiki_server.py   # 上游：wiki.search / wiki.read（异构工具样本）
├── tasks/               # 20 条任务定义 YAML + checker 代码
├── config/
│   ├── gateway.yaml     # 上游列表、K、预算、白名单
│   └── evidence.json    # 冻结的证据模型（由离线层产出）
├── traces/              # 运行产物
├── scripts/
│   ├── experiment.py    # 基线/网关对照
│   └── freeze_evidence.py
└── DESIGN.md
```

### 2.3 数据模型

```text
ToolRecord
  server          str           # 上游标识
  raw_name        str           # 上游原始工具名
  ns_name         str           # 命名空间后：server__raw_name
  schema_hash     str           # schema 规范化后的哈希（版本漂移检测）
  doc             str           # 检索用文档：name + description + tags
  danger          bool          # 是否不可逆动作

TraceRecord（一行一个 JSON）
  seq             int
  run_id          str
  turn            int           # 第几轮 LLM 调用
  offered         [str]         # 本轮暴露给模型的工具（必录，否则选择不可评估）
  chosen          str | null
  args_hash       str           # 归一化参数哈希（算法 A2.1）
  result_digest   str           # 上游结果摘要（不存全文，避免泄密）
  evidence_added  [str]         # 本次调用新增的证据类型
  stop_check      {rule, hit, evidence[], missing[]}
  decision        str           # forward | block_duplicate | halt_complete | ...
  latency_ms      int
  prev_hash       str
  hash            str
```

```text
EvidenceSpec（冻结产物，来自离线层）
  task_family     str
  required        [ev_type]
  optional        [ev_type]
  done_predicate  str          # 可执行的判定表达式名，非自然语言
```

### 2.4 一次 tools/call 的生命周期

1. 收到 `tools/call(name, args)`
2. **策略检查**：白名单 / 预算 / 危险工具 → 不通过则拦截并记录 `decision=block_policy`
3. **前置守卫**：重复调用 / 无进展预检 → 命中则直接返回上次结果，不触达上游
4. 转发上游（超时 + 重试 + 缓存）
5. 结果归一 → 抽取证据增量 → 更新证据集
6. **后置判定**：证据完备？预算耗尽？→ 产出 stop 断言
7. 写 TraceRecord（含哈希）→ 返回结果 + 断言

顺序不可交换：安全在最先，主动收束在被动兜底之前。

### 2.5 单次运行的状态机

```
RUNNING
  ├─ required ⊆ evidence        → HALTED_COMPLETE     （主动收束，最理想）
  ├─ dup(tool,args) 命中         → HALTED_DUPLICATE
  ├─ w 轮无进展                  → HALTED_NO_PROGRESS
  ├─ 预算耗尽                    → HALTED_BUDGET
  ├─ danger 工具                 → WAITING_APPROVAL
  └─ 证据不可得（策略拒绝）      → DEGRADED（继续，但断言里标 missing）
```

`DEGRADED` 是必要设计：否则某一类证据永远取不到时，任务会跑到预算上限才停，误判为"停晚了"。

---

## 3. 算法设计

### A1 工具准入（Tool Gating）

**问题**：|T| ≈ 30–60 个工具，全部暴露给模型会稀释注意力、抬高选错率、浪费 context。每轮应只展示 K 个（K=5）。

**输入**：当前轮上下文 q（最近 2 轮消息 + 任务描述）、工具库 T、上一轮 offered `S_{t-1}`
**输出**：`S_t`，|S_t| = K，并写入轨迹

**步骤**：

1. **召回**：BM25(q, doc(t)) 对每个工具打分，取 top-M，M = 2K
2. **硬过滤**：去掉 policy 不允许的、必需参数在当前上下文无法满足的（如缺 repo 名却要求 repo 参数）
3. **稳定性合并**：`S_t = topK(score) ∪ (S_{t-1} ∩ topM)`
4. **兜底**：q 为空或冷启动 → 返回全量（K=|T|），并在轨迹标记 `cold_start=true`

**为什么要有第 3 步**：工具集合轮间抖动会让模型行为不稳定——上一轮能用的工具这轮突然消失，模型会改道重试。稳定性合并把 offered set 的变更率压下来，这是实测出来的工程细节，不是理论洁癖。

**复杂度**：O(|T| · L)，|T| < 100，L 为文档平均长度；实测 < 5ms，无需索引优化。

**评估**：Accuracy@K 对 K 的曲线。K 从 30 降到 5 若准确率不掉，即为"省 context 不损选择"的实证。

---

### A2 终止判定（Stop Controller）

**问题**：何时可以安全地不再调工具。要求：确定性、O(1)、可解释、可断言。

**形式化**：这不是全局规划，而是**每步的守卫（per-step guard）**。每步计算状态

```
S_t = (tool_t, args_hash_t, result_digest_t, Evidence_t, budget_used_t)
```

按优先级级联执行规则，首个命中者即为决策。

**优先级：R5 > R1 > R4 > R2 > R3**

| 规则 | 条件 | 决策 | 优先级理由 |
|---|---|---|---|
| R5 危险动作 | `tool ∈ dangerous` | WAITING_APPROVAL | 安全绝对优先，且必须在"证据完备"之前判，否则可能带着未授权动作收束 |
| R1 重复调用 | ∃i<t: `tool_i=tool_t ∧ args_hash_i=args_hash_t` | HALT_DUPLICATE，回填上次结果 | 零成本可判，先砍掉最明显的浪费 |
| R4 证据完备 | `required ⊆ Evidence_t` | HALT_COMPLETE | 主动收束，必须早于 R2，否则"本可以早停"会被误记成"打转" |
| R2 无进展 | `state_hash` 连续 w=2 轮相同 | HALT_NO_PROGRESS | 被动兜底，抓伪进展 |
| R3 预算 | calls > C_max ∨ tokens > T_max ∨ wall > W_max | HALT_BUDGET | 最后一道保险 |

**R2 的状态哈希**：

```
state_hash_t = H( sorted(Evidence_t) ‖ last3(tool_i, args_hash_i) )
```

只盯"新增证据"会漏掉一类伪进展：换工具、换参数、结果无新增（比如换个关键词再搜一遍）。把最近三次调用的签名并入哈希，这类行为会被正确识别为无进展。

**为什么是 w=2**：w=1 会把"先查元数据、再取内容"的正常两步误判为停滞；w=3 则多浪费一轮。这个参数要在 20 条任务上标定，并在 README 里写明标定依据——**参数有出处**这件事本身是加分项。

**复杂度**：每步 O(1)（重复检测用 args_hash 的哈希集合，O(1) 查表）。

---

### A2.1 参数归一化（决定成败的细节）

**问题**：直接 `hash(tool + json(args))`，R1 和 R2 永远不会触发——因为参数里几乎总是带 `timestamp`、`page`、`nonce`、浮点尾差、键序不稳定。

**归一化流水线**：

1. 键排序，canonical JSON（无空格、UTF-8）
2. 按 schema 丢弃 volatile 字段：`format: date-time` 且值来自当前时间的、名为 `nonce/uuid/request_id` 的
3. 数值截断：float `round(x, 6)`；过大的整数 id 保留（id 通常是语义的一部分）
4. 字符串：`trim`；枚举/工具名做 `lower`；路径做 `normpath`
5. `args_hash = blake3(canonical_bytes)`

**边界**：分页游标（cursor）是否算 volatile？取决于语义——若游标代表"不同数据"，不算；若是上一步返回的、等价重查，算。默认不算（保守，宁可漏判重复，不可误判）。

这条如果做错，整个项目的核心数字会变成 0，但表面看不出任何报错。

---

### A3 证据模型（Evidence Set）

**问题**：如何确定性地判断"任务已完成"。**不允许用 LLM 判断**——那是用不确定的东西校验不确定的东西，可验证性当场失效。

**模型**：每个任务族一份 `EvidenceSpec`，`required` 是"完成所需的事实类型"集合，如：

| 任务族 | required | 说明 |
|---|---|---|
| 仓库调研 | `repo.list`, `file.content`, `commit.list` | 要看结构、要读实现、要看演进 |
| 数据查询 | `sql.schema`, `sql.rows` | 先有表结构再有数据 |
| 知识检索 | `wiki.page` | — |

**抽取方式**（二选一，都用确定性方法）：

- (a) **静态声明**：工具 schema 的出参映射到 ev_type，写在配置里。20 条任务规模下手写最划算。
- (b) **规则抽取**：结果 JSON 的字段签名匹配（如出现 `rows` 且非空 → `sql.rows`）。

**判定**：`required ⊆ Evidence_t` → complete。

**误差处理**：
- 某项证据被 policy 永久拒绝 → 进入 `DEGRADED`，断言里列 `missing`，不阻塞收束
- 结果为空但调用成功 → 是否算"获得证据"要区分：`sql.rows` 空集仍是"查过了"的证据，计入；`file.content` 取不到则不计入

**复杂度**：集合运算 O(|required|)，可忽略。

---

### A4 轨迹哈希链（Verifiability 的实现）

**写入**：

```
hash_i = H( hash_{i-1} ‖ canonical(TraceRecord_i \ {hash}) )
hash_0 = H(run_id)
```

**verify**：从头重算全部哈希，首个不匹配处即被篡改或被插入/删除的位置。O(n)，n < 1000，无需 Merkle。

**三级保证**：

| 层级 | 保证 | 手段 |
|---|---|---|
| 完整性 | 没有隐藏或删除的调用 | 哈希链 + 链尾哈希对外发布 |
| 可重放 | 决策序列可复现 | 冻结的规则版本 + 记录 decision |
| 可判定 | "选错没选错"可离线算 | 必须记录 offered / chosen 这一对 |

**边界（要主动说清）**：哈希链只能证明"记录未被人为修改"，不能证明"上游返回的就是真实结果"。要补这一层需要上游签名，超出本项目范围，写进局限。

---

### A5 确定性重放（Replay）

**关键定义**：重放**不重跑 LLM**，只重跑网关的决策函数。

输入：原始 `trace.jsonl` + 冻结的 `evidence.json` + 规则版本号 + 上游响应缓存
过程：按 seq 逐条喂给决策函数，比对每条产生的 `decision` 与原始记录
输出：`decision_sequence_hash`，与原始轨迹比对；一致即可证明"停在这里不是因为运气"

这样设计才站得住：LLM 采样本身不可复现，但**网关的决策必须可复现**——而"该不该停"恰恰是网关的决策，不是模型的。

---

### A6 指标计算

| 指标 | 定义 |
|---|---|
| 停对了 | 网关步数 ∈ [最优−1, 最优+1] 且 checker 通过 |
| 停早了（误停） | 步数 < 最优−1 或 checker 失败 |
| 停晚了 | 步数 > 最优+1 或触发预算上限 |
| 误停率 | 停早了 / 总任务数（**必须报**，否则数据不可信） |
| 省调用 | (baseline_calls − gw_calls) / baseline_calls |
| Accuracy@K | 正确工具落在 offered set 中的比例 |

「最优步数」由人工标注（20 条任务约 1 小时），是这套指标的基准线。

**统计诚实**：20 条样本不足以做显著性检验，只报配对差值与均值，不报 p 值。面试里主动说明这一点，比硬拗显著性可信。

---

## 4. 关键权衡（面试必问）

| 问题 | 回答 |
|---|---|
| 为什么不用向量检索工具？ | |T| < 100，BM25 + 规则更可解释、零依赖、确定性；向量检索在这里是过度工程 |
| 为什么不用 LLM 判停？ | 会让"可验证"这条主线失效。判据必须能写成断言并重放 |
| 和 LangGraph / 现成编排框架什么关系？ | 框架解决编排，本项目研究的是**截断与取证**，两者正交，可以叠在框架之下 |
| 为什么哈希链不上 Merkle？ | n < 1000，O(n) 校验足够；Merkle 的收益在全量证明场景，这里用不上 |
| 20 条任务太少？ | 承认。目标是证明机制可行并给出方向性数字，不是做 benchmark 排行榜 |

---

## 5. 里程碑

| 阶段 | 内容 | 可演示 |
|---|---|---|
| W1 / D1–D4 | 代理透传 + 命名空间 + 哈希链 + verify | 篡改一条记录被检出 |
| W1 / D5–D8 | 四级判据 + 证据集 + 参数归一化 | 对抗任务从 11 步压到 3 步 |
| W2 / D9–D12 | 20 条任务 + 对照脚本 + 指标表 | 一张数字表 |
| W2 / D13–D16 | BM25 准入 + wiki server + README | Accuracy@K 不掉 |
| 缓冲 | 只修 bug | 3 分钟录屏 |

**止损线**：第 8 天若 stop controller 跑不通，降级为"可验证轨迹网关"（哈希链 + 重放 + offered 记录 + 篡改校验），把"停"作为已识别问题写进 roadmap。完整的小项目优于跑不起来的宏大项目。

---

## 6. 已知局限

1. 哈希链保证记录完整性，不保证上游结果真实性（需上游签名）
2. 证据模型需按任务族手工建模，跨域迁移成本高
3. w=2、K=5、C_max 等参数在 20 条任务上标定，泛化性未验证
4. 单机单用户，多 Agent 并发下 offered set 与证据集需要按会话隔离
5. 工具 schema 漂移（上游升级）会让 `schema_hash` 变化，需要显式告警而非静默继续
