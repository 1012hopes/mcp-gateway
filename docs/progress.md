# 进度日志

每天一行，格式：`D<n> | 状态 | 实测 | 明日`。这不是形式主义——项目主张是"行为可追溯"，开发过程用同一套做法。

| 日 | 状态 | 实测 | 下一个 |
|---|---|---|---|
| D1（提前于 11-09 完成） | 完成 | `canonical.py` + `record.py`，37 tests 通过；哈希覆盖未知字段、浮点与键序归一、`-0.0` 折叠、NaN 拒绝 | D2 |
| D2（同日完成） | 完成 | `sink.py`（fail-closed，写失败回滚不留半行）+ `verify.py`（L1 先于 L2，报首个异常）+ `cli.py`；74 tests；样例 12 步轨迹 + 6 种篡改演示全检出 | D3 已并入 D2，接 D4 透传与 G1 |

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
