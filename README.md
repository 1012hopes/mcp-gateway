# mcp-gateway

在 Agent 与多个 MCP server 之间做两件事：

1. **终止判定**——决定这个任务现在该不该继续调工具；
2. **轨迹可验证**——每条决策进哈希链，事后能重放、能被篡改检出。

一句话概括差异化：**别的网关管"谁能调"，这个管"什么时候该停"，并且能证明那次停是对的。**

> 状态：**D12 / D18**（轨迹层 + 多上游 + 生命周期 + 参数归一化 + 证据模型 + R1/R2/R3/R4/R6 + run 边界与脱敏 + 20 条任务 + checker 与 **G3 指标表**已跑通，461 tests；R5 危险动作、选择器、回放视图待做）。进度见 [`PLAN.md`](PLAN.md) 与 [`docs/progress.md`](docs/progress.md)。

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
| D8 | R2 无进展 + 规则集 R1/R2/R3 + **G2 演示**（**打转任务 11 步 → 3 步**） | ✅ 271 tests |
| D9 | 证据模型（来源 + 参数谓词 + 结果形状）+ R4 证据完备 + DEGRADED（**正常任务 9 步 → 4 步**） | ✅ 331 tests |
| D10 | run 边界声明 + 先脱敏再哈希 + `tool_calls` 串行化（**同一串调用：声明了就继续，不声明就误停**） | ✅ 384 tests |
| D11 | 20 条任务（14 生产 + 6 对抗）+ 人工最优步数 + 扫描表（**182 次调用 → 65 次，20/20 达标**） | ✅ 430 tests |
| D12 | checker（20 条 + 40 个样本自测）+ **G3 指标表**（**误停率 0/20、20/20 停对、省 64.3% 调用**） | ✅ 461 tests |
| D13 | 三个脏上游（脏 schema / 脏协议 / 慢且抖）+ 兼容性自检（**31/31，一次超时只损失一次调用**） | ✅ 468 tests |

## 快速开始

