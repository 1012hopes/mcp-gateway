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
| D10（11-09） | 完成 | `gateway/boundary.py`（`_meta.gateway` 声明）+ `gateway/policy.py`（先脱敏再哈希）+ `session.next_run` + `handle_call_tool` 串行锁 + schema v2→v3（`task_id`）；`boundary_demo.py` **12 项自检全 PASS**：不声明 → 第二个话题被 R1 误停，声明 → 三 run 三文件、追问 `evidence_added=[]` 零重读、换题重新读；384 tests | D11：20 条任务集 + 人工最优步数 |
| D11（11-09） | 完成 | 8 张表的演示库 + 9 个 `ev_type` + `agent/policy.py` 的 `DeclaredPolicy`（计划从数据来、再验尾部是同一段程序）+ 20 条任务（14 生产 + 6 对抗）+ `task_sweep.py`；**182 → 65 次调用（36%），20/20 满足 `baseline ≥ optimal × 3`**；430 tests | D12：checker 自测 + 指标表 |
| D12（11-09） | 完成 | `tasks/checkers/`（20 条，core / productive / adversarial）+ `scripts/make_fixtures.py`（真实运行生成 40 个样本，**40/40**）+ `gateway/metrics.py` + `scripts/experiment.py` → `results/table.md`：**误停 0/20、20/20 停对、182 → 65 次调用（省 64.3%）**；461 tests | D13：三个脏上游 |
| D13（11-10） | 完成 | 三个脏上游（`dirty_schema` / `weird_capability` / `slow_flaky`）+ `config/gateway-dirty.json`（**不并进 `gateway.json`**，否则实验分母从 10 涨到 23）+ `docs/backlog.md`；`compat_demo.py` **31/31 PASS**：23 个工具名唯一、十种坏结果无一中断、"一次超时只损失一次调用"；468 tests（裸解释器 333 / 135 skipped，0 error） | D14：准入 + 异构工具 |
| D14（11-10） | 完成 | `gateway/selector.py`（BM25，`enriched` 索引，K=5、M=10、pin 占槽位不放宽）+ `mcp_servers/catalog_server.py` / `wiki_server.py` / `gh_server.py`（30 个工具）+ `agent/loop.py` 每轮重取 `tools/list`；`gating_demo.py` **16/16 PASS**：**Accuracy@5 全量 1.00 → 窗口 1.00（下降 0%）**、20/20 答案不变、`outside-window 0/65`、schema **14578 → 1920 字节/轮（省 87%）**、`pins 43/43`；`test_selector` 22 例 + `test_gating` 5 例；**495 tests**（裸解释器 355 / 140 skipped，0 error） | D15：回放视图 + README |
| D15（11-10） | 完成 | `gateway/trace/render.py`（单文件 HTML，服务端渲染，JS 只切显示）+ `python -m gateway.trace render` + `scripts/replay_demo.py`（**37/37 PASS**）+ `docs/faq.md`（25 问，第 12 问是 D16 补的：轨迹文件本身坏了会怎样）；**4 步真轨迹渲染 → 改第 3 行 → 再渲染**：只有第一条红、之后标"无法验证"、R4 在 seq=4 拿唯一强调色；顺手修正 `DECISIONS`（漏 3 个真名、错 1 个、多 1 个），加源码扫描测试；**519 tests**（裸解释器 379 / 140 skipped，0 error） | D16：缓冲期（只修 bug） |
| D16（11-10） | 完成 | 缓冲期第一天，**只修 bug、没加功能**。拿恶意输入撞模块：4000 条类型变异记录（3913 条真正改变内容，**0 谎报绿、0 崩溃**）+ 空/纯空行/BOM/CRLF/20 万字符长行/NUL/非 UTF-8。修六个真 bug：`verify` 被 `NaN` 干掉（新增 `non_canonical`）、被非 UTF-8 字节干掉（新增 `decode_trace`/`not_utf8`，带行号+字节偏移，并报出坏行之前已验证的 `records=n`）、CLI 对目录回 traceback 且退出码 1（现在 0/1/2，`tests/test_cli.py` 是这份契约的第一个测试）、选择器索引缓存按名字做键（改名不改就吃旧排名，改按语料做键 + 上限）、`MAX_RESULT_BYTES` 量的是字符数（1.2 MB 中文不标 `truncated`，改成 UTF-8 字节）、`_meta` 非对象被静默当成"没声明"（改成 `DeclarationError`）；**539 tests**（裸解释器 399 / 140 skipped，0 error），八个 demo 全绿 | D17：缓冲期（只修 bug） |
| D17（11-10） | 完成 | 缓冲期第二天，**只修 bug**。撞 D16 留下的三个方向：`trace/sink.py`（丢换行 / 半截行 / 非 UTF-8 尾巴 / 两个写者）、`stop/controller.py`（规则自己崩）、`agent/policy.py`+`tasks.py`（空计划）。修六个真 bug：**丢换行的尾部会让下一次 append 把两条记录粘成一行、静默毁掉前一条**（`_resume` 改成用真校验器跑一遍，坏尾巴一律 `TraceChainError` 且不修）、半截行/非 UTF-8 尾巴抛原始 `JSONDecodeError`/`UnicodeDecodeError`（改成带行号）、两个 sink 写同一文件重复 `seq` 无人发现（append 前比对文件大小）、`tell()` 在 `try` 外导致死句柄抛 `ValueError` 未被分类（回滚点改用 `self._size`）、**规则崩溃时被审的那次调用不留记录**（转成 `halt_rule_error` + `DEGRADED`）、`policy:"declared"` 空计划 = 零调用的"成功"且抬高"省调用"分母（加载期拒绝）；`scripts/experiment.py` 读数一字未变（`182 -> 65`、64.3%、误停 0/20），`results/table.md` 重跑**字节相同**；**556 tests**（裸解释器 415 / 141 skipped，0 error），八个 demo 全绿 | D18：缓冲期（只修 bug） |

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

## D10 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 追问继承 | `boundary_demo.py`：follow-up 声明 `inherit + require=["sql.schema"]`，第一条记录 `evidence_added=[]`、`missing=[]`、`stop_check.hit=true`——**一步没查就完成了**，事实只能来自继承；`parent_run_id` 指向父 run。`WithBoundaryTests.test_a_follow_up_inherits_and_completes_without_reading_again` 同样断言 |
| 换题重置（不误停） | 同一 demo 的第三段：`task=t02` 不带 `inherit`，`evidence_added=["sql.schema"]`（重新读过）、`parent_run_id=null`。反事实由 `WithoutBoundaryTests` 提供：同一串调用**不声明**时，第二个话题的第一句被 R1 判成重复，`state=HALTED_DUPLICATE` |
| `redact → canonical → hash` 顺序有单测守护 | `tests/test_policy.py::PipelineTests`：断言身份等于 `digest_of(normalize_args(redact(args)))` **且不等于** `digest_of(normalize_args(args))`；另有幂等、不改入参、归一化仍在脱敏之后生效、`result_digest` 同样脱敏、以及 `NoOpTests`（真实查询/行集/信封脱敏前后逐字节相同） |
| 串行化（FR-16，计划表外补的） | `test_lifecycle.py::SerializationTests`：`asyncio.gather` 三个调用 → `seq` 与 `turn` 都是 `[1,2,3]`，上游只被各调一次；另一半断言"排队不等于延后判定"——第二个调用看到第一个产生的 halt |

