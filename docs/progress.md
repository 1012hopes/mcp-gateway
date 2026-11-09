# 进度日志

每天一行，格式：`D<n> | 状态 | 实测 | 明日`。这不是形式主义——项目主张是"行为可追溯"，开发过程用同一套做法。

| 日 | 状态 | 实测 | 下一个 |
|---|---|---|---|
| D1（提前于 11-09 完成） | 完成 | `canonical.py` + `record.py`，37 tests 通过；哈希覆盖未知字段、浮点与键序归一、`-0.0` 折叠、NaN 拒绝 | D2 |
| D2（同日完成） | 完成 | `sink.py`（fail-closed，写失败回滚不留半行）+ `verify.py`（L1 先于 L2，报首个异常）+ `cli.py`；74 tests；样例 12 步轨迹 + 6 种篡改演示全检出 | D3 已并入 D2，接 D4 透传与 G1 |
| D4（11-09） | 完成 | 官方 SDK 2.3 透传 + `sqlite_server` 自写上游；真实运行 5 次调用产出 5 条带 `offered` 的记录并 verify 通过；fail-closed 三段演示全 PASS；94 tests（14 条需 SDK，裸解释器 skip） | D5：多上游 + 注册表 + schema 归一 |
| D5（11-09） | 完成 | `registry.py`（归一 / 命名 / 哈希 / 漂移）+ `dirty_schema_server`；两上游 10 个工具并存，`sqlite.query` 与 `dirty.query` 互不遮蔽；`freeze_schema.py --check` OK；136 tests | D6：生命周期接线 + 轨迹落盘 |
| D6（11-09） | 完成 | `stop/controller.py`（排序 / 短路 / `attempted`，零规则）+ 七步生命周期 + `agent/`（闭环策略 + 循环）；一条真实任务 9 步、9 条记录、`verify` OK；182 tests | D7：参数归一化 + R1 + R3 |
| D7（11-09） | 完成 | `stop/normalize.py` + `rules/duplicate.py` + `rules/budget.py`；同一任务 **9 步 → 5 步**：R1 在第 5 步命中并回填第 3 步的结果（`result_digest` 相等）；`--max-calls 3` 的 run 由 R3 在第 3 步收束；241 tests | D8：R2 无进展 + 规则注册表完整化 |
| D8（11-09） | 完成 | `rules/no_progress.py` + `stop_check.state_hash`（schema v1→v2）+ 对抗任务 `t02`；**G2：11 步 → 3 步**，R2 在第二个相同答案处收束，基线 11 步全程无逐字重复（证明不是 R1 抓的）；271 tests | D9：证据模型 + R4 + DEGRADED |
| D9（11-09） | 完成 | `gateway/evidence.py` + `config/evidence.json` + `rules/evidence_complete.py`（R4）+ `rules/unreachable.py`（R6，`DEGRADED`）；t01 **9 步 → 4 步**，R4 在最后一项证据到达处收束，checker 独立复核交付结果；缺生产者的配置 **1 步**收束、零上游调用；331 tests | D10：run 边界 + 脱敏顺序 + 串行化 |

## D1 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| `TraceRecord` 含 2.3 全部字段 + `schema_version` | 20 个 payload 字段 + `hash`，有测试锁住字段名与顺序 |
| 同一对象序列化两次哈希相同 | `test_compute_hash_is_idempotent` |
| 键序归一后哈希不变 | `test_dict_insertion_order_does_not_matter` |
| 浮点归一后哈希不变 | `test_rounding_makes_near_equal_floats_hash_equal` / `test_sum_of_parts_equals_whole_after_rounding` |
| 时间戳等 volatile 字段可剔除后哈希不变 | `test_drop_keys_removes_volatile_fields`（A2.1 完整流水线在 D7） |

## 与设计的两处偏差（记录，不隐藏）

1. **链哈希用 SHA-256，不是 BLAKE3**。`DESIGN` A4 只写了 `H()`，未指定算法；A2.1 的 `args_hash` 才要求 BLAKE3，那部分在 D7 实现并引入依赖。记录层保持零依赖是刻意的：证据底座不该因某个包升级而写不出记录。
2. **`stop_check` 增加 `attempted` 字段**。原 2.3 只有四个键，但 A2 要求记录"试过哪些规则"，否则无法回答"为什么不是别的规则停的"。已同步回 `DESIGN` 2.3。

