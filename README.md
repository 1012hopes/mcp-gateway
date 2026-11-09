# mcp-gateway

在 Agent 与多个 MCP server 之间做两件事：

1. **终止判定**——决定这个任务现在该不该继续调工具；
2. **轨迹可验证**——每条决策进哈希链，事后能重放、能被篡改检出。

一句话概括差异化：**别的网关管"谁能调"，这个管"什么时候该停"，并且能证明那次停是对的。**

> 状态：**D7 / D18**（轨迹层 + 多上游 + 生命周期 + 参数归一化 + R1 重复调用 / R3 预算已跑通；R2/R4 与证据集待做）。进度见 [`PLAN.md`](PLAN.md) 与 [`docs/progress.md`](docs/progress.md)。

## 它解决什么问题

一个 3 步就能完成的任务，基线 Agent 跑了 11 步：重复查同一个页面、换个措辞再查一遍、把已经拿到的结果重新验一遍。它不缺能力，缺的是**判定"够了"的能力**。

现有框架把这件事推给开发者填数字：

```python
max_turns = 25        # OpenAI Agents SDK
recursion_limit = 25  # LangGraph
```

这些是**保险丝**，不是**判据**。保险丝熔断的那一刻，任务已经失败了——它保证你不被账单打死，不保证你及时收手。

## 三条机制（互相使能，缺一不可）

| 机制 | 做什么 | 少了它 |
|---|---|---|
| 证据完备判定 | `required(任务族) ⊆ Evidence_t` → 收束 | 只能靠预算上限兜底，"停得早"无从谈起 |
| 记录 `offered` | 每轮实际暴露给模型的工具全集 | 工具"选错"连分母都无法定义 |
| 哈希链轨迹 | 每条决策 `prev_hash` 链接，可重放 | 只能算"这次跑得好"，不能说"停对了" |

判据是确定性的符号规则，**不让 LLM 判停**——LLM 无法自证，重放也会当场失效。

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| D1 | 记录结构 + canonical 序列化 | ✅ |
| D2 | 写入（fail-closed）+ verify 首错定位 | ✅ |
| D3 | 篡改用例（6 处 + 2 个进阶攻击） | ✅ 并入 D2 |
| D4 | 单上游透传 + 真实轨迹 + **G1 演示** | ✅ |
| D5 | 多上游 + 工具面归一 + schema 漂移冻结 | ✅ |
| D6 | 生命周期接线 + 证据/预算上下文 + Agent 循环 + 完整轨迹 | ✅ 182 tests |
| D7 | 参数归一化 + R1 重复调用 + R3 预算（**同任务 9 步 → 5 步**） | ✅ 241 tests |
| D8–D9 | R2 无进展 → R4 证据完备 + **G2 演示** | 待做 |
| D10–D12 | run 边界 + 20 条任务 + checker + **G3 指标表** | 待做 |

## 快速开始

```bash
# 轨迹层零依赖，裸解释器就能跑（需要 SDK 的用例会自动 skip）
py -3.12 -m unittest discover -s tests -t .

# 端到端：agent -> 网关 -> sqlite 上游 -> 轨迹落盘 -> verify
uv sync
uv run python scripts/echo_client.py

# D6/D7：同一条任务跑两遍（无规则 vs 有规则），对比步数并自检
uv run python scripts/agent_demo.py
uv run python scripts/agent_demo.py --max-calls 3    # 用 R3 收束而不是 R1

# G1 演示：正常运行 / 真实拔掉写权限 / 注入写盘失败
uv run python scripts/failclosed_demo.py

# 篡改演示：8 个用例，其中 1 个是文档记载的盲区
uv run python scripts/tamper_demo.py --path traces/runs/<run>.jsonl

# 工具面：冻结当前 schema 基线 / 检查上游是否改了契约
uv run python scripts/freeze_schema.py
uv run python scripts/freeze_schema.py --check
```

不带 `uv` 时用虚拟环境里的解释器：`.venv\Scripts\python.exe scripts\echo_client.py`。

单纯校验一条轨迹：

```bash
python -m gateway.trace verify traces/samples/demo-12-steps.jsonl
# [PASS] traces/samples/demo-12-steps.jsonl
#   OK: 12 条记录，链完整
#   note: 链完整只证明「已记录的部分没被改过」：删掉尾部 N 行，剩余链依然自洽；…
```