## D10 的五个发现

**1. `require` 缺省继承上一 run 的要求，会让换题带着旧判据。** 第一版 `_resolve_required` 在 `declaration.require is None` 时取 `current.required`。结果是：换题的新 run 继承了旧任务的 `require`，第一步就 `HALTED_COMPLETE`——**边界要防的假完成，只是换了个位置发生**。改成缺省取**进程启动时**的 `--require`。这条值得单独记，因为它说明边界的正确性不只取决于"重置了什么"，还取决于"新 run 从哪儿取默认值"。

**2. 重复声明必须是 no-op，否则无状态客户端每调一次就开一个新 run。** 无状态客户端会把同一个 `_meta` 声明挂在**每一次**请求上（它不知道网关记不记得）。所以判据只能是 `task != current_task`，不能是"声明了就是边界"。

**3. 边界之后不能往同一个文件追加。** 链的创世哈希由首条记录的 `run_id` 决定，第二个 run 追加进去，在文件里与"上一个 run 的继续"**无法区分**——而这恰是边界要消除的混淆。所以一个 run 一个文件，关系由 `parent_run_id` 与该记录的 `task_id` 承担。留了一个"空文件"的坑：如果首个调用就带声明，而 session 还没有任何记录，直接分叉会留下一个 0 记录、只能被 `verify` 判为 `empty` 的文件；改成**认领**（把声明写进当前 run）而不是分叉。

**4. 边界检查必须在"已关闭"检查之前。** 否则一个已经 HALTED 的 run 会拒掉新话题的第一次调用——边界功能就只在不需要它的时候可用。`WithBoundaryTests.test_a_declaration_is_served_even_after_the_run_halted` 守这条。

**5. 并发下 `turn` 比 `seq` 更早暴露问题。** `seq` 由 sink 的 `expected_next()` 保证连续，两个处理函数交错时它会**抛异常**（`seq out of order`），看起来像"偶发失败"；而 `turn` 是"读-加一"，没有锁时两个处理函数读到同一个值、各自加一，轨迹会声称两次调用发生在同一轮，**且不报错**。所以串行化的断言落在 `turn == [1,2,3]` 上。

## D10 追加的四条偏差

20. **边界触发方式改为协议字段 `_meta.gateway`（计划只说"session 补 reset/inherit"）。** CLI flag 不能中途改；控制工具会进 `offered`，污染 Accuracy@K 的分母；`_meta` 本来就是每请求的、且被网关剥掉不进参数。新增模块 `gateway/boundary.py` 承担解析与校验，`session.next_run` 承担分叉。
21. **schema v2 → v3（`task_id` 可选键）。** 一个 run 一个文件之后，"哪个文件属于哪个任务"若无字段只能靠文件名或时序去猜。沿用 D8 的口径：可选键**缺省即不出现**，必需字段表与 v2 相同，`seal()` 只写 v3、可读 1/2/3。`traces/samples/demo-12-steps.jsonl` 随之重新生成（字节变化 12 行）。
22. **脱敏落 `gateway/policy.py`（计划写 `policy/redact.py`），并扩展到 `result_digest`。** 目录里没有 `policy/`；`DESIGN` 2.2 原本就把"白名单 / 预算 / 审批 / 脱敏"放在 `gateway/policy.py`。FR-14 只说 `args_hash`，但两个只有返回凭据不同的答案否则永远不会看起来相同，R2 的平台期就永远形不成——同一个论证，扩到结果摘要。
23. **`agent/loop.run_task` 现在会传 `--task-id`。** 一个进程一条任务时这是"声明"，也是后续 `_meta` 变化能被判定为边界的前提；不带它，第一次声明会被当成"从空到 t01"的边界。

## D11 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 20 条全部可跑通基线 | `scripts/task_sweep.py`：20 条 × 2 遍（`--no-rules` / 默认），**40 个 run 全部落盘并退出 0**；基线各自停在 `policy_exhausted`（生产型）或 `max_turns`（对抗型），没有一条依赖"回滚 commit 才有的分母"。表在 `results/tasks.md` |
| 每条最优步数已标 | 20/20 有 `optimal_steps` 且有 `optimal_path`（最少调用路径的文字版）；规则与逐条路径在 `docs/optimal-steps.md`；`tests/test_taskset.py::test_every_task_has_a_hand_counted_optimum` 禁止缺项（缺失即"未完成"，不是"以后补"） |
| 含 6 条"必然打转"的对抗任务 | `t02`、`t16`–`t20`，覆盖三种死路：不存在的 repo（t02/t20）、不存在的标签/依赖/tag/维护者（t16–t19）、翻页越过末尾（t20）。6 条的基线都是 10–11 步、受控都是 3 步、都由 **R2** 收束 |
| 入选线 `baseline ≥ optimal × 3` | 20/20 达标，最低 3.00（t01），最高 11.00（t02）；扫描脚本对任一条不达标**以非零退出**，不让任务集悄悄漂移 |

## D11 的六个发现

**1. 计划进数据文件，超出的部分才进代码——否则"基线步数"测的是写计划的人。** 若二十条任务各配一个 policy 类，"基线 8 步"里就混进了计划者的水平，`baseline / optimal` 不再是同一个行为的度量。所以 `plan` 是 `tasks/*.json` 里的数据，而"再验一遍"的尾部（重复上次查询 → `count_rows` → `describe_table` → `list_tables` → 换 `limit` 再问）是**一段程序**，二十条任务共用。这也是唯一能诚实地写"多出来的步数是同一个行为"的写法。

**2. 尾部第一项是计划最后一项的逐字重复，于是锚点必须取"首次出现"。** `_tail_done` 靠"计划最后一动在历史里的位置"算已经验了几轮。若用**最后**一次出现，尾部第一步就把锚点往后推，`done` 永远是 0，循环卡在第一次复查上——`ReactivePolicy` 的注释里记的是同一个坑的**反面**（那里锚点不能取"看起来有用的最后一次"）。两边都说明：这个锚点不是实现细节，是这条策略能停下来的唯一原因。测试 `test_the_tail_never_stops_advancing` 钉住。

**3. 表一多，"证据类型 = 表"这个粒度立刻不够用。** `severity` 同时在 `incidents` 和 `scans` 里，只看列名会把"扫描发现的严重度"记成"事故的严重度"。所以每个 `sql.rows.*` 的 args 谓词都必须带**表名**（`contains "from scans"`），和 D9 里 `sql.rows` 必须拆成 `.incidents` / `.repos` 是同一条理由，只是这次是横向扩展暴露出来的。

**4. "重复清单必须 ≥ 轮数上限"是个静默约束。** 对抗任务的 `futile_sql` 若比 `max_turns` 短，循环会绕回、出现**逐字重复**的调用，R1 抢先命中，而任务仍然报"11 步 → 3 步"——看上去完全正常，测的却是另一条规则。单靠人眼看不出来，所以 `test_the_spin_never_wraps_around` 把它变成了断言（`len(futile_sql) >= max_turns`，且语句两两不同）。