## D2 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| `verify` 对完好链返回 0 | `scripts/make_sample.py` 末尾自动校验；`python -m gateway.trace verify` 退出码 0 |
| L1 完整性先于 L2 结构 | `test_missing_field_without_reseal_reads_as_integrity_break`（报 `hash_mismatch`，不是 `missing_field`）；补 seal 后才是 `missing_field` |
| 写盘失败抛错 | `test_write_failure_raises_and_leaves_no_partial_line`：fsync 抛错 → `TraceWriteError`，文件仍为空 |
| 5 处篡改全部检出且报首个 `seq` | `scripts/tamper_demo.py` 6/6（含"删除整行"报 `seq_gap`） |

## D2 的三个发现

**1. 写入失败必须回滚，不能只抛异常。** 追加模式下 `write()` 成功、`flush()` 成功、`fsync()` 失败，文件里已经有半行或整行数据，而调用方收到的是"失败"。所以 `sink` 在写前记 `tell()`，失败时 `truncate()` 回原位。这条同时意味着：**sink 一旦失败就自我毒化**，后续 append 直接抛错——链既然断了，再写也没意义。

**2. `open(path, "a+")` 的初始位置不是 EOF。** 如果不 `seek(0, SEEK_END)`，回滚会用错误的偏移量截断，把已有记录整片删掉——一个"防数据丢失"的机制反过来造成数据丢失。`test_failure_does_not_damage_records_written_before` 守这条。

**3. 哈希链挡不住"重写整条尾链"。** 改一条记录后把后续 `prev_hash` 和 `hash` 全部重算，链依然自洽；删掉尾部 N 行同样检不出。这不是实现 bug，是哈希链的固有性质，已写成 `verify` 成功时必打印的 note + `DESIGN` 局限第 7 条 + 测试 `test_rewriting_the_whole_tail_produces_a_valid_chain`。要封住需要外部锚定（链头签名 / 只追加日志 / 末条 hash 打进 git tag），不在本期范围。

**顺带一个自我提醒**：`tamper_demo.py` 第一次跑时"中间行字段"漏检——因为我把 `chosen` 改成了它本来就有的值，一个字节没变。演示脚本因此加了一条硬检查：**改完若文件字节未变，判定为"无效篡改"并计为失败**，绝不报"检出"。这正是本项目要防的那类错：不报错、但结论是假的。

## D4 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 拔掉写权限后 run 中止 | `os.chmod(path, S_IREAD)` 后网关退出码 **3**，`stderr` 含 `refusing to start`；旧链字节完全未变 |
| `trace 行数 = 已执行调用数` | 见下面的口径修正：断言改为 `results_delivered == records_written`，在真实上游 + 真实写失败下验证 |
| echo 客户端能 list + call 到 sqlite 工具 | `scripts/echo_client.py`：4 个工具（`sqlite.*`）+ 5 次调用全部成功，`verify` PASS |
| G1：改一条旧记录，指出坏在第几行 | `scripts/tamper_demo.py` 8/8 符合文档；真实轨迹上同样 8/8 |

## D4 的四个发现

**1. `trace 行数 = 已执行调用数` 这个口径本身是错的，已改成 `results_delivered == records_written`。**

写失败发生在"上游已执行、记录未落盘"之间。此时调用确实执行过，但记录必然没有——按原口径恒不成立，除非把 fail-closed 做成"先写记录再执行"（那又会记录下没发生的调用）。真正要保证的不是"行数等于调用数"，而是**没有任何结果在未被记录的情况下交付给模型**：结果被丢弃、run 中止、后续调用全拒。这三件事都在 `tests/test_failclosed.py` 里断言了。

**2. 官方 SDK 2.3 是 handler 式 API，不是 1.x 的装饰器。** `Server(on_list_tools=..., on_call_tool=...)`，`StdioServerParameters` 变成了 pydantic 模型。好处是**处理函数是普通方法**，所以 fail-closed 路径可以在进程内被测试和演示直接驱动，不必每次起子进程。这是把它写成方法而不是闭包的原因。