**为什么轨迹层零依赖**：它是整个项目的证据底座，不该因为某个包升级而写不出记录。`upstream.py` / `transport.py` 才依赖官方 MCP SDK（`mcp>=2.3`）。

## 一次 run 的七个步骤（D6 接线，D7 起真的有规则在里面）

| # | 做什么 | 在哪 | 出错时 |
|---|---|---|---|
| 1 | 收到 `tools/call` | `transport.handle_call_tool` | — |
| 2 | **前置守卫**：危险动作 / 重复调用 | `stop` 的 `pre` 阶段 | 不触达上游，仍写一条记录（R1 回填上次结果） |
| 3 | 转发上游（超时 + 失败归一） | `_forward` | 变成 error 结果，不中断 |
| 4 | 结果归一 → 抽取证据增量 | `session.extract_evidence`（D9） | 记 `evidence_added: []` |
| 5 | **后置判定**：证据完备 / 无进展 / 预算 | `stop` 的 `post` 阶段 | 产出 `stop_check` 断言 |
| 6 | 写记录（含 `offered/chosen/prev_hash`） | `session.record_call` | 结果丢弃、run 置 `ABORTED` |
| 7 | 交付结果（+ 停止通知） | 同上，之后 | 后续调用一律拒绝 |

**为什么分两段而不是一段**：R5/R1 的价值在于"让这次调用不发生"，必须在转发之前；R4/R2/R3 要看到新证据才能判，只能在之后。`priority` 仍是全局有序（10/20 < 30/40/50），`attempted` 是两段按执行顺序拼接的日志——"为什么不是别的规则停的"要靠它回答。

**停止怎么传达给 Agent**：结果里追加一个 `[gateway] halt | state=… | rule=… | reason=…` 文本块（`gateway/wire.py`，全项目唯一被两侧共享的模块）。这是**礼貌**；真正的约束是**后续调用一律拒绝**。只靠通知的停止是 prompt，不是控制。

**Agent 侧**（`agent/`，独立于网关的客户端）：`ReactivePolicy` 每一步都从**上一步的结果**推下一步，写完有用步骤后进入"再验一遍"阶段——它不知道什么时候该停。这不是缺陷，是 R4 要压缩的那段行为，也是指标的分母。换成真模型只需替换 `next_action`，循环、轨迹、规则都不用动。

## 判据：从 R1 开始（D7）

同一条任务，同一套策略，只差网关里有没有装规则：

```
baseline -- no rules installed
  ... 9 步 ...            stop: policy_exhausted
governed -- rules installed
  ... 5 步 ...            stop: halted   rule=R1  state=HALTED_DUPLICATE
        reason=sqlite.query with identical arguments already ran at step 3
steps: 9 -> 5  (saved 4)
```

| 规则 | priority | 阶段 | 命中后 | 为什么在这个位置 |
|---|---|---|---|---|
| R1 重复调用 | 20 | pre | `HALTED_DUPLICATE`，**回填上次结果** | 价值在于让这次调用不发生 |
| R3 预算耗尽 | 50 | post | `HALTED_BUDGET` | 最后一道保险；跑到这里说明任务没做完 |

三件值得单独说的事：

**重复不等于错误。** R1 命中后不返回错误，而是把上次的结果还给模型——数据本来就在上下文里，重发一遍比报错有用。可验证性没有因此打折：回填结果的 `result_digest` 与被重复那一步相等，所以"这是同一份数据"是**被证明**的，不是被声称的。

**"同一个调用"是 `(tool, args_hash)`，不是 `args_hash`。** `count_rows({"table":"x"})` 和 `describe_table({"table":"x"})` 参数相同、问题不同；只看摘要会把"换个工具问同一件事"误判成重复，而那恰好是无进展判定该抓的伪进展。

**归一化是判据的前提，不是格式美化。** 参数里带 `request_id`、浮点尾差或键序不同，R1 就永远不会触发——而且不报错，只让所有数字静默归零。所以 `stop/normalize.py` 的每条规则都成对存在：丢掉噪声，**同时**保住语义。两条保守原则：volatile 键**按键名**判定（UUID 形状的值在 `id` 下就是"要查哪一行"），大小写折叠**按工具族声明**（SQLite 标识符不区分大小写，别的不一定）。误判一个重复的代价是停掉一个健康的 run 并在轨迹里留下一条错决策——比漏判贵得多。