**5. 受控步数比最优多 1，多出来的那一步是 agent 的谨慎，不是判据的漏洞。** 13 条生产型任务的计划都以 `describe_table` 开头——agent 不确定列名。它不产生新事实，但网关不会拦：钱已经付了，结果也是合法答案的一部分。网关只在**证据齐了**的那次调用上收束，不做"这次看起来没用，我替你省掉"这种判断，因为那种判断需要的正是本项目拒绝替 agent 做的东西。`governed = optimal + 1` 因此是设计结果。

**6. "3 倍"是入选门槛，不是研究发现。** 尾部是固定长度的程序，所以比值主要由"每条任务需要几个事实"决定（1 个事实 → 8 步，2 个 → 10 步）。它的作用是筛掉"9 步 vs 8 步"这类测不出东西的任务。真正的结论在两列的绝对差和"停在哪条规则上"——这两项不随任务挑选而变。

## D11 追加的五条偏差

24. **任务文件仍是 JSON，不是计划里写的 `tasks/*.yaml`。** D6 起就是 JSON（零依赖、diff 可读，同 `config.py` 的理由）。计划与实现的这处差异一直都存在，只是到 D11 才值得在计划里记一笔。
25. **"20 条任务"的实现方式是 `plan` 数据 + 公共再验尾部，不是二十个策略类。** 代价写清楚了：**计划不能依赖上一步的结果**，所以只有 t01（星数查询由 incident 行算出来）留在 `reactive_sqlite`，其余 13 条是固定计划。这条限制在 `docs/optimal-steps.md` 与 `DESIGN` 6.19 里都记了。
26. **"最优步数"补了操作定义**：一次调用必须贡献至少一项 `required_evidence` 才算必要。于是生产型最优 = `required` 条数，对抗型 = 1。不补的话"最小充分步数"只是形容词——agent 自己探结构的那一步算不算，两个人会给出两个数。
27. **6 条对抗任务覆盖三种死路，但共用同一个信号**（答案逐字节重复）。这是刻意的：给每种死路配一条专用规则，结论就变成"我认得这六种情况"，而不是"平台期本身可判"。
28. **`results/tasks.md` 作为生成物入库。** 它与 `traces/samples/` 同类：可复现、可 diff。某次改动让某条任务的步数变了，diff 里会直接看到，而不是等 D12 的指标表才发现"数字对不上"。
29. **D10 遗留缺陷：`tests/test_run_boundary.py` 在类体里构造规则。** `rules: tuple = (DuplicateCall(),)` 是类体语句，import 期就执行，早于 `skipUnless`——裸解释器上直接 `NameError: name 'DuplicateCall' is not defined`，报的还是与依赖无关的错。这和 D9 修过的 `ScriptedPolicy` 是同一个坑，只是换了个文件，说明"类体里不碰受 SDK 守卫的名字"当时只修了现场、没写进规矩。改法相同：类体只声明**名字**（`rule_names`），`setUp` 里按需实例化。裸解释器现在 **298 tests / 0 error / 132 skipped**（原 279 / 1 error）。

## D12 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| checker 自测 40/40（20 通过 + 20 拒绝） | `scripts/make_fixtures.py` 输出 `PASS: 40/40 samples (20 accepted, 20 refused)`；`tests/test_checkers.py::FixtureTests::test_all_forty_samples_behave` 逐条断言，另有 `test_every_refusal_names_the_reason_it_was_built_for` 要求拒绝理由里出现预声明的原因词 |
| 产出 `results/table.md` | `scripts/experiment.py` 一条命令写盘：六项指标 + 20 条任务的逐行明细。**误停率 0/20、20/20 停对、182 → 65 次调用（省 64.3%）**；脚本对"有误停 / 基线答错 / 无法计算 Accuracy@K"以非零退出 |

## D12 的六个发现

**1. `good` 样本必须来自真实运行，否则它证明的是"我认为该答什么"。** 计划写的是"一个必然通过的结果"（手写 JSON）。手写等于把"运行答了 X"换成"我认为它该答 X"，两者一致到第一次不一致为止。改为用真实运行的交付生成，并在生成时当场用 checker 判一次——这一步立刻变成了 checker 的第一道验收：t13 的期望里我少数了一行（`IBM/mcp-context-forge` 的 `c3d4e5f` 也是 4-25 之后），生成的 good 样本被自己的 checker 拒了。**checker 会写错，而且错得和 agent 一样自然。**

**2. checker 必须读"整次交付"，不能读最后一条答案。** `DeclaredPolicy` 的尾部会用 `limit 1` 复读同一个问题，只看最后一条等于给复读打分：t03 的 breaking 有两条，`limit 1` 只剩一条，一个正确的基线会被判成答错。所以 `facts()` 取并集。代价写在 `core.py` 的注释里：并集允许"两个截断答案拼成真相"，那是刻意选的宽松方向——如果 run 真的分两次交付了每一行，它就交付了答案。

**3. "空集不是答案"这个坑第二次出现，这次在 checker 里。** 五条对抗任务的正确答案就是空行集，而 `facts()` 在"问了没有"和"根本没问"两种情况下都返回 `[]`。照直写，一个一次都没查过的 run 会全部通过。所以加了 `touched()`（至少有一次针对该表的查询），再加主题词守卫（那次查询确实在问这个主体）——后者是因为"对另一个问题得到空集"同样会被误接受。R4 在 D9 踩的是同一个坑的正面（空 required 集是任何集合的子集），一年两次说明这不是疏忽，是这类判据的固有形状。

**4. 对抗任务的判定区间中心必须上移，而且必须写明这是偏离。** `optimal = 1` 对六条对抗任务成立，但它是**信息论**下界，不是**可判**下界：任何基于重复的判据最早只能在"第二个相同答案"处发火，还要加对抗策略读表结构那一步。照 A6 字面区间算，六个按设计停止的 run 会被全部判成"停晚"——20 条里 6 条误判，而错误出在区间而不是网关。改法只动中心、**不动宽度**（容差仍是 ±1），`optimal` 列保持下界不变，两列并排印在表里让人可以自己重算。`tests/test_metrics.py::BandTests::test_the_tolerance_stays_one_step_wide` 守着"不许用放宽容差的方式凑数字"。

**5. 六项指标里有一项现在不可能有信息量，说实话比凑数重要。** Accuracy@K 要等 D14 的选择器：现在 `offered` 就是全目录，正确工具必然在其中，算出来恒为 1.00。保留这一行是因为 FR-7 要求六项齐全，但表里带脚注说明它按构造为真。一个恒真值被当成结果是本项目全部论证要防的那种失败——`DESIGN` 1.7 的 FR-7 那行因此从"六项齐"改成了"六项齐、五项有信息量"。

**6. `bad` 样本必须是"声明的变异"，且拒绝理由要能对上。** 只断言"拒了"是不够的：一个把一切都拒掉的 checker 会通过全部 20 条 bad，而它对应的正是误停率 100%。所以每个 bad 样本带 `expect`，变异表（删一行 / 改一个值 / 编一行）与理由同处一表。另加 `test_the_bad_sample_is_not_simply_empty`：变异不能把答案清空——空答案被拒说明不了任何事。

## D12 追加的六条偏差