**3. 配置用 JSON 不用 YAML。** `gateway.yaml` 需要额外依赖，而 JSON 零依赖、可 diff、可以被机器读——三条都符合 `DESIGN` 2.7 那条"配置属于机器，散文属于 wiki"。解析隔离在 `config.py`，将来要换 YAML 只改一个文件。已同步回 `DESIGN` 2.2 / EP-1 / S1。

**4. 篡改用例在短轨迹上会退化。** 真实轨迹只有 2 条记录时，"删中间行"其实是删尾行，属于已知盲区；若照原样计分，会把"盲区"误报成"漏检"，等于用一个错的基线去评价工具。现在索引强制取"有后继"的位置，轨迹少于 4 条时显式打印告警。

## D4 追加的两条偏差

3. **`stop_check` 在 D4 恒为空**（`attempted=[]`、`hit=false`）。不是遗漏：`rules-v0` 还没有任何规则，写空比写"看似在判"诚实。字段存在性由 `test_stop_check_is_present_and_empty_at_d4` 守着，D7 接入 R1/R3 后这里必须非空。
4. **`truncated` 只按结果体积标记（>1MB），尚未真正截断。** 截断必须与证据抽取一起做，否则半份数据会被当成完整证据——那是 D9 的事。提前标 `truncated=true` 不会误导，提前截断会。

## D5 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 两上游并存、工具名无冲突 | 10 个工具、两个前缀 `{sqlite, dirty}`、名字全局唯一；同名短名 `sqlite.query` / `dirty.query` 共存（`test_same_short_name_from_two_upstreams_coexists`） |
| `schema_hash` 跨重启稳定 | 两次独立 spawn 两个上游，manifest 完全一致（`test_hashes_are_identical_across_two_process_starts`）；`scripts/freeze_schema.py --check` 报 OK |
| `$ref` / `oneOf` 归一后不为空 | `dirty.set_mode` 线上 schema 有 3 个 properties（`test_dirty_schema_reaches_the_wire_normalized`）；`dirty.query` 的两层 `$ref` 被内联、`$defs` 被剥离并记 `resolved-refs:2` |

## D5 的四个发现

**1. SDK 会先一步拒收一部分脏 schema。** 第一次跑冻结脚本直接失败：`tools.1.inputSchema.type Field required`——顶层缺 `type` 的 schema **根本发不出去**，客户端收到的是 `Handler returned an invalid result`。这修正了一个隐含假设：不是所有脏 schema 都能到达网关，stdio + 官方 SDK 这一层已经过滤掉一类。处理方式：dirty server 改成"可传输的脏"（其余五种缺陷都保留），而 registry 的这套归一逻辑仍然保留并有单测覆盖——别的 SDK、旧版本 server、直连 HTTP 场景都会把它们发出来。

**2. 归一化不是格式化，是"让工具可被调用"。** `additionalProperties: false` 且 `required` 不在 `properties` 里，严格客户端连参数都传不进去——这个工具从构造上就是不可调用的。补一条无约束的 property 属于修可达性，不是猜类型，所以 note 写 `required-not-in-properties:flag` 而不是"已修复"。

**3. `oneOf` 的合并必须区分两种信息。** 属性名是**可见性**（不合并，模型就以为没有参数），`required` 是**约束**（只能取交集，取并集就是撒谎）。类型冲突的字段保留成 `anyOf`，原始 `oneOf` 一律不动——网关本来就不做参数校验，展示层弱化一点可以，改写约束不行。

**4. 冻结是"改上游就要显式提交一次"。** `config/schema-manifest.json` 入库后，任何 schema 变化都会让对应工具从 `offered` 里消失，调用返回 `block_schema_drift` 并照常写轨迹。这是设计要的行为（历史轨迹可比性优先），但它意味着 D9 加工具时必须重跑 `freeze_schema.py`，否则会看到"工具忽然不见了"。已写进 README 的日常工作流。

## D5 追加的三条偏差

