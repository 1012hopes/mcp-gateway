# 进度日志

每天一行，格式：`D<n> | 状态 | 实测 | 明日`。这不是形式主义——项目主张是"行为可追溯"，开发过程用同一套做法。

| 日 | 状态 | 实测 | 下一个 |
|---|---|---|---|
| D1（11-10，提前于 11-09 完成） | 完成 | `canonical.py` + `record.py`，37 tests 通过；哈希覆盖未知字段、浮点与键序归一、`-0.0` 折叠、NaN 拒绝 | D2：sink（fail-closed）+ verify 首错定位 |

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