30. **`good` 样本是生成物，不是手写样本。** 计划（DESIGN 1.5）写"`good/`：一个必然通过的结果"，没规定谁来写。改成生成 + 当场自判，代价是它依赖一次真实运行（需要 SDK、需要库），收益是它同时成了 checker 的验收与 seed 漂移的哨兵。
31. **checker 的输入形态是"整次交付"，计划未指定。** 选它是因为尾部会复读，任何"看最后一条"的实现都会给复读打分。`core.facts()` 因此是并集语义，不是覆盖语义。
32. **A6 的对抗任务区间中心从 `optimal` 改为 `optimal + 2`**（宽度不变）。理由与代价都写进了 `DESIGN` 的 A6 与变更记录、`docs/optimal-steps.md` 第六节、`gateway/metrics.py` 的模块注释——同一件事写四处，因为它是最容易被读成"调参直到数字好看"的一处。
33. **FR-7 记为"阶段性未达标"**：六项都在表里，但 Accuracy@K 要到 D14 才有信息量。没有把它写成"已满足"，也没有从表里删掉。
34. **`tasks/checkers/` 拆成三个模块**（core / productive / adversarial）而不是一个文件。20 个检查函数 + 共享谓词 + 一个主题词表，塞进一个文件会让"哪段是接口、哪段是某条任务的期望"分不清。
35. **`facts()` 的并集语义允许"两个截断答案拼成真相"。** 这是刻意选的宽松方向，写在 `core.py` 的 docstring 里：如果 run 分两次交付了每一行，它就交付了答案；反过来取"最后一条"会让正确基线因为尾部的 `limit 1` 被判错，那是更贵的错。

## D13 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 三个 server 全挂上不崩 | `scripts/compat_demo.py`：四个上游（`sqlite` / `dirty` / `weird` / `flake`）挂在同一个 registry，23 个暴露名全部合法（`[A-Za-z0-9_.-]{1,64}`）且唯一；`weird` 的十种坏结果（多块正文 / `null` / 空 content / 半截 JSON / 裸数字 / 错误标记带行集 / 字符串错误码 / 直接抛异常 / 五次 `list_changed`）没有一种让运行中断；`flake` 五次查询里恰好一次超时，run 仍是 `RUNNING`，下一次调用仍成功。**31/31 PASS** |
| 脏上游的证据抽取结果与干净上游一致 | 拆成两句断言（原句含混，见偏差 37）：**信封不改变事实**——同一 body 的六种封装（单块 / 横幅+正文+尾注 / 显式 `is_error:false` / 空 `structuredContent` / CRLF / 多余键）抽出同一个 `{sql.rows.labels}`；**不是正文的正文不产生事实**——五种伪正文各抽出 `{}` 且不抛异常。另用假上游把 tool 与 args 钉死，只改字节数：200 B 有证据、1.2 MB 标记 `truncated` 且无证据（`tests/test_compat.py`） |

## D13 的五个发现

**1. 一次超时会不会毁掉整个上游，取决于上游怎么睡。** 第一版 `slow_flaky_server` 用 `time.sleep(1.5)` 制造慢响应，实测结果是**之后每一次调用都超时**（含很快的 `stats`）。原因不在网关：同步 sleep 阻塞了 server 自己的事件循环，之后到达的请求根本没被读。换成 `await anyio.sleep` 后，慢 handler 与其它请求并发，"一次超时只损失一次调用"才成立。**这条差一点被记成"传输层被一次超时毒化"，那个结论会指向一个不存在的 bug（重连），而真因在上游的实现方式。**

**2. 超时不是"没送到"，所以重试不成立。** 计划写"超时重试"。但超时只说明答复没在期限内到达，**不说明调用没执行**——重试会让轨迹声称一次调用、实际执行两次，正是本项目禁止的那处空隙；对非只读的上游更是语义错误。改为记成一次 `forward` 的失败调用，run 继续，下一条调用照常打到同一个上游。要重试，得先有幂等性声明（`docs/backlog.md` 第 3 条）。

**3. 判据"证据抽取与干净上游一致"必须拆成两句，否则第一句自己就能被空实现通过。** "信封噪声不改变结果"在"抽取器什么都不抽"时也成立——所以每一组都要配一组伪正文（错误标记带行集、`null`、空 content、半截 JSON、裸数字）断言 `{}`。这和 D9 的"判据不能自证"是同一件事的第三次出现：**一个只会说"不"的判据和一个正确的判据，在只测正例时长得一样。**

**4. 用真的脏上游测截断，测的是另一条规则。** `flake.huge` 的 tool 是 `flake.huge`，而 `sql.rows.labels` 的生产者只有 `sqlite.query`——"没有证据"会被 producers 规则解释掉，与截断无关。所以截断的验证用假上游把工具名钉在 `sqlite.query`、只改字节数，并顺手断言两条记录的 `chosen` / `args_hash` 相同：**"只差大小"必须是断言，不能是旁白。** 这与 D9 修 R4 时"三输入缺一不可"是同一类错误：**把两个原因中的一个当成全部。**

**5. 脏服务端的验收里，"不崩"和"不装死"是两件事。** `boom` 抛异常 → 客户端拿到 `MCPError` → 网关记成一次失败的 `forward`，这是"不崩"。但 `noisy` 的五次 `list_changed` 若不处理，表现是"表面没变"或"表在 run 中途变了"——两种都不报错。判据落在"通知之后重新 `tools/list`，`offered` 逐项相同"：**一次 run 的工具面是冻结的**（与 D5 的 schema 冻结同源），通知不改变它。

## D13 追加的六条偏差

36. **"超时重试"改为"超时即一次失败的调用"**（`DESIGN` 2.6 表已改）。理由是可证伪的：超时不含"有没有执行"的信息，重试把不确定性变成了重复执行。代价是偶发超时按"这次没拿到"计；要再问由调用方决定（完全相同的重问会被 R1 拦下并回填）。
37. **"证据抽取与干净上游一致"拆成两句，并在 `DESIGN` 2.6 写清**。原文是一句话，实测发现它把"信封不影响判断"和"伪正文不算正文"混在一起，而只有前者能被正向测试证明。
38. **`slow_flaky_server` 的失败是确定性的第 N 次，不是 20% 随机**。随机让测试只能"循环到坏分支出现"，demo 也无法把同一处失败演示两遍。周期（`--timeout-every` / `--empty-every`）是命令行参数，测试可以把它调到第 1 次。
39. **`config/gateway-dirty.json` 是独立的配置文件，没有并进 `gateway.json`。** 并进去会让 `offered` 从 10 个涨到 23 个，D12 的 Accuracy@K 分母与 `results/table.md` 全部要重算——**兼容性测试不该改动实验的分母**。该配置也不挂 `schema-manifest`（文件故意不存在）：兼容面不是冻结契约，冻结它只会让"改了脏上游"表现为一屏 drift 告警。
40. **`weird_capability_server` 多了一个计划外的工具 `mirror_labels`**。计划里"返回 `null` 而非空对象"只测了否定路径；要证明"多块正文仍可读"，需要一个**有答案**的负载，否则整组正向断言是空的。它复制了 `labels` 的六行而不读数据库，这样这个 server 不依赖 `var/` 下的种子库。
41. **新增 `docs/backlog.md`**（PLAN 第 2 节点名要求的地方，此前一直不存在）。D13 攒下三条"想过但不该现在做"的：真裁剪 >1 MB、按形状选正文块、幂等性声明 + 可重试超时。每条都写了"什么条件下值得做"，因为只写"待办"等于把决定推给不在场的未来的自己。