5. **`dirty_schema_server` 从 D13 提前到 D5。** D5 的判据"`$ref/oneOf` 归一后不为空"没有脏上游就无法验收；先写它省一次返工，D13 只剩 `weird_capability_server` 与 `slow_flaky_server`。
6. **`digest_of` 从 `session.py` 移到 `trace/canonical.py`。** trace 与 registry 现在都要算摘要，两份实现迟早会在浮点或键序上分叉，而摘要不一致是没人会发现的对比 bug。
7. **删掉 `config.max_tools`。** 它在 D4 只是"取前 N 个"，从未真正做过准入；真正的 K 是 D14 的 gating，届时要连同 `offered` 的子集语义一起设计。留一个"看起来在控制上下文、实际只是截断"的开关比没有更危险。

## D6 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 一次真实任务产出完整 trace | `scripts/agent_demo.py`：9 步、9 条记录，`one record per delivered call` / `seq contiguous` 全 PASS |
| 每行含 `offered`（此阶段全量） | 每行 `sorted(offered) == sorted(tools/list 的返回)`，`test_offered_is_the_full_set_at_this_stage` 守着；D14 做 gating 时这条会失败，那是要改断言的地方，不是要绕过的地方 |
| 每行含 `chosen` / `prev_hash` | `test_every_record_carries_the_fields_that_make_it_judgeable`；`chosen ∈ offered` 同时断言 |
| 证据集 / 预算上下文已挂上 | `RunSession` 持 `budget / required / evidence / history`；`--require` 可注入必需证据；`stop_context()` 是规则唯一入口 |
| 真实 Agent 循环（替代写死的调用表） | `agent/`：`ReactivePolicy` 每步只依赖上一步 payload，`test_beliefs_come_from_observations` 证明表名来自 step 1 而不是硬编码 |

## D6 的五个发现

**1. `args_hash` 不是调用身份。** 第一次跑 e2e 断言"只有一个重复 args_hash"，结果是两个：`count_rows({"table":"incidents"})` 与 `describe_table({"table":"incidents"})` 参数完全相同，摘要自然相同。R1 的条件必须比 `(tool, args_hash)` 元组——只看 args_hash 会把"换个工具问同一个对象"误判成重复调用，而那恰好是 R2 该抓的伪进展。断言已改成元组，`DESIGN` A2 的条件本来就是这两个的合取，是我写测试时差点把口径改松。

**2. R1 的"回填上次结果"与"轨迹不存 payload"直接冲突。** 轨迹里只有 `result_digest`（这是"轨迹不能变成数据副本"的代价），所以命中重复后无处可取原结果，除非在 run 内保留一份内存缓存。结论写进 `DESIGN` 2.4：回填只能来自内存缓存（run 结束即释放），不能来自轨迹；不允许驻留的结果类型则退化为"拦截并说明"，并把退化写进断言。

**3. 停止通知不能进 `result_digest`。** 通知在摘要**之后**追加。反过来做的话，同一个上游答案会因网关当时的决策不同而哈希不同，跨 run 对比当场失效——而且是静默失效。`test_notice_is_not_part_of_the_result_digest` 守这条。

**4. `rules_version` 必须由控制器生成，不能是常量。** `Gateway.__init__` 用 `controller.version` 覆盖 session 的默认值。否则插了规则还写着 `rules-v0(none)`，等于在轨迹里撒谎——而这条轨迹的全部价值就是"说的和做的一致"。

**5. 缺口要显式留着：证据抽取为空，所以 `missing` 恒不消失。** 每次 run 的 stderr 摘要都是 `"missing": ["sql.rows","sql.schema"]`。这不是 bug，是 D9 之前 R4 不可能触发的原因。把它写在这里，比让一个空实现假装抽到了证据要好——本项目反对的正是"不报错、但结论是假的"。

## D6 追加的三条偏差

8. **`stop/controller.py` 从 D8 提前到 D6。** 计划里 D6 只写"生命周期接线"，但"接线"如果不含 pre/post 两段的插槽，D7 插 R1 时必然回头改 transport。先建空控制器 + 零规则（`rules-v0(none)`），D7/D8 只加规则文件和一个元组字面量。
9. **新增 `agent/` 包（计划里没有）。** 原计划用 `scripts/echo_client.py` 的调用表充当"agent"，但那是开环的：下一步不依赖上一步结果，测不出任何"停得早/停得晚"。`agent/` 独立于 `gateway/` 是刻意的——网关要能控制**它不拥有**的 agent，放进同一个包里，这句话就变成"一个程序内部的事"。
10. **`tasks/` 先放 1 条任务（D11 才要求 20 条）。** 没有任务规格，`--require` 只能手敲，D7 的 R4 就没有验收对象。D11 只扩充文件，不改代码。