`--no-rules` 不是调试开关：D12 的对照表需要"没有网关时这条任务跑多少步"作分母，靠回滚 commit 生成的分母一定会过期。

## 工具面：归一化不是格式化，是"让工具可被调用"

对着两个上游、10 个工具跑，其中一个专门不合规（`mcp_servers/dirty_schema_server.py`）：

| 上游缺陷 | 不处理的后果 | 处理 |
|---|---|---|
| `$ref` 经 `$defs` 两层嵌套 | 模型看到 `{"$ref": ...}`，参数全靠猜 | 内联后剥离 `$defs`，记 `resolved-refs:N` |
| 参数只写在 `oneOf` 分支里 | 顶层 `properties` 为空 → 模型以为这个工具不要参数 | 合并属性名；`required` **只取交集**；原 `oneOf` 不动 |
| `additionalProperties: false` 但 `required` 不在 `properties` | 严格客户端连参数都传不进去，工具**不可调用** | 补一条无约束 property，记 `required-not-in-properties:x` |
| 非 ASCII 工具名 | 名字不合法 | 转 `_uXXXX`；原值留在 `original_name` 供调用 |
| 名字超 64 字符 | 加前缀后必超限 | 截断 + 4 位哈希后缀；**前缀不截**，否则看不出是哪个上游 |
| 2 kB description | 撑爆上下文 | 只归一空白，不截断；长度归 gating 管（D14） |

三条硬规矩：

- **哈希只覆盖模型真正看到的东西**：归一后的 schema + 归一后的 description。反过来（哈希原文、展示归一稿）会让"这条轨迹对应哪个契约"永久失配。
- **改上游 schema 就重跑 `scripts/freeze_schema.py`**：`config/schema-manifest.json` 入库即契约，hash 变了工具就从 `offered` 里消失，调用返回 `block_schema_drift` 并照常记录。
- **归一化不碰约束语义**：网关不校验参数，校验是上游的事。合并 `oneOf` 只补"名字可见性"，`required` 取交集而不是并集——取并集就是替上游撒谎。

写这套东西时撞到一件值得记的事：**顶层缺 `type` 的 schema 在官方 SDK 下根本发不出来**，客户端收到 `Handler returned an invalid result`。所以"上游能脏到什么程度"取决于传输层——registry 仍处理这种 schema（有单测），因为别的 SDK、旧版本 server、直连 HTTP 场景都会把它发出来。

## fail-closed 是唯一能挡"漏记"的东西

哈希链能证明"已记录的部分没被改"，**不能证明"一次调用都没漏"**——删掉尾巴，链依然自洽。所以只剩一条防线：

> **记录不下来，就不执行。**

D4 把它做成了可演示、可断言的行为：

| 场景 | 行为 | 断言 |
|---|---|---|
| 启动时轨迹不可写 | 退出码 3，一次调用都不发生 | 旧链字节不变，`verify` 仍通过 |
| 运行中写盘失败 | 结果丢弃、run 置 `ABORTED`、后续调用全拒 | `results_delivered == records_written` |

第二条是关键：写失败的那次调用，**模型拿不到结果**，所以不会基于"没被记录的证据"继续推理。

## 目录