## D14 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| Accuracy@5 相对全量下降 ≤ 10% | `scripts/gating_demo.py`：全量（K=30）**1.00**，K=5 **1.00**（热轮也 1.00），**下降 0%**。整条曲线印在同一张表里：K=3 → 0.923（热轮 0.889）、K=2 → 0.800、K=1 → 0.785。三个索引进同一张表，不是为了好看——`name` 要到 K=10 才 1.00（窗口均值 10.0 个、3650 字节/轮），`description` 在 K=5 只有 0.892、K=10 也才 0.908（就算加宽也回不到 1.00，因为有些工具的描述里根本没有那个词），`enriched` 在 K=5 就 1.00（窗口 5.3 个、**1920 字节，比全量的 14578 少 87%**） |
| `wiki.search` 与 `gh.search` 构成易混淆样本 | 两条断言，缺一条就都能被蒙对：**`name` 索引分不开**——"找一个函数在哪"那条落到 `wiki.recent_changes`（同为 `search` 后缀、只差 server 名）；**`description` 索引分得开**——两条各自落到 `wiki.search` / `gh.search`。所以这对样本测的是索引，不是排序器的运气 |
| 20 条任务在窗口下仍然做得完 | `answers: 20/20 ok \| outside-window calls: 0/65`：没有任何一次调用需要窗口之外的工具，也没有一条任务的答案改变（比对的是 checker 判定，不是摘要） |
| 窗口是真的，不是事后补记 | 离线重排**逐字复现**线上每一轮窗口：`replay == live: 65/65`。这一项是为了防"demo 自己算出漂亮窗口，网关实际发的是全量"——同一段 `gating_query()` 两边共用 |
| 缺事实时生产者还在窗口里 | `pins: 43/43` 个缺事实的热轮都保住了生产者（否则 R6 会把可完成的 run 判成 `DEGRADED`） |

## D14 的七个发现

**1. 窗口一窄，先坏的不是准确率，是完成率。** R6 判"任务永远做不完"用的是 `offered`：这一轮的工具面里没有某个事实的生产者，就归档 `DEGRADED`。窗口收窄到 5 个之后，排第 6 位的生产者被判成**不存在**——于是准确率指标一动不动，完成率悄悄掉了。修法不是把窗口放宽（那等于取消准入），而是把这一轮**缺什么事实就把它的生产者钉进窗口**，占槽位不加宽度：`|S_t| ∈ [K, K+M]`。**这是本文档里第三次出现同一类错误：一个下游消费者假设了另一个模块的输出是完整的。**（D9 的 R4 假设 `required` 粒度够细，D13 的截断假设 tool 名不会被 producers 规则解释掉。）

**2. 冷启动如果参与"稳定性合并"，整条曲线会变平。** 第一版把上一轮窗口无条件拿去和这一轮求交，冷启动那轮窗口是全量 30 个，于是第二轮变成 top-M（2K 宽）并**一直那么宽**——实测 K=1 时 Accuracy@K 也是 1.00，看着漂亮，实际每一轮都在发 10 个工具。`sticky_seed()` 对 `reason == COLD_START` 返回空之后曲线才有形状。**一个恒等于 1 的指标，在加选择器那天和加选择器之前长得一模一样。**

**3. 选择器的上限由 q 决定，不由 BM25 决定。** 只用 `--goal` 时，`describe_table` 在 30 个工具里排**第 11 位**，第 2 步就出窗——而它几乎是每条生产型任务的第 2 步。把最近 2 次调用的名字/参数和**结果前 400 字**放进 q 之后才进 top-5。BM25 的 `k1=1.5, b=0.75` 从头到尾没调过：**排序质量在这类任务上主要是"文档里写了什么"和"查询里放了什么"的问题，不是权重的问题。** 调参能掩盖第 3 项这种结构性错误。

**4. 停用词表必须收疑问词。** goal 写成 "which repositories have unresolved incidents and **how many** stars" 时，`wiki.tags`（描述里有 "how many pages carry it"）排第一——因为它和问题的**问法**共享词，而和问题的**内容**无关。加 `how/many/which/whose/there/does` 之后才对。这条和 D9 的"判据不能自证"是同一族：**匹配到了正确的词，不等于匹配到了正确的事。**

**5. 复数折叠那一行代码，决定了一个工具在不在窗口里。** `tables → table` 让 `describe_table` 从第 11 位进 top-5；`repositories → repository` 要 `ies → y` 特例（第一版折成 `repositorie`，索引和查询一起错，所以看不出错）。**只做复数，不做 Porter**：五条规则能被复算，一个算法不能——这和本项目"每个数字都要能被第二个人重跑出来"是同一条约束。

**6. `enriched` 索引省的不是准确率，是一半 context。** 跨层复用：网关已经从证据模型知道 `sqlite.query` 能产 `sql.rows.incidents`，而它的描述只写"跑一条只读 SELECT"。把"这个工具能产什么事实"加进被索引的文档后，`enriched` 在 K=5 达到 1.00，`name` 要到 K=10 才 1.00。**同样的准确率，一半的字节（1920 vs 3650）。** 换句话说，D9 建的证据模型在这里第二次付了利息——第一次是 R4 能判完成，这一次是准入能排对。

**7. "省调用"的分母不能跟着模型的可见性走。** 加过滤时顺手把政策里的尾巴（再验步骤）也按窗口过滤了，基线从 182 掉到 175——**省调用率从 64.3% 变成 62.9%，方向还反了**（分母小了，比例反而低）。原因不是数字难看，是口径错了：**计划属于模型**（看不见的步骤它不会选，所以跳过），**尾巴属于测量**（它是"省了几次"的分母，模型看不看得见它都在那儿）。`_tail()` 因此改回不过滤，并把理由写进注释；`_next_tail` / `_next_verify` 用谓词（这一步做过没有）而不是位置计数，因为过滤后列表长度会变——**D12 修过的下标 bug，在这个方向上是同一处。**

## D14 追加的七条偏差

42. **窗口外的调用放行，不拒**（新增 decision `forward_outside`）。判据只写"Accuracy@5 相对全量下降 ≤10%"，没写窗口外该怎样。放行的理由是**网关看见的是调用，不是意图**：拒掉会把一次排序失误变成任务失败，而轨迹从此分不清"计划错了"和"窗口藏了工具"。代价写在 `DESIGN.md` A1：`forward_outside` 不为零时，省下的只是 context，不是错。
43. **窗口宽度是区间不是定值：`|S_t| ∈ [K, K+M]`**（发现 1）。计划写"K=5"。pin 占槽位不加宽度——否则"钉住生产者"和"放宽窗口"没有区别，后者等于取消准入。
44. **`gating` 默认关，`config/gateway.json` 里显式开**。旧 trace 的 `offered` 记的是全量目录；默认改变它等于让历史轨迹撒谎。启动日志印 `gating: on (k=5, m=10, index=enriched)`，`docs/` 里每条命令都带着它。
45. **索引做成三个变体（`name` / `description` / `enriched`），默认 `enriched`**。计划只写"BM25"。没有变体，"enriched 有用"就只是个说法；有了变体，它是同一张表里的三行（发现 6）。
46. **新增 `mcp_servers/catalog_server.py` 做公共管道，并新增 `gh_server.py`**。计划只列 `wiki_server.py`——但易混淆样本需要**对手**：`wiki.search` 与 `gh.search` 同名不同 server，才谈得上"名字分不开、描述分得开"。两个 server 的 `Tool` / `select` / `build_server` / `serve` 抽进 `catalog_server`，各自只留数据与工具表。
47. **`agent/loop.py` 每轮重新 `list_tools()`，`RunResult.visible` 记下每轮窗口**。原实现一次取全量再循环——准入就不生效了。这一条是准入唯一真正的落点：网关会按窗口过滤 `tools/list`，但**如果客户端只问一次，窗口就是一次性的**。
48. **`_next_tail` / `_next_verify` 从位置下标改为谓词**。过滤后的列表长度会变，"第几个"不再稳定；改成"这一步做过没有"之后，两条政策共用同一段语义。D12 修过的同类下标 bug 在 D14 又冒了一次——说明**下标是一种会在改动中静默失效的接口**。