## D7 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 归一化单测覆盖 timestamp / UUID / 键序 / 浮点 / 大小写 | `tests/test_normalize.py` 25 条，每类都是**一对**断言：噪声被折叠 + 语义没被折叠 |
| R1、R3 **各至少触发 1 次** | `tests/test_agent_e2e.py` 三个真实 run：`GovernedRun` 由 R1 在第 5 步收束、`BudgetRun` 由 R3 在第 3 步收束、`BaselineRun` 一次也没命中（对照组）。命中率 > 0 是被断言的，不是被声称的 |
| 断言命中率 > 0 | 同上；`scripts/agent_demo.py` 把同一条任务跑两遍（`--no-rules` / 有规则）并自检 10 项，**9 步 → 5 步** |
| `attempted` 记录全部试过的规则 | 前 4 步 `["R1","R3"]`，被 R1 短路的那步只有 `["R1"]`——`R3` 缺席正是"post 阶段没跑到"的证据 |

## D7 的七个发现

**1. 大小写归一不能全局做，否则就是造重复。** 计划 A2.1 写的是"枚举/工具名做 lower"，落到实现上含糊：`{"table": "INCIDENTS"}` 和 `{"table": "incidents"}` 对 SQLite 是同一个请求（标识符不区分大小写），对大小写敏感的上游是两个。全局折叠等于用猜测换命中率，而误判重复会让网关**停掉一个健康的 run 并说它是重复**——那是轨迹里一条错误的决策，比漏判贵得多。所以折叠改成按工具族声明（`SPECS_BY_PREFIX`，`sqlite` 声明 `table/column`），默认不折叠。

**2. volatile 键必须按键名判定，不能按值的形状判定。** UUID 形状的值出现在 `request_id` 下是噪声，出现在 `id` 下就是"要查哪一行"。按形状丢键会把两次不同的查询并成一次。同理，时钟类键（`ts/time/now`）还要**值也像时间**才丢：`{"time": "business_hours"}` 是过滤器，不是时间戳。两条都有正反用例。

**3. 归一化一插进来就撞上循环依赖。** `session` 要用 `normalize_args` 算调用身份，`normalize` 属于 stop 层，而 `stop.controller` 又需要 `session` 的 `Budget`/`CallSignature`。绕法有两条——方法内延迟 import，或 `TYPE_CHECKING` 撒谎——两条都是把结构问题藏起来。改成把两个纯值类型下沉到叶子模块 `gateway/limits.py`，`session` 再 re-export，依赖图变成两条无环边，已有 import 一处没改。

**4. `calls_used` 必须含"正在判的这一次"。** 否则 `max_calls=3` 的 run 里，R3 的 reason 写"calls=2 达到上限 3"，而轨迹上停在第 3 条记录——读的人会以为规则早停了一步。现在语义是"这条记录算第几次调用"，和读者数的 `seq` 一致；生命周期测试里的假规则阈值也跟着从 `>= 1` 改成 `>= 2`。

**5. R1 命中之后，原结果还能还回去。** D6 记下的遗憾（轨迹不存 payload，只能"拦截并说明"）解掉了：transport 持有 run 内已交付结果的缓存（上限 64，只在记录落盘之后写入），命中重复时回填。可验证性没有损失——回填结果的 `result_digest` 与被重复那一步相等，**"这是同一份数据"是被证明的，不是被声明的**（`test_pre_halt_with_replay_returns_the_earlier_result`）。

**6. 基线必须是一条命令就能重跑的。** `--no-rules` 不是调试开关：D12 的对照表需要"没有网关时这条任务跑多少步"作分母，若只能靠回滚 commit 生成，那个数就永远过期。`scripts/agent_demo.py` 现在把两遍跑在同一个命令里，差值当场打印。