```bash
# 全量测试必须在虚拟环境里跑：需要 SDK 的用例会在裸解释器上 skip，
# 但 `tests/test_agent_e2e.py` 的收集阶段就会因缺少 SDK 而失败。
uv run python -m unittest discover -s tests -t .

# 端到端：agent -> 网关 -> sqlite 上游 -> 轨迹落盘 -> verify
uv sync
uv run python scripts/echo_client.py

# D6/D7：同一条任务跑两遍（无规则 vs 有规则），对比步数并自检
uv run python scripts/agent_demo.py
uv run python scripts/agent_demo.py --max-calls 3    # 用 R3 收束而不是 R4

# D9：R4 在证据齐备处收束 + 独立检查答案 + 一个不可能完成的任务降级
uv run python scripts/evidence_demo.py

# G2 演示：一条必然打转的任务，11 步压到 3 步，平台期在轨迹里可见
uv run python scripts/g2_demo.py

# D10：同一串调用，声明了 run 边界就继续，不声明就误停（两个进程对照）
uv run python scripts/boundary_demo.py

# D11：20 条任务 × (baseline, governed) 全跑一遍，写出 results/tasks.md
#      任一条达不到 baseline >= optimal × 3 就以非零退出——任务集不能悄悄漂移
uv run python scripts/task_sweep.py

# D12：40 个样本自检（20 接受 + 20 拒绝），顺带重新生成 tests/fixtures/
uv run python scripts/make_fixtures.py

# D12/G3：对照实验 —— 20 条任务各跑两遍，产出 results/table.md（六项指标）
uv run python scripts/experiment.py

# D13：四个上游（1 干净 + 3 故意脏）挂在一个网关上，31 项自检
#      脏 schema / 脏协议（多块正文、null、非标错误码、list_changed 连发）
#      / 慢且抖（超时、空结果、1.2 MB）——不崩，且证据抽取与干净上游一致
uv run python scripts/compat_demo.py

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

## 一次 run 的八个步骤

| # | 做什么 | 在哪 | 出错时 |
|---|---|---|---|
| 0 | **run 边界**：`_meta.gateway.task` 变了就收束本 run、开新 run | `gateway/boundary.py` | 声明写错 → 该调用被拒但留痕，run 继续 |
| 1 | 收到 `tools/call` | `transport.handle_call_tool` | — |
| 2 | **前置守卫**：危险动作 / 重复调用 / 证据不可得 | `stop` 的 `pre` 阶段 | 不触达上游，仍写一条记录（R1 回填上次结果，R6 直接 DEGRADED） |
| 3 | 转发上游（超时 + 失败归一） | `_forward` | 变成 error 结果，不中断 |
| 4 | 判截断 → 抽取证据增量 | `gateway/evidence.py` | `truncated` 的结果不算证据，记 `evidence_added: []` |
| 5 | **后置判定**：证据完备 / 无进展 / 预算 | `stop` 的 `post` 阶段 | 产出 `stop_check` 断言 |
| 6 | 写记录（含 `offered/chosen/prev_hash`） | `session.record_call` | 结果丢弃、run 置 `ABORTED` |
| 7 | 交付结果（+ 停止通知） | 同上，之后 | 后续调用一律拒绝 |

第 0 步放在最前、且在"已关闭"检查**之前**：一个已经 HALTED 的 run 必须能被新话题接管，否则边界功能只在不需要它的时候可用。

**为什么分两段而不是一段**：R5/R1/R6 的判定在转发之前就已成立，价值在于"让这次调用不发生"；R4/R2/R3 要看到新证据才能判，只能在之后。`priority` 仍是全局有序（10/20 < 30/35/40/50），`attempted` 是两段按执行顺序拼接的日志——"为什么不是别的规则停的"要靠它回答。

**并发**：`handle_call_tool` 全程持一把锁，一次只判一个调用。不是为了吞吐，而是因为处理过程跨两个 `await` 点修改会话（证据 / 历史 / 计数）——两个并发调用能在"已决定"和"已记录"之间交错，产出一条 `seq` 与实际发生顺序不符的轨迹。agent 一轮发三个调用是常态，队列对它不可见，轨迹保持线性。

**停止怎么传达给 Agent**：结果里追加一个 `[gateway] halt | state=… | rule=… | reason=…` 文本块（`gateway/wire.py`，全项目唯一被两侧共享的模块）。这是**礼貌**；真正的约束是**后续调用一律拒绝**。只靠通知的停止是 prompt，不是控制。

**Agent 侧**（`agent/`，独立于网关的客户端）：`ReactivePolicy` 每一步都从**上一步的结果**推下一步，写完有用步骤后进入"再验一遍"阶段——它不知道什么时候该停。这不是缺陷，是 R4 要压缩的那段行为，也是指标的分母。换成真模型只需替换 `next_action`，循环、轨迹、规则都不用动。

## 判据：从"别再重复"到"做完了"（R1 → R4）

同一条任务，同一套策略，只差网关里有没有装规则：

```
baseline -- no rules installed
  ... 9 步 ...            stop: policy_exhausted
governed -- rules installed
  ... 4 步 ...            stop: halted   rule=R4  state=HALTED_COMPLETE
        reason=all 3 required evidence types obtained
               (sql.rows.incidents, sql.rows.repos, sql.schema)
steps: 9 -> 4  (saved 5)
```

第 4 步是"星数查询回来了"的那一步——证据集恰好在这一刻齐了，于是网关收束，而不是等模型自己觉得够了。D7 时同一任务只能压到 5 步（R1 砍掉一次逐字重复），差的这一步就是 R4 的活。

R1 只能砍"逐字重复"。换措辞、换排序、换工具再问一遍——答案一模一样，R1 看不见，那要 R2：

```
t02: 找一个库里没有的 repo
baseline (--no-rules)                   governed
  1. sqlite.list_tables   state=d154…     1. sqlite.list_tables   state=d154…
  2. sqlite.query         state=0366…     2. sqlite.query         state=0366…
  3. sqlite.query         state=0366…     3. sqlite.query         state=0366…  <- same state
  ... 8 more identical answers ...       stop: halted  rule=R2  state=HALTED_NO_PROGRESS
 11. sqlite.query         state=0366…    steps: 11 -> 3  (saved 8)