## D15 完成判据核对

| 判据（`PLAN.md`） | 结果 |
|---|---|
| 单文件 HTML 双击可看 | `python -m gateway.trace render <trace>` 写出**一个**文件，禁掉 `script src` / `link` / `@import` / 任何绝对 URL（自检逐条断言）——断网、无字体、无图片也能看。演示产物 `results/replay/r4.html`（18472 字节）与 `tampered.html`（18327 字节） |
| 点步看 `offered/chosen` 差集 | 左栏每步一个按钮，右侧卡片是**服务端算好**的三行：展示了 N 个 / 选的是其中之一（或**不在其中**）/ 展示了但没选 M 个；`offered` 列表里被选中的那条有底色与 `← chosen` 标记；窗口外的调用额外印"这次调用被记成 forward_outside：窗口是注意力，不是许可" |
| 篡改后链条标红 | 把第 3 行的 `latency_ms` 从 `0` 改成 `1234`（其余字节不动）→ 顶部 `链断裂 · 首个坏记录 seq=3 line=3 · hash_mismatch`；**只有第 3 张卡片是红的**，卡片里印"记录值 6078572fe174…，重算得 e1717cd307ca…"；第 4 张标"断链之后，无法验证"（灰色删除线）——不是绿色，也不是红色 |
| 证据齐那步 R4 高亮 | R4 命中的卡片带 `R4 证据完备 → 收束` 徽标与页面唯一的强调色，且必是最后一步（自检断言 `data-seq == 步数`）；`hit=false` 的 R4 只标 `R4 已评估 · 未命中`，不抢那个颜色 |
| （自加）渲染是确定的 | 同一份记录渲染两次**字节相同**；页面里没有时间戳、没有随机 id（自检用正则钉住）。理由：`latency_ms` 每次跑都不同，页面里再带一个"生成于"，diff 就再也读不出东西 |
| （自加）底部证据带与记录一致 | `required` 由**最后一条跑过后置规则的记录**的 `evidence ∪ missing` 反推（不读任务文件），t01 这条印出 `sql.rows.incidents / sql.rows.repos / sql.schema` 三个全亮 + `3/3 · 齐了`；缺一项就有一个暗 chip。**没有后置规则的轨迹不画这条带**——画一个空的等于宣称"什么都没要求" |
| （自加）轨迹文本不会变成标记 | `<script>alert("x")</script>` 与 `"><img src=x onerror=…>` 两种载荷都只作为文本出现；`escaping.html` 是给人肉看的样例，`tests/test_render.py::SafetyTests` 是给 CI 看的 |

## D15 的六个发现

**1. 一个"声明"要等第一个消费者出现，才变成"承诺"。** `gateway/trace/record.py` 里的 `DECISIONS` 集合写着 `halt_complete`，而那条规则写的是 `halt_evidence_complete`；漏了 `halt_duplicate`、`block_declaration`、`block_unknown_tool`、`block_schema_drift`；还列了一个只作为**状态**存在的 `waiting_approval`（`RUN_STATES` 里有它，`DECISIONS` 里不该有）。它漂了十一天没人发现，**因为没有任何代码读过这个集合**——它只是被 `__init__.py` 转出去，在文档里好看。D15 的视图要照着文件里的字符串画，这才第一次有人消费它。修法不是改那几行，是让下一个人改不动：`DECISION_SOURCES` 记明谁写哪个，`tests/test_record.py` 扫 `gateway/` 源码里的 decision 字面量，漏一个就红，多一个也红。

**2. 断言要落在元素上，不要落在整页字符串上。** 第一版自检写 `assertNotIn("is-r4", html)`，用来证明"没命中的规则不该高亮"——它必然失败，因为样式表里本来就有 `.card.is-r4{…}` 和 `.step.is-r4{…}` 两条规则。**这个断言测的是"页面里有没有这条 CSS"，不是"这条记录有没有被标成 R4"。** 改成对 `_cards(html)` 逐个判类名才是测那件事。同一天里第二次撞到同一类问题：`assertNotIn("R4 命中", html)` 也会被顶部那句"这条轨迹里没有 R4 命中"命中——**断言的范围比它要证明的命题大，就会既误报又漏报。**

**3. 算出来不渲染，等于没算。** 第一版把校验器的 `reason` 存进了 `step.error`，但只在"这一行不是 JSON"的分支里渲染它。于是被篡改的记录虽然标红了，却只显示字段表——**页面在告诉你"这里有问题"的同时，把问题的内容藏起来了**。自检里"页面要显示原因"那一条当场红。这一条和 D9 的"判据不能自证"是同一族：**做了事但没有交付，与没做在界面上长得一样。**

**4. 篡改之后的记录有第三种状态，不能只有红绿。** `verify` 只报**第一个**坏记录，因为它之后的 `prev_hash` 已经断了，后面每条"通过"都只是"它自己算得对"。视图必须接住这个口径：第一条红（有证据说它坏）、之后灰（**没有**证据说它们好或坏）。把它们标绿是撒谎，全标红是把"未知"说成"已知"。**"无法验证"是一个必须画出来的状态**，这在图上就是第三条颜色通道。

**5. `hit=false` 的规则不能借 `hit=true` 的措辞。** 第一版对"R4 评估过但没命中"也印 `R4 命中`。这不是文案问题：视图的每一行都在断言，而那一行断言的内容与文件相反。分开之后，`R4 已评估 · 未命中`（灰）与 `R4 证据完备 → 收束`（强调色）是两种事实——前者解释"为什么继续跑"，后者解释"为什么停"。

**6. 确定性是可以被测试的，而时间戳是最容易被顺手加进去的东西。** 我在页脚写了"由 `python -m gateway.trace render` 生成"，但没写生成时间——写了的版本会在同一天里让两份渲染结果对不上。**同输入同字节**现在是一条断言（渲染两次比字节 + 正则禁掉 `20\d\d-\d\d-\d\dT` 与 UUID 形态），因为"能 diff 的产物"本身就是这套东西的用法。

## D15 追加的七条偏差