**7. 9 → 5 是 R1 的功劳，也把 R4 的缺口照出来了。** 第 4 步（拿 stars 的 enrichment 查询）跑完，`sql.schema` + `sql.rows` 其实已经齐了——**本可以在第 4 步停**。R1 只能砍掉"逐字重复"，砍不掉"再验一遍换措辞的"。那 1 步的差距就是 D8/D9 要拿走的，也是 G2 的判据。

## D7 追加的偏差

11. **`test_gateway_e2e` 改走 `--no-rules`。** D6 记的偏差 3 说"接入 R1/R3 后这里必须非空"，但那个断言的本意是"字段存在"，不是"今天装了什么规则"；让它跟着规则集变，等于每加一条规则就要改一次 D4 的测试。现在它显式跑 `--no-rules`，规则路径由 `test_agent_e2e.py` 的三个 run 负责。
12. **`args_hash_for` 增加 `tool` 参数。** 身份是 `(tool, args_hash)` 的合取，而归一化策略也按工具族选——两条理由都要求工具名进到这个函数，否则调用方迟早会在某一处漏掉 tool 而产生两套身份。
13. **归一化策略暂时写在代码里（`SPECS_BY_PREFIX`），没进配置文件。** 现在只有一个上游族需要声明折叠，加一个 `config/normalize.json` 只是把一个字面量挪到另一个文件，还得为"文件缺失/写错"设计退化路径。D13 上三个脏上游时再决定它该不该成为配置项。

## D8 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| `state_hash` 连续 w=2 轮相同即停 | `rules/no_progress.py`，priority 40，post 阶段；`window` 可配，`window=1` 直接报错（一次无新增是正常绕行） |
| 注册表接入 R1/R2/R3 | `build_controller()` 一个元组字面量；`rules_version = rules-v0(R1,R2,R3)`，控制器校验优先级唯一 |
| `stop_check.attempted` 非空 | 每次 forward 后的记录都是 `["R1","R2","R3"]`，被 R2 短路的那次是 `["R1","R2"]`——`R3` 缺席正是"没轮到它"的证据 |
| 对抗任务步数显著下降（**G2**） | `scripts/g2_demo.py`：**11 步 → 3 步**，8 项自检全 PASS；基线 11 步里**没有任何一次逐字重复**，所以这是 R2 的功劳而不是 R1 的 |

## D8 的六个发现

**1. DESIGN 初稿的 R2 状态公式方向是错的，而且可证伪。** 初稿是 `H(Evidence ‖ last3(tool, args_hash))`——把**问题**并进了状态。后果：冒进式伪进展（换个措辞再问一遍、答案一模一样）会改变 tool/args，于是在构造上永远不可能"状态不变"，而这恰好是 R2 唯一要抓的行为。用 t02 的 11 步基线做对照实验（旧公式重算 vs 新公式）：

```
seq  old: H(evidence | last3 sigs)   new: H(evidence | results)
  1  c9c172d8f1                      d15447bea5
  2  4557bb8696                      03666e90a3
  3  f8a3bb2543                      03666e90a3
 …
 11  ca6fe8ac24                      03666e90a3
old formula: steps where it would see an unchanged state: none
```

旧公式在 11 步里看到 11 个不同状态，而运行从第 2 步起就没学到任何东西。改成 `H(sorted(Evidence) ‖ set(result_digest))`：两个输入都是只增集合，所以状态**当且仅当学到新东西时**改变——衡量知识，不衡量活动量。

**2. 加字段这件事的坑不在加，在"读回来再写出去"。** v1→v2 只多一个可选键 `stop_check.state_hash`，但如果无值时输出 `"state_hash": null`，任何 v1 记录被读进来再序列化都会多一个键，字节变了、链在没人改过的地方断掉。规则：**可选键缺省即不出现**。配套两条：`__post_init__` 接受 v1/v2（旧链永远要能验），`seal()` 只允许 v2（新记录不许铸造成旧版本）；v2 的必需字段列表与 v1 相同，否则 v2 校验器会拒收每一条 v1 记录。

**3. 判定发生在写盘之前，所以状态只能"两阶段提交"。** R2 要判的是"这一次调用之后的状态"，但记录还没写。若此时就把 digest 收进 session，写失败（fail-closed）时 session 会声称知道一份轨迹里不存在的证据。做法：`session.state_hash(pending)` 算**临时值**给规则判，记录落盘成功后才 `commit_result()` 真正收下。指纹进 trace 也顺带让"平台期"成为文件里的事实：两条相邻记录的 `state_hash` 相同，一眼可见。