stop: max_turns after 11 steps
```

`state` 是这一行记录的**信息指纹**（`stop_check.state_hash`）：证据集 ∪ 已交付结果的摘要集合。两个集合都只增，所以指纹**当且仅当运行学到东西时**变化。上面的平台期不是推断，是文件里的两行相等。

| 规则 | priority | 阶段 | 命中后 | 为什么在这个位置 |
|---|---|---|---|---|
| R1 重复调用 | 20 | pre | `HALTED_DUPLICATE`，**回填上次结果** | 价值在于让这次调用不发生 |
| R4 证据完备 | 30 | post | `HALTED_COMPLETE` | 唯一能说"做完了"的判据；必须早于 R2，否则"本可以早停"会被记成"打转" |
| R6 证据不可得 | 35 | pre | `DEGRADED` | 不是"停不停"，是"停不了"；必须在第一次调用之前就判 |
| R2 无进展 | 40 | post | `HALTED_NO_PROGRESS` | 要看到答案才能判"有没有学到"；兜底，不是压缩器 |
| R3 预算耗尽 | 50 | post | `HALTED_BUDGET` | 最后一道保险；跑到这里说明任务没做完 |

五个值得单独说的事：

**重复不等于错误。** R1 命中后不返回错误，而是把上次的结果还给模型——数据本来就在上下文里，重发一遍比报错有用。可验证性没有因此打折：回填结果的 `result_digest` 与被重复那一步相等，所以"这是同一份数据"是**被证明**的，不是被声称的。

**"同一个调用"是 `(tool, args_hash)`，不是 `args_hash`。** `count_rows({"table":"x"})` 和 `describe_table({"table":"x"})` 参数相同、问题不同；只看摘要会把"换个工具问同一件事"误判成重复，而那恰好是无进展判定该抓的伪进展。

**"无进展"里不能有问题。** `state_hash` 只合入**证据**和**答案摘要**，不合物 `(tool, args)`——把问题并进状态，状态就会随问题变化，"换个措辞再问一遍"在构造上永远不可能是"状态不变"，而这正是 R2 唯一要抓的东西。D8 用旧公式重算过那条 11 步基线：11 步 11 个不同状态，一次都触发不了（见 `docs/progress.md`）。

**证据完备不是"回来了一些行"。** 第一版证据模型只看结果形状，t01 因此**在第 3 步就被判完成**——那一刻只读过 incidents，星数一个都没查。这是**误停**，比漏判贵得多。修法是把 `ev_type` 从"事实类型"升级成"事实类型 + 主体"，抽取条件变成三件事的合取：

```
tool ∈ producers            谁有资格生产这个事实（dirty.query 的假行集不算）
args 命中参数谓词           问的是哪个问题（sql contains "from repos"）
result 命中某个 shape       回来的是什么形状（{"rows": [...]}，空集也算）
```

发现它的不是证据模型自己，是**任务级 checker**——判据无法自证。所以验收句必须是"R4 触发**且** checker 通过"。

**归一化是判据的前提，不是格式美化。** 参数里带 `request_id`、浮点尾差或键序不同，R1 就永远不会触发——而且不报错，只让所有数字静默归零。所以 `stop/normalize.py` 的每条规则都成对存在：丢掉噪声，**同时**保住语义。两条保守原则：volatile 键**按键名**判定（UUID 形状的值在 `id` 下就是"要查哪一行"），大小写折叠**按工具族声明**（SQLite 标识符不区分大小写，别的不一定）。误判一个重复的代价是停掉一个健康的 run 并在轨迹里留下一条错决策——比漏判贵得多。

`--no-rules` 不是调试开关：D12 的对照表需要"没有网关时这条任务跑多少步"作分母，靠回滚 commit 生成的分母一定会过期。

## 证据模型：`required ⊆ Evidence` 是怎么算出来的

证据模型是**数据**（`config/evidence.json`），解释器是**代码**（`gateway/evidence.py`）。每个 `ev_type` 声明三样东西：

```json
"sql.rows.repos": {
  "shapes": [["rows"]],
  "producers": ["sqlite.query"],
  "args": [{ "key": "sql", "op": "contains", "value": "from repos" }]
}
```

抽取是 `(payload, tool, args) → {ev_type: 生产它的工具}` 的纯函数，因此重放成立。四条边界，每条都有正反用例：

- **空行集算证据**："查过了、没有"是知识。若不算，t02 那条"这个 repo 不存在"的任务会被判成答案不完整，一路跑到轮数上限。
- **错误结果不算**：`is_error` 是"没有答案"，不是"答案是没有"。
- **截断的结果不算**：半个行集算证据就会得到假完成（`DESIGN` 2.6 坑 2）。所以顺序是**先判 `truncated` 再抽取**，不是反过来。
- **别的上游的同样形状不算**：`producers` 是准入，不是装饰。

轨迹里只存 `{ev_type: 工具}`，不存 payload——`seq` 已经说明"哪一步拿到的"，`evidence_added` 说明"这一步新增了什么"。UI 的证据进度条画的就是这两个字段，不需要另算。

**R6（`DEGRADED`）用的是同一份声明**：某个必需 `ev_type` 的**所有** producer 都不在 `offered` 里，说明这个事实永远来不了。此时在**第一次调用之前**就收束，零上游调用：

```
degraded -- the tool that produces sql.rows.repos is not available
   1. dirty.status      +-    still missing: sql.rows.repos
  stop: halted after 1 steps   rule=R6  state=DEGRADED
        reason=completion is impossible: sql.rows.repos cannot be obtained
               (… only produced by sqlite.query; unavailable: sqlite.query)