49. **页面全部服务端渲染，JS 只切显示**（判据只写"点步看差集"，没写在哪算）。在浏览器里再算一遍差集等于给同一件事写第二个实现，而其中一个不在测试覆盖里。现在 `body.js-on` 只控制"JS 在时才折叠"，关掉脚本所有卡片仍然可见、可读、可 Ctrl-F。
50. **`render.py` 放进 `gateway/trace/`，并给 `python -m gateway.trace` 加 `render` 子命令**（计划只写"`render.py`"）。放包里的实质好处是它与 `verify` 共用同一个 `verify_lines`：**页面的 PASS/FAIL 与命令行不可能给出两个答案**——如果各写一份，迟早一个是"新口径"一个是"旧口径"。
51. **修正 `DECISIONS` 并加源码扫描测试**（判据外，D15 的第一个消费者逼出来的）。见发现 1。`DECISION_SOURCES` 是新的权威来源，`DECISIONS` 由它派生，两者不可能不一致。
52. **`hit=false` 不打强调色、文案改为"已评估 · 未命中"**（发现 5）。这一条会改变页面上"徽标密度"，属于表现层决定，但立的规矩是硬的：**视图里的每一行都必须与文件同义**。
53. **演示产物不入库，冻结一条样例轨迹代替**。`run_id` 与 `latency_ms`（被哈希）每次都不同，HTML 入库就是每次一片无关 diff；`results/replay/` 进 `.gitignore`，仓库里留 `traces/samples/replay-r4.jsonl`（真实跑出来的 4 步、R4 在最后一步、链完整）。所以 `replay_demo.py` 加了 `--sample-out`，样例是**可重造**的，不是手抄的。
54. **`docs/backlog.md` 加两条并写明"什么条件下值得做"**：跨 run 的合并视图（判据：合并页面必须与单独打开每个 run 得到逐字段相同的结论，否则合并引入了新的解释）、回放页上的"复制为断言"（拒绝理由：从页面复制的多半是**当前行为**，而这套东西存在的意义正是区分当前行为与期望行为）。
55. **按 `DESIGN` 2.7 补底部证据带，并删掉自己多加的暗色模式**。2.7 原设计写"底：证据进度 通栏、`required` 逐项点亮"，第一版只在卡片里印累计证据，等于把一条判据实现成了半条。`required` 由最后一条跑过后置规则的记录的 `evidence ∪ missing` 反推，**不读任务文件**——2.7 的硬线是"UI 只读 trace，不产生任何新数据"，从任务文件读 `required` 看似更准，实际让页面有了第二个输入源，而那个源不会随轨迹走。暗色模式是同一类问题：先写了 `prefers-color-scheme`，再删掉——语义色只有一套，暗色下六个语义色都得重定对比度，那是把"证据"调成"主题"，正是 2.7 明令禁止的那件事。**设计文档里写"禁止"的东西，落实时最容易被当成审美偏好绕过；它其实是一条判据。**

## D16 完成判据核对

缓冲期没有新判据，验收就是一句话：**只修 bug，且每个 bug 都有一条会红的测试**。所以这张表记的是"用什么输入撞出了什么"。

| 输入 | 修之前 | 现在 |
|---|---|---|
| 一行合法 JSON，其中一个字段是 `NaN`/`Infinity` | `NonCanonicalValue` 抛出 `verify_lines` → CLI traceback，退出码 1 | `non_canonical`，**带行号**，detail 写明"无法重算哈希，这条不可验证"——不说成篡改 |
| 文件里有一个字节不是 UTF-8 | `UnicodeDecodeError` 抛出 `verify_path` 与 `load_replay` | `not_utf8`，带行号 + 字节偏移 + `records=n`（坏行之前真校验过几条）；渲染同一口径 |
| `verify <目录>` | `PermissionError` traceback，退出码 1（= "你的轨迹被篡改了"） | 退出码 2 + "打不开这个路径"；`verify nope.jsonl` 也统一到 2 |
| 同一工具换描述、名字不变（`tools/list` 再问一次） | 索引缓存按名字命中 → 用旧文本排名，实测排名一动不动 | 按语料做键，排名跟着新文本走；缓存上限定为 8 个语料 |
| 1.2 MB 的一页中文结果 | `len(repr(...))` 数的是字符 = 40 万 → **不标 `truncated`** | `result_size_bytes()` 数 UTF-8 字节 → 与同样大小的 ASCII 一致 |
| 4000 条类型变异记录（3913 条真改过） | 1 类崩溃 | **0 崩溃、0 谎报绿**（改过的记录不可能验通过，这条被断言了 3913 次） |
| `_meta: []` / `_meta: "gateway:t01"` | 静默回落成"这个调用没有声明"，run 不继承、不报错 | `DeclarationError: _meta must be an object`；`_meta: null` 与缺省仍是"没声明" |
| 空文件 / 纯空行 / BOM / CRLF / 20 万字符长行 / NUL 字节 | 全部已正确（BOM 报 `json_parse` 并给出 "decode using utf-8-sig"） | 未改动，写进探测脚本防回归 |

## D16 的五个发现

**1. 判"文件有没有被改过"的程序，必须能承受文件已经坏了。** `verify` 是这套东西的终审，它的输入按定义就是"可能已经损坏的字节"。可它原先有两条路会抛异常：非有限浮点（`json.loads` 默认接受 `NaN`）与非 UTF-8 字节。两者都表现为 **traceback 取代行号**——而文件损坏时，行号是唯一有用的东西。教训不是"多加几个 try"，是**给校验器写测试时，输入要从"坏文件"出发，而不是从"合法记录改一个字段"出发**。前者是我这次做的事，后者是之前所有测试做的事。

**2. "不可验证"是第三种结论，不能借"被改过"的措辞。** 含 `NaN` 的行没有哈希可比，非 UTF-8 的行根本读不出内容——两者都**没有被证明被篡改**。把它们报成 `hash_mismatch` 是在编证据，而编证据正是这个项目反对的东西。这和 D15 的"篡改之后是第三种状态"是同一条：**红/绿之外必须有"不知道"，而且"不知道"要说出来。**

**3. 断言两件事相同的代码，最后一定会不同。** `verify_path` 读 UTF-8、`render.load_replay` 也读 UTF-8，两个 `read_text` 各自处理坏字节，于是页面和命令行会就"哪一行坏了"给出两个答案。修法不是让两边都 try 一下，是**让 `render` 调 `verify` 的解码器**（`decode_trace`）。这条在 D15 已经立过一次（页面与 CLI 共用 `verify_lines`），D16 是它在字节层的复发——**同一个原则会在每一层重新出现，直到那一层也被收进去。**

**4. 缓存键漏掉维度，就是"用不存在的语料回答现在的问题"。** 选择器的索引按工具名做键，而它缓存的东西依赖描述与参数名。名字不变、文本变了，缓存命中，排名按一份已经不存在的目录给出。触发条件不是假想：D14 让 agent **每轮重新 `tools/list`**，D13 的上游**就是会改 schema 的**。修成按语料做键后每轮重建 0.33 ms——**一个会撒谎的缓存，比没有缓存贵得多。**

**5. 名字里的单位是承诺。** `MAX_RESULT_BYTES` 量的是 `len(repr(...))`，即字符数。同样 1.2 MB，ASCII 被标 `truncated`、中文不被标。而这个标记的下游是 R4：漏标的部分结果会被当成完整证据，直接产出一个假完成。**方向的错误比大小的错误严重**——阈值差一倍只是慢一点，判反了就是错答案。