**4. 对抗任务不能顺手测到别的规则。** 第一版 `FUTILE_SQL` 只有 6 个改写，而任务有 11 轮，列表绕回去就产生了逐字重复——基线里出现了 R1 的靶子，这个任务就从"测 R2"变成"测 R1 顺便测 R2"。规则：**改写变体数必须大于 `max_turns`**，并加断言 `baseline makes no exact repeat`。一条测试集里的任务只能有一个考点，否则指标归属不清。

**5. R2 对 t01（正常任务）一步也没省。** 9→5 仍是 R1 的功劳，t01 的第 5 步之后每一步的答案都不同（换个排序、换个表），状态一直在变。这说明 R2 是**兜底**而非压缩器：它只在"重复得到一个答案"时动手，而"该收手了"要由 R4 判断。R4 缺失时 t01 会一直验下去——D9 的靶子。

**6. G2 过了，但过法和计划里的预想不同。** `PLAN` 写的是"11 步压到 3 步"，数字对上了；计划假设的是"3 轮循环被识破"，实际是"1 步热身 + 2 个相同答案"，第 3 步就停。数值一致不代表路径一致，记录一下，免得复盘时把巧合当设计。

## D8 追加的三条偏差

14. **`schema_version` v1 → v2（计划里没有）。** 计划把 schema 演进当成 D10 之后的事，但 R2 的判据要能被复看就必须写进记录。D1 定的规则是"新增字段前先升版本"，照做，并把三个坑写进 `DESIGN` 2.3。
15. **新增对抗任务 `t02` + `futile_search` 策略（任务集本该在 D11）。** G2 的判据需要一条"必然打转"的任务，D11 才建任务集；但没有它 D8 的 gate 无法验收，所以在 D11 之前先建一条。D11 扩到 20 条时只加文件，不改代码。
16. **`scripts/make_sample.py` 的样例轨迹升到 v2 重新生成。** 它不带 `state_hash`——那样例建模的是"没有装规则的 run"，没人算过指纹，缺省即不出现，正好演示 v2 的可选键语义。

## D9 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| `required ⊆ Evidence` 触发 | `scripts/evidence_demo.py`：t01 **9 步 → 4 步**，R4 在第 4 步（`evidence_added = ["sql.rows.repos"]`，`missing = []`）收束；12 项自检全 PASS |
| 该任务 checker 通过 | demo 里的 `check_t01_answer()` 独立于证据模型复核交付结果：2 条未解决 incident（id 2 / 4 + severity）+ 两条 repo 的星数（`maximhq/bifrost`=9100、`1012hopes/mcp-gateway`=1） |
| 证据被策略拒绝时进入 `DEGRADED` 而非跑满预算 | `DegradedRun`：只挂 stub 上游、`required = sql.rows.repos`，R6 在 **pre 阶段**收束——**1 步、零上游调用**，`state=DEGRADED`，reason 指名 `sqlite.query` 不可用 |
| 空结果仍算证据（t02 依赖） | `test_evidence.py::test_an_empty_row_set_is_evidence`；t02 的 11→3 未被 D9 改变（`SpinGovernedRun` 断言） |

## D9 的七个发现

**1. 只按结果形状抽取，会让 R4 在第 3 步给出一次假完成。** 计划的 `required = {sql.schema, sql.rows}` 在 t01 上第 3 步就满足了——那一刻只读过 incidents，星数一个都没查。判据没错、抽取也没错，错在"有些行回来了"被当成"任务要的那几行回来了"。这就是**误停**，是这套判据唯一可能造成的、比漏判更贵的伤害。修法是把 `ev_type` 从"事实类型"升级为"事实类型 + 主体"（`sql.rows.incidents` / `sql.rows.repos`），并在抽取器上加参数谓词（`args.sql contains "from repos"`）。

发现它的不是证据模型自己，是**任务级 checker**——判据无法自证。这也是为什么验收句必须是"R4 触发**且** checker 通过"，而不是"R4 触发"。