```
gateway/
  __main__.py        唯一装配点：先开 sink，再连上游，最后装规则（开不了就退出）
  config.py          配置解析（{python} / {root} 替换）
  limits.py          Budget + CallSignature（纯值类型，叶子模块，避免依赖成环）
  session.py         RunSession：证据集 / 预算 / 调用历史 + 唯一写轨迹入口
  stop/controller.py 停止控制器：排序、短路、attempted 日志（**不含任何判据**）
  stop/normalize.py  参数归一化：调用身份的唯一定义处（A2.1）
  rules/duplicate.py R1 重复调用（pre，回填上次结果）
  rules/budget.py    R3 预算耗尽（post，最后一道保险）
  wire.py            停止通知的线格式——网关与 agent 唯一共享的模块
  registry.py        工具面：schema 归一、命名空间、schema_hash、漂移冻结
  transport.py       MCP server 侧：七步生命周期，记录成功后才交付结果
  upstream.py        MCP client：stdio 连接，只负责通信，不做归一
  trace/canonical.py 确定性序列化（键序、浮点、UTF-8、拒绝 NaN）
  trace/record.py    TraceRecord + StopCheck + 哈希链入口
  trace/sink.py      fail-closed 写入（写失败回滚，不留半行）
  trace/verify.py    双层校验：L1 完整性 → L2 结构，报首个异常
  trace/cli.py       verify 命令（退出码 0/1，可进 CI）
agent/               客户端：policy（决策）/ loop（驱动）/ tasks（任务规格）
mcp_servers/sqlite_server.py        只读 SQLite 上游（数据固定可复现）
mcp_servers/dirty_schema_server.py  故意不合规的上游（$ref / oneOf / 非法名）
tasks/01_unresolved_incidents.json  一条任务：目标 + 必需证据 + 轮数上限
config/gateway.json                 上游、trace 目录、模型标识
config/schema-manifest.json         冻结的工具契约（入库，改上游后重跑冻结）
scripts/echo_client.py              端到端驱动（固定调用表，D4 产物）
scripts/agent_demo.py               D7：基线 vs 受控，同一任务两遍 + 10 项自检
scripts/failclosed_demo.py          G1 的 S1/S2/S3
scripts/tamper_demo.py              8 个篡改用例
scripts/freeze_schema.py            冻结 / `--check` 检查 schema 漂移
docs/demo-g1.md                     G1 台词与录屏脚本
```

## 三个设计决定，值得单独说

**哈希覆盖字节，版本号解释语义。** `hash` 覆盖 canonical JSON 的**全部字段**，包括当前版本不认识的字段（存在 `_extra` 里原样回写）。因此加字段不会作废旧轨迹：`verify` 按记录的 `schema_version` 选校验器，只检查该版本必需字段是否存在。

**未知字段必须存活。** 旧代码读到新字段时若丢弃不写，字节就变了，链当场断——所以 `from_dict` 保留未知字段，`to_dict` 原样还回去。这条有测试守着。

**工具名先命名空间化。** 暴露给模型的是 `sqlite.query`，而不是 `query`。事后加前缀会改写全部历史 `chosen` 值，把所有跨 run 对比作废——这类改动必须第一天做。现在两个上游各有一个 `query`，这条从假设变成了每天都会跑到的东西。

## Related work

- [`p1s4/mcp-search-proxy`](https://github.com/p1s4/mcp-search-proxy) — search-first 代理 + 4 个固定工具 + 本地 BM25。**工具准入那半条路它已经走通了，本项目直接复用其思路，不重新发明。** 力气放在它没有的后半段：什么时候该停，以及怎么证明这个判断不是事后编的。
- Kong / IBM `mcp-context-forge` / bifrost — 工业级网关与注册中心，强项在接入、鉴权、故障转移、context 压缩。它们的 guardrails 判定单位是"这次调用允不允许"，不是"这个任务做完没有"。
- `max_turns` / `recursion_limit` — 硬上限，见上文。

## 已知局限

- 证据模型需按任务族手工建模，**不可泛化到开放任务**（`DESIGN` 假设 A2）
- 哈希链保证记录完整，**不保证上游结果真实**（需上游签名）
- **链只能检出局部改动**：有写权限者可把改后记录的后续 `prev_hash`/`hash` 全部重算，链依然自洽；删掉尾部 N 行同样检不出。要封住这两个口子需外部锚定（链头签名、只追加日志、把末条 `hash` 打进 git tag）。`verify` 成功时会打印这条限制，不让"链完整"被读成"无遗漏"
- **中途写失败的那次调用已经执行过了**：结果不会交给模型，但上游副作用无法回滚。所以只读工具可以放心重试，写操作需要上游侧幂等（`DESIGN` FR-17）
- 工具返回内容会进模型上下文，存在提示注入面，只能约束"结果不解析为控制流"
- **schema 漂移时工具直接消失**：从 `offered` 移除、调用返回 `block_schema_drift`，而不是降级继续。这是刻意的（历史轨迹可比性优先），但现场看起来像"网关坏了"，所以 `registry: schema-drift:...` 会打到 stderr
- **R1 的回填假设上游在一轮 run 内是参数的函数**：回填的是上次结果，上游若可变，回填的可能是过期数据。当前上游只读（`mode=ro`），假设成立；换可写上游时必须退化为"拦截并说明"，并把退化写进 `decision`
- 参数在 20 条任务上标定，样本量不做显著性检验，只报配对差值

## License

MIT
