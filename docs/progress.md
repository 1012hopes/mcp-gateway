# 进度日志

每天一行，格式：`D<n> | 状态 | 实测 | 明日`。这不是形式主义——项目主张是"行为可追溯"，开发过程用同一套做法。

| 日 | 状态 | 实测 | 下一个 |
|---|---|---|---|
| D1（提前于 11-09 完成） | 完成 | `canonical.py` + `record.py`，37 tests 通过；哈希覆盖未知字段、浮点与键序归一、`-0.0` 折叠、NaN 拒绝 | D2 |
| D2（同日完成） | 完成 | `sink.py`（fail-closed，写失败回滚不留半行）+ `verify.py`（L1 先于 L2，报首个异常）+ `cli.py`；74 tests；样例 12 步轨迹 + 6 种篡改演示全检出 | D3 已并入 D2，接 D4 透传与 G1 |
| D4（11-09） | 完成 | 官方 SDK 2.3 透传 + `sqlite_server` 自写上游；真实运行 5 次调用产出 5 条带 `offered` 的记录并 verify 通过；fail-closed 三段演示全 PASS；94 tests（14 条需 SDK，裸解释器 skip） | D5：多上游 + 注册表 + schema 归一 |
| D5（11-09） | 完成 | `registry.py`（归一 / 命名 / 哈希 / 漂移）+ `dirty_schema_server`；两上游 10 个工具并存，`sqlite.query` 与 `dirty.query` 互不遮蔽；`freeze_schema.py --check` OK；136 tests | D6：生命周期接线 + 轨迹落盘 |

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