**2. 抽取必须按来源准入，不能只看形状。** `dirty.query` 返回的也是 `{"rows":[...],"row_count":1}`。不加 `producers` 门，一个 stub server 的假行集就能满足关于真库的任务。加了之后，`producers` 同时承担第二个用途：**可用性判定**（R6），于是"谁能生产"只有一个定义。

**3. 截断判定与证据抽取的先后反了（D8 遗留）。** `truncated` 原本在抽取**之后**计算，等于让抽取器读半个 body；而"半个行集算证据"恰好会得到一次假完成（`DESIGN` 2.6 坑 2 早就写了这条，代码里顺序是错的）。已改为先判截断再抽取，`truncated=True` 时抽取器返回空。

**4. `DEGRADED` 从"继续"改成"终态"是必要的，不是偷懒。** 生产者声明在配置里、可用工具集在第一次 `list_tools` 后就确定，所以"这个事实永远来不了"在第一步就是已知的。继续跑只能为一个已经成立的结论付钱，而且会把这条 run 记成 `HALTED_BUDGET`——指标表里就成了"agent 很慢"，而真实原因是"server 没部署"。分类本身就是产出。R6 因此放在 **pre** 阶段：连那一次调用都不让它发生。

**5. `StopContext` 加字段时，默认值决定了规则的可用性。** `available` / `unreachable` 都给了 `frozenset()` 默认值，所以 D6–D8 的所有既有测试一处没改就继续跑。上一个版本（`state_hash`）的经验在这里复用：**新字段先做成"缺省即无信息"，再让唯一的生产者去填**，否则每加一个规则就要动一遍所有构造点。

**6. 停止通知的追加条件从 `session.halted` 换成了 `notice_in_result`。** `DEGRADED` 是终态但不是 `HALTED_*`，用 `halted` 判断会让降级的 run 拿不到通知；而 pre 阶段被拦下的调用本来就把通知**当结果**返回，再用 `halted` 判断会追加第二次。两个 bug 方向相反，用同一个布尔量表达才不会再错。

**7. 证据原本在写盘之前就进了 session——这是自己给规则打脸。** 项目的立身之本是"session 不能声称知道轨迹证明不了的东西"，`result_digest` 为此做了两阶段提交；但证据集是直接 `add_evidence()` 的。写失败时 run 会中止，而 `stats()["evidence"]` 会报告一组**没有任何记录证明**的事实。修法是把两阶段提交扩展到证据：抽取结果先留在 transport 的 `pending_evidence` 里，判定用合并视图，落盘成功才提交。

修的过程中踩到了同一类错的第二个面：`stop_context(evidence=...)` 最初被实现成**替换**而非合并。于是第 4 步 POST 阶段规则看到的证据集只有 `{sql.rows.repos}`，`sql.schema` 和 `sql.rows.incidents` 全丢，`missing` 反而从 1 项变成 2 项——R4 在**完成的那一步**恰好不发火，run 继续跑到第 5 步被 R1 抓住。**四个测试同时红，但报的是 `'R1' != 'R4'` 和 `5 != 4`，没有一个指向 `stop_context`。** 这类 bug 的共性是：不抛错、类型对、字段在，只有结论是假的——和 `tamper_demo` 那次"改了但字节没变"是同一族。已写进 `DESIGN` A3，因为它是 A3 的约束而不是 A3 的实现细节。

## D9 追加的三条偏差

17. **`StopContext` 新增 `available` / `unreachable`（计划里没有）。** 原计划让 R4 直接读证据集就够；但"证据不可得"需要知道**可用工具面**，而规则不该去摸注册表。改为 transport 用证据模型算一次，规则只读快照——和 `state_hash` 的处理方式一致。
18. **`--require` 写错类型 → 拒绝启动（exit 2），不是降级。** 计划没写这条。若把未知类型当成"不可得"，每个任务都会在第 1 步 `DEGRADED`，而那种失败看起来像正常运行——正是 `DESIGN` 2.6 坑 4 说的"半可用的网关比拒绝启动更危险"。
19. **三个 demo 的 `--run-id` 默认值改成随机。** 反复跑同一个 demo 时，`JsonlSink` 会**续写**已有链：文件仍然 verify 通过，但里面是两次 run 的记录，断言会莫名其妙地失败。默认唯一、显式指定即自负其责。