**6. "沉默"是最难被发现的失败。** 边界层把 `_meta: []`（不是对象）过滤成 `None`，于是"这个调用没有声明"。没有异常、没有日志、没有测试失败——只有一次本该继承证据的 run 悄悄从空开始。**这个模块自己的文档就在反对这件事**（"未知键必须报错，否则调用者会以为声明生效了"），但它只防住了 `_meta` 里面的拼写，没防住 `_meta` 本身的形状。**一条规则的检查点要覆盖到它自己的入口。**

## D16 追加的三条偏差

56. **CLI 的退出码从 0/1 扩到 0/1/2，`verify` 对"读不到"不再返回 1**。判据没提退出码，但原来的行为是"关于一个从未打开的文件，做出关于内容的判决"。2 的含义是"读不到或命令行不对"，与 `gateway/__main__.py` 里既有的 0/2/3/4 同一套习惯。**这是一次口径变更，所以 `tests/test_cli.py` 先立起来**——此前 CLI 一条测试都没有，退出码只是文档里的一句话。
57. **`not_utf8` 额外报出 `records=n`**（坏行之前已验证的记录数）。判据只要求"能定位"，多加这个数字是因为审稿人真正要问的是"这份文件还剩多少可信"。代价是多一次前缀校验（O(n) 且只在文件已坏时发生）。
58. **选择器缓存加了上限（8 个语料，FIFO 淘汰）**。按语料做键之后，一个持续漂移的上游每轮都会产生新键，字典就成了长期进程里的慢性泄漏。**改一个 bug 时要把它的新失效模式一起想掉**——否则只是把崩溃换成了泄漏。
59. **第六个 bug 在 tag `v0.15-d16` 之后才撞出来**（补齐边界层审计时），单独一个 commit。前五个修完就打点，然后按"还剩哪层没撞过"继续推进——`boundary.py` 当时只读过、没喂过输入。**记录这件事本身也是判据的一部分**：如果文档写"五个"而实际六个，那就回到了这个项目最反对的那种状态——产物与描述不一致。`539 tests`（裸解释器 399 / 140 skipped）。

## D17 完成判据核对

缓冲期没有新判据，验收仍是那句话：**只修 bug，且每个 bug 都有一条会红的测试**。这一轮撞的是 D16 收工时点出的三个方向。

| 输入 | 修之前 | 现在 |
|---|---|---|
| 文件以 `..."turn":2}` 结尾（崩在写 `\n` 之前），再打开续写 | 读出 `seq=2`，把新记录贴在 `}` 后 → **两条记录粘成一行，前一条无法解析，无人报错** | `TraceChainError: the last line has no terminating newline`，字节不动；提示"若确认这条记录完整，请手工补一个换行" |
| 文件尾部是半截 JSON | 原始 `JSONDecodeError` 冒出 `_resume` | `TraceChainError`，带行号 |
| 文件尾部有非 UTF-8 字节 | 原始 `UnicodeDecodeError` | `TraceChainError: line 3 is not UTF-8 (not_utf8)` |
| 尾部被改过一个字段 | 照样续写，新记录的 `prev_hash` 指向一个不描述任何东西的哈希 | `TraceChainError`，带 `hash_mismatch` 与行号 |
| 两个 sink 打开同一个文件 | 都以同一 `seq` 恢复、都写入，链里多一条重复，事后报成 `seq_gap` | 后动手的那个拒绝（`grew from 0 to 707 bytes`），磁盘上那一条仍校验通过 |
| 文件被截短 | 续写到错误偏移 | 拒绝（`shrank`） |
| sink 的句柄已被关闭 | `ValueError: I/O operation on closed file` 从 `tell()` 抛出，**未分类**；sink 仍"健康"、记录丢失 | `TraceWriteError (ValueError)`，sink 中毒，文件 0 字节 |
| 规则 `check()` 抛 `ZeroDivisionError` | 异常穿过 `_handle_call`（**在 `record_call` 之前**）→ 被审的那次调用**零条记录** | `decision=halt_rule_error`、`state=DEGRADED`、一条记录、`stop_check.rule` 指名规则；`KeyboardInterrupt` 仍照常抛出 |
| `policy:"declared"` + 空 `plan` | 第一轮返回 `None` → 0 次调用、0 条记录、`steps=0`，无任何报错 | 加载期 `ValueError`：声明了计划却不计划任何事 |
| `list_tables` 返回字符串 / 对象数组 / `rows` 不是对象 | 已能退化（当作没有表、当作没有行） | 未改动，写进探针防回归 |

## D17 的六个发现

**1. "追加"能犯的最重的错，是改写了不是自己写的字节。** 丢一个换行的尾部，恢复时被当成完整记录，新记录贴在它后面，前一条从"可解析"变成"不可解析"——**一次 append 毁掉了更早的一条记录**，而且没有任何东西报错。这是写入器唯一的越界行为，所以修法不是"更小心地读最后一行"，而是**续写必须先证明这份文件值得续写**（跑一遍真校验器），并且**任何情况下都不去修它**：修一次，它就成了改写他人字节的地方，那是这个模块全部的立身之本。

**2. 防御性的 `try` 要把获取现场的那一步也包进去。** `tell()` 被留在 `try` 外面，于是"句柄已死"这个最该被分类的失败恰好逃出了分类——记录没写、sink 仍健康、调用者拿到一个没人认领的 `ValueError`。修法不是补一层 `except`，是**让回滚点在失败之前就已确定**（用 `self._size`，只追加契约下它就等于文件末尾）。**依赖一个会失败的调用去准备失败时的退路，是把退路也一起赌上。**

**3. 一个崩溃如果不能被记录，它就变成了"这次调用从未发生"。** 规则崩溃时异常在 `record_call` 之前逃出，于是轨迹里少一条。这份设计明确说"未被记录的调用是唯一的盲点"，而它自己的扩展点（规则）就是产生这个盲点的入口。**扩展点的失败必须走正常路径，不能走异常路径**——转成判决，让写记录那段代码照常跑。

**4. 分母为 0 的任务看起来像最优解。** 声明了 `policy:"declared"` 却没有计划，run 以 0 次调用"成功"结束：主张的指标（省调用）把它算成完美，实际是把任务从实验里删掉了。**"什么都没做"和"做完了"必须能被区分**，方法是在加载期就拒绝，而不是在报表里解释。

**5. 注释里的因果要先量再写。** 我给 `except Exception` 写的理由是"代理项在 `write()` 抛 `UnicodeEncodeError`"。探针一跑就否了：`seal()` 早就把代理项拒了，那个异常到不了写入器；真正到达的是死句柄（发现 2）。**一个看似有据的错误解释，比没有解释更坏**——后来的读者会照着它去改错的地方。

**6. 有些性质在平台上测不了，就别写成测得了。** "文件被替换"这条用例在 Windows 上根本跑不起来（打开的句柄删不掉），而且检查本身就是按大小做的，**同名同大小的替换本来就能过**。删掉这条用例，把"这个检查只能发现大小漂移"写进注释：**测代码真有的性质，不测名字暗示的性质。** 同一类还有一处纯测试错误——`tearDown` 里清临时目录而句柄未关，Windows 报 `WinError 32`，读起来像用例失败而用例其实通过了；改成 `addCleanup`（逆序执行）。