```

`DEGRADED` 是终态而不是"降级后继续"：生产者是声明过的、可用工具集第一步就确定，继续跑只能为一个已成立的结论付钱。分类本身就是产出——指标表因此能把这条 run 从"停晚了"里剔除，而不是给它记一个 24 步的假超时。

## 任务集：20 条，每条都有一个人工标的"最少几步"

任务规格是 JSON（`tasks/*.json`），一个文件同时喂给三方：agent 拿 `plan` 去执行，网关拿 `required_evidence` 判完备，实验拿 `optimal_steps` 当基准线。14 条生产型 + 6 条对抗型（库里查不到的东西，问多少次都是空）。

```json
{
  "id": "t10",
  "goal": "Which incidents are still open, and which labels does each of them carry?",
  "required_evidence": ["sql.schema", "sql.rows.incidents", "sql.rows.labels"],
  "optimal_steps": 3,
  "optimal_path": "list_tables -> query open incidents -> query labels",
  "plan": [ ... 4 次调用 ... ]
}
```

三种策略，差别只在"计划从哪来"：

| policy | 计划来源 | 用在 |
|---|---|---|
| `reactive_sqlite` | 从上次结果推导（星数查询的 `where name in (...)` 由第 3 步算出来） | t01，唯一一条结果依赖的任务 |
| `declared` | 任务文件里的 `plan`，跑完再进同一段"再验一遍"尾部 | 13 条生产型 |
| `futile_search` | 任务文件里的 `futile_sql`，换措辞/换 offset 反复问同一个不可答的问题 | 6 条对抗型 |

**为什么计划是数据而不是二十个 policy 类**：如果每条任务各配一段代码，"基线 8 步"里就混进了写计划那个人的水平。计划进数据、超出部分只有一段程序，才使"多出来的步数是同一个行为"这句话成立。

入选线是 **`baseline ≥ optimal × 3`**，达不到的任务不进集合——它测不出任何东西。实测（`scripts/task_sweep.py` → `results/tasks.md`）：

```
  t03  optimal=2  baseline=8   governed=3   R4  HALTED_COMPLETE   PASS
  t10  optimal=3  baseline=10  governed=4   R4  HALTED_COMPLETE   PASS
  t16  optimal=1  baseline=10  governed=3   R2  HALTED_NO_PROGRESS PASS
  ...
合计 182 -> 65 次调用（基线的 36%），20/20 达标
```

三条必须一起读的话：

- **受控步数 = 最优 + 1**，差的那一步是 agent 自己 `describe_table`。它不产生新事实，但也不违反任何规则——网关只在证据齐了的那次调用上收束，不做"这次看起来没用，我替你省掉"这种判断。
- **"最优"是定规则后数出来的，不是量完回填的**：一次调用必须贡献至少一项 `required_evidence` 才算必要。逐条的最少调用路径在 [`docs/optimal-steps.md`](docs/optimal-steps.md)，包括两处已知代价（一次 join 能带回两个事实但只记一类；3 倍是筛选门槛而非研究发现）。
- **对抗任务受控仍要 3 步**：一次空结果是"这次没有"，两次同样的空结果才是"再问也没用"。凭一次就断言不可答，会把"表里恰好没有"和"查询写错了"混为一谈。

## checker 与 G3：数字是从哪来的

**checker 是项目里唯一能说"答案对不对"的部件。** 证据模型证明"读过什么形状的行"，`sql.rows.repos` 并不保证星数没写错。每条任务一个纯函数（`tasks/checkers/`），输入是这次 run 交付的**全部**回答，输出 `ok` + 一句理由。

```python
def t01(answers):
    incidents = project(facts(answers, "incidents"), ("id", "repo", "severity"))
    verdict = conclude(incidents, T01_INCIDENTS, "unresolved incidents")
    if not verdict.ok:
        return verdict
    stars = {r for r in project(facts(answers, "repos"), ("name", "stars")) if r[0] in T01_STARS}
    return conclude(stars, set(T01_STARS.items()), "star counts of the affected repos")
```

一条命令出表（`scripts/experiment.py` → `results/table.md`，20 条各跑两遍）：

| 指标 | 基线（`--no-rules`） | 网关（R1/R2/R3/R4/R6） |
|---|---|---|
| 停对了（步数在区间内 **且** checker 通过） | 0/20 | **20/20** |
| 停早了（误停） | 0/20 | **0/20** |
| 停晚了 | 20/20 | 0/20 |
| **误停率**（early / 总任务数） | 0.0% | **0.0%** |
| 调用数 | 182 | 65（省 64.3%） |
| Accuracy@K † | 1.00 (n=182) | 1.00 (n=65) |

† **这个 1.00 是构造出来的**：D14 之前没有选择器，`offered` 就是全部工具，正确工具必然在其中。保留这一行是为了 FR-7 的六项齐全，脚注说明它要到 A1 落地后才有信息量——印一个恒真值当结果是本项目要防的失败。

四条口径值得单独读：

- **对抗任务的区间中心是 `optimal + 2`，宽度仍 ±1**。`optimal = 1` 是信息论下界，但任何基于重复的判据最早只能在"第二个相同答案"处发火，还要加对抗策略读表结构那一步。照字面区间算，六个按设计停止的 run 会被全部判成"停晚"——那是区间中心没对齐。
- **答错优先于步数**：checker 失败在任何长度都计为误停，否则"在正确步数上答错"会被记成停对了。表里因此单独留一列 `answer`。
- **`policy_exhausted` 计为停晚**：它是"想法用完了"，不是"被判定完成了"。
- **`good` 样本不是手写的**，是真实运行的交付（`scripts/make_fixtures.py`）。生成时当场用 checker 判一次——这一步同时成了 checker 的第一道验收，D12 现场就抓到一条任务的期望少数了一行。

自测 **40/40**：每条任务 20 个 `good`（必须接受）+ 20 个 `bad`（必须拒绝，且理由要对得上）。`bad` 全部是"声明的变异"——删一行、改一个值、编一行——不是把答案清空：一个空答案被拒说明不了任何事。

## run 边界：为什么它必须是声明的

一个对话先问"未解决的事故"，再问别的事。如果第二个问题还在同一个 run 里：

- 上一个话题的事实还在证据集里，**完备性可能在还没读任何东西之前就成立**（假完成）；
- 新话题的第一句回答恰好重复了旧话题的最后一句，信息指纹不动，**R2 会把健康的 run 停在第一步**（假无进展）。

两种结局在轨迹里长得一样，成因却完全无关，而且都**没法靠更好的启发式解决**——"话题变了"是关于**调用方意图**的事实，网关只看得到工具调用。所以它被声明：

```jsonc
// tools/call 的 _meta
{ "gateway": { "task": "t01-followup", "inherit": true, "require": ["sql.schema"] } }
```

- 只有 `task` **变化**才是边界。相同任务重复声明是 no-op——无状态客户端会把同一个声明挂在每一次请求上。
- `inherit` 只携带**事实**，不携带**要求**：`require` 缺省取进程启动时的集合。把上一 run 的要求带进新任务，等于把旧任务的完成判据交给新任务，正好是上面那个假完成。
- 边界之后**一个 run = 一个轨迹文件**。不给同一文件追加第二个 run：链的创世哈希由首条记录的 `run_id` 决定，追加进去在文件里与"上一个 run 的继续"无法区分。两者的关系由 `parent_run_id` 和记录里的 `task_id` 承担，于是"这个文件属于哪个任务、继承了谁"是可读的，不是猜的。

同一串调用，两个进程对照（`scripts/boundary_demo.py`）：

```
no declaration -- one process, two topics, same first call
  1. [ok  ] sqlite.list_tables   topic 1 opens by reading the schema
  2. [halt] sqlite.list_tables   topic 2 opens the same way
  halt: task=(undeclared) | rule=R1 state=HALTED_DUPLICATE

declared -- one process, three runs, one trace file each
  1. [ok  ] sqlite.list_tables   topic 1 opens            [task=t01]
  2. [halt] sqlite.list_tables   follow-up: already knows [task=t01-followup inherit require=['sql.schema']]
  3. [ok  ] sqlite.list_tables   new topic: relearn it    [task=t02]
  halt: task=t01-followup | rule=R4 state=HALTED_COMPLETE
```

第 2 步的 `evidence_added` 是 `[]`——它一步没查就完成了，因为事实是继承来的；第 3 步的 `evidence_added` 又是 `["sql.schema"]`，因为新话题没有继承任何东西。两件事都从轨迹上读出来，不是从脚本的意图里读出来。

**先脱敏再哈希**（同一层）：身份流水线是 `redact → normalize → digest`。判据只有一句——同一调用换 token 得到相同 hash。做少了 token 进摘要，凭据一换同一个问题就换一个身份，R1 永不命中；做多了（"长随机串都抹"）订单号、commit SHA 一起被抹，两个不同调用并成一个身份，健康 run 被误停。现有任务没有一个传凭据，所以脱敏对既有轨迹是 no-op，这条被单测钉住。

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
  session.py         RunSession：证据集 / 预算 / 调用历史 + 唯一写轨迹入口 + `next_run`
  boundary.py        run 边界声明（`_meta.gateway`）：解析、校验、**不推断**
  policy.py          脱敏：凭据键 + 凭据形态 -> 固定标记（在归一化之前）
  metrics.py         A6 的六个指标：分类规则 + 聚合（无 I/O，可单测）
  stop/controller.py 停止控制器：排序、短路、attempted 日志（**不含任何判据**）
  stop/normalize.py  参数归一化：调用身份的唯一定义处（A2.1）
  evidence.py        证据模型：来源 + 参数谓词 + 结果形状 -> ev_type（纯函数）
  rules/duplicate.py R1 重复调用（pre，回填上次结果）
  rules/evidence_complete.py R4 证据完备（post，唯一能说"做完了"的判据）
  rules/unreachable.py R6 证据不可得（pre，DEGRADED，零上游调用）
  rules/no_progress.py R2 无进展（post，信息指纹连续两次不变）
  rules/budget.py    R3 预算耗尽（post，最后一道保险）
  wire.py            停止通知的线格式——网关与 agent 唯一共享的模块
  registry.py        工具面：schema 归一、命名空间、schema_hash、漂移冻结
  transport.py       MCP server 侧：八步生命周期，记录成功后才交付结果，一次一个调用
  upstream.py        MCP client：stdio 连接，只负责通信，不做归一
  trace/canonical.py 确定性序列化（键序、浮点、UTF-8、拒绝 NaN）
  trace/record.py    TraceRecord + StopCheck + 哈希链入口
  trace/sink.py      fail-closed 写入（写失败回滚，不留半行）
  trace/verify.py    双层校验：L1 完整性 → L2 结构，报首个异常
  trace/cli.py       verify 命令（退出码 0/1，可进 CI）
agent/               客户端：policy（reactive / declared / futile）/ loop（驱动）/ tasks（任务规格）
mcp_servers/sqlite_server.py        只读 SQLite 上游（8 张表，数据固定可复现）
mcp_servers/dirty_schema_server.py  故意不合规的上游（$ref / oneOf / 非法名）
mcp_servers/weird_capability_server.py 脏协议：多块正文 / null / 非标错误码 / list_changed 连发
mcp_servers/slow_flaky_server.py    慢且抖：每 N 次超时、每 M 次空结果、单次 1.2 MB
tasks/t01_unresolved_incidents.json 正常任务：唯一一条结果依赖的（reactive）
tasks/t03..t15_*.json               13 条生产型：声明式计划 + required evidence
tasks/t02,t16..t20_*.json           6 条对抗型：查不到的东西，问多少次都是空
tasks/checkers/                     每任务一个 checker（core / productive / adversarial）
tests/fixtures/{good,bad}/          40 个样本：20 个必须接受、20 个必须拒绝
config/gateway.json                 上游、trace 目录、证据模型路径
config/gateway-dirty.json           D13 的兼容面：干净 + 三个脏上游（不并进主配置，见 progress 39）
config/evidence.json                冻结的证据模型（谁能生产、问什么、什么形状）
config/schema-manifest.json         冻结的工具契约（入库，改上游后重跑冻结）
scripts/echo_client.py              端到端驱动（固定调用表，D4 产物）
scripts/agent_demo.py               基线 vs 受控，同一任务两遍 + 10 项自检
scripts/g2_demo.py                  G2：打转任务 11 步 -> 3 步 + 8 项自检
scripts/evidence_demo.py            D9：R4 收束 + 答案检查 + DEGRADED + 12 项自检
scripts/boundary_demo.py            D10：同一串调用，声明了就继续、不声明就误停 + 12 项自检
scripts/task_sweep.py               D11：20 条任务 × (baseline, governed) -> results/tasks.md
scripts/make_fixtures.py            D12：真实运行 -> good/bad 样本 + 当场互判（40/40）
scripts/experiment.py               D12/G3：对照实验 -> results/table.md（六项指标）
scripts/compat_demo.py              D13：四个上游一个网关 + 31 项自检（证据一致性在此钉住）
scripts/failclosed_demo.py          G1 的 S1/S2/S3
scripts/tamper_demo.py              8 个篡改用例
scripts/freeze_schema.py            冻结 / `--check` 检查 schema 漂移
docs/optimal-steps.md               最优步数怎么数：规则、逐条路径、四条已知代价
results/tasks.md                    扫描表（生成物，随 commit 更新）
docs/backlog.md                     想过但判断为"现在不做"的：每条写清什么条件下值得做
docs/demo-g1.md                     G1 台词与录屏脚本
docs/demo-g2.md                     G2 台词与录屏脚本
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
- **证据证明"读过什么"，不证明"读对了什么"**：`sql.rows.incidents` 只看"问了 incidents 且回了行集"，一个写错 `where` 的查询同样满足它。R4 保证事实类型到齐，**对不对必须由任务级 checker 判**——所以验收句是"R4 触发**且** checker 通过"
- **参数谓词是字符串包含，不是 SQL 解析**：`contains "from repos"` 会被 `from repos_backup` 命中。更严需要 SQL 解析器，那会让证据模型比它审的对象更难信任；保守方向就一条——宁可漏判（停在 `max_turns`，`missing` 有记录），不可误判（假完成）
- **R6 看的是网关的准入决定，不是 agent 的意图**：生产者不在 `offered` 里就判 `DEGRADED`，这是确定的；但"agent 从来不去试那个工具"在网关眼里与健康 run 无异，只能等到轮数上限，此时靠轨迹里的 `missing` 看出来
- 哈希链保证记录完整，**不保证上游结果真实**（需上游签名）
- **链只能检出局部改动**：有写权限者可把改后记录的后续 `prev_hash`/`hash` 全部重算，链依然自洽；删掉尾部 N 行同样检不出。要封住这两个口子需外部锚定（链头签名、只追加日志、把末条 `hash` 打进 git tag）。`verify` 成功时会打印这条限制，不让"链完整"被读成"无遗漏"
- **中途写失败的那次调用已经执行过了**：结果不会交给模型，但上游副作用无法回滚。所以只读工具可以放心重试，写操作需要上游侧幂等（`DESIGN` FR-17）
- 工具返回内容会进模型上下文，存在提示注入面，只能约束"结果不解析为控制流"
- **schema 漂移时工具直接消失**：从 `offered` 移除、调用返回 `block_schema_drift`，而不是降级继续。这是刻意的（历史轨迹可比性优先），但现场看起来像"网关坏了"，所以 `registry: schema-drift:...` 会打到 stderr
- **R1 的回填假设上游在一轮 run 内是参数的函数**：回填的是上次结果，上游若可变，回填的可能是过期数据。当前上游只读（`mode=ro`），假设成立；换可写上游时必须退化为"拦截并说明"，并把退化写进 `decision`
- **R2 判"无进展"靠逐字节重复的答案**：上游若在每次响应里塞时间戳、请求 id 或随机排序，语义相同的两次查询也会得到不同摘要，R2 就再也判不出停滞。它因此是被动兜底——真正承担"该收了"的是 R4，它不依赖答案是否重复。别把 R2 的低命中率读成"任务都很健康"
- **run 边界靠调用方声明，不靠推断**：网关没有"用户说了新问题"这个信号，只有工具调用，所以边界来自 `_meta.gateway.task`。一个**不声明**的客户端永远不分叉，会退化成"所有话题共用一个 run"——那正是误停的来源。代价是接入方必须配合：这个项目的形态是"网关 + 它认识的客户端"，不是"任何 MCP 客户端插上就有用"
- **脱敏只认声明过的键名与已知凭据形态**：`{"credential": ...}` 会被隐藏，`{"blob": "..."}` 不会，除非它长得像 `ghp_` / `sk-` / `AKIA` / `Bearer`。取向是"宁可漏掉一个秘密，不可并掉两个调用"——反过来做，误停会先于泄露发生
- **串行化是进程内的一把锁**：它保证本进程的轨迹线性、证据集不竞态，但不解决多进程同时写同一 `trace_dir` 的问题（各自成链、互不干扰，但没有跨 run 的偏序）
- **受控步数天然比最优多 1**：agent 常先 `describe_table` 再写 SQL，那一步不产生新事实却必须付钱。网关只在证据齐了的那次调用上收束，不会替 agent 判断"这次看起来没用"——那正是它拒绝替 agent 做的判断。`governed = optimal + 1` 是设计结果，不是判据漏了
- **"基线 ≥ 最优 × 3"部分是任务挑选的结果**：20 条任务的基线是同一条再验尾部（重复上次查询 → count → describe → list_tables → 换 limit 再问），所以这条线筛掉的是"9 步 vs 8 步"这类测不出东西的任务，不是研究发现。真结论在两列的绝对差与"停在哪条规则上"
- **checker 只认"行集"形状的答案**：`count_rows` 返回 `{"count": 0}` 也是"没有这一行"的合理表达，但 checker 看不见它。当前所有策略都用 `sqlite.query`，换一个只用 count 的 agent，五条对抗任务的 checker 会报"从没问过"
- **checker 的期望是冻结的读数，不是从数据推出来的**：字面量表是读 seed 抄下来、再由真实运行校验的。好处是 seed 一改 good 样本立刻红；代价是它证明"与这份数据一致"，不证明"与问题意图一致"——后者只能靠人复核那几十行
- **"主题词守卫"是子串匹配**：空行集只在查询文本里出现 `ghost`/`typo`/`triage` 之类的词时才算答案。一条既不写主体也不写名字的改写会让守卫失效，所以 `tests/test_checkers.py` 遍历任务文件里每一条改写在守这件事
- **超时是一次失败的调用，不是一次重试**：超时不含"调用有没有执行"的信息，重试会让轨迹声称一次调用、实际执行两次。所以网关记失败、run 继续，要不要再问由调用方决定（完全相同的重问会被 R1 拦下并回填）。可重试需要上游声明幂等性
- **"第一个可解析的文本块"就是正文**：真 server 的横幅是散文，所以成立；把 `{"ok": true}` 放在正文前面的上游会让抽取器报告"没有证据"——一次有答案的调用被记成没答。这条被钉在 `tests/test_compat.py` 里，不是留给别人发现
- **超过 1 MB 的结果只被标记，没有被裁剪**：`truncated: true` 让证据判定拒绝它，但完整 payload 仍交给模型，上游依然能用单次结果撑爆上下文。真裁剪必须在算摘要之前切，否则 `result_digest` 与模型看到的字节不再对应（`docs/backlog.md` 第 1 条）
- **误停率的分母是 20 条任务**：一条误停就是 5%。表里报 0/20，不报"误停率 0.0% 所以没问题"——样本量只有这么大，能说的就这么多
- 参数在 20 条任务上标定，样本量不做显著性检验，只报配对差值

## License

MIT
