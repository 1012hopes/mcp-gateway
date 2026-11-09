# 最优步数：怎么数，以及为什么这么数

`optimal_steps` 是**先定规则、后量数据**的。规则写在这里，数字写在每条任务的
`tasks/*.json` 里，实测数字由 `scripts/task_sweep.py` 产出（`results/tasks.md`）。
三处分开不是形式主义：如果"最优"是量完之后回填的，那它就不是判据，是事后解释。

## 一、规则

> **最优步数 = 产出正确答案所需的最少工具调用数，且每一次调用都必须贡献至少一项
> `required_evidence`。多出来的、看着有用但没带回新事实的调用，一律不计。**

按这条规则：

- **生产型任务**的最优步数恰好等于 `required_evidence` 的条数。每项事实一次调用，
  没有一次是"顺手看看"。
- **对抗型任务**（`required_evidence: []`）的最优步数是 1：问一次，空行集就是答案。
  "查过了、没有"是知识，这一点在 `config/evidence.json` 的 `why` 里已经写明，
  所以一次问询就够。

这条规则故意**不**把"先看看表结构"算成必要步骤，尽管网关的证据模型把 `sql.schema`
算作一种事实。区别在于：一个知道数据库长什么样的执行者可以在一次查询里把问题问对，
而 `sql.schema` 之所以出现在多数任务的 `required_evidence` 里，是因为**这个 agent**
（`DeclaredPolicy`）确实会先探一次结构——它是 agent 的谨慎，不是任务的要求。

于是同一批任务有两列数，差的那一步正好是"agent 自己的谨慎"：

| 列 | 含义 | 谁产生 |
|---|---|---|
| optimal | 最少必要调用 | 人，按上面的规则 |
| baseline | `--no-rules` 的实际步数 | `DeclaredPolicy`：计划 + 再验一遍 |
| governed | 装上规则后的实际步数 | 网关在证据齐的那次调用上收束 |

## 二、为什么最优不按 agent 的 plan 长度算

`t03` 的 plan 是 `list_tables → describe_table(releases) → query releases`，三步。
其中 `describe_table` 对答案毫无贡献——agent 只是不确定 `releases` 有没有 `breaking`
这一列。若把最优记成 3，3 倍判据就变成"8 步 vs 3 步"，测的是这个 agent 有多谨慎，
而不是网关的判据有多准。记成 2，比较的是"最少必要的两次"和"它实际花了八次"。

## 三、每条任务的最少调用路径

生产型（14 条）：

| 任务 | 问题 | 最优 | 最少调用路径 |
|---|---|---|---|
| t01 | 未解决 incident + 各 repo 星数 | 3 | `list_tables` → `query incidents` → `query repos` |
| t03 | 哪些 release 是 breaking | 2 | `list_tables` → `query releases where breaking=1` |
| t04 | gateway 的提交与作者 | 2 | `list_tables` → `query commits where repo=gateway` |
| t05 | 被标为 vulnerable 的依赖 | 2 | `list_tables` → `query deps where vulnerable=1` |
| t06 | bifrost 的维护者 | 2 | `list_tables` → `query maintainers where repo=bifrost` |
| t07 | forge 最近一次扫描发现 | 2 | `list_tables` → `query scans where repo=forge` |
| t08 | incident 2 的标签 | 2 | `list_tables` → `query labels where incident_id=2` |
| t09 | 星数前三的 repo 与语言 | 2 | `list_tables` → `query repos order by stars desc limit 3` |
| t10 | 未关闭 incident 及其标签 | 3 | `list_tables` → `query open incidents` → `query labels` |
| t11 | 有 triage 维护者的 repo | 2 | `list_tables` → `query maintainers where role=triage` |
| t12 | sev1/sev2 incident 与仓库语言 | 3 | `list_tables` → `query incidents` → `query repos` |
| t13 | 2026-04-25 之后的提交 | 2 | `list_tables` → `query commits where committed_at > ...` |
| t14 | gateway 的 v0.9.0 与星数 | 3 | `list_tables` → `query releases` → `query repos` |
| t15 | 没有未关闭 incident 的 repo | 3 | `list_tables` → `query open incidents` → `query repos` |

对抗型（6 条）：最优均为 1，`query` 一次，返回空行集即答案。

## 四、实测（`results/tasks.md`）

| 任务 | 类型 | 最优 | 基线 | 受控 | ratio | 停在哪 |
|---|---|---|---|---|---|---|
| t01 | 生产 | 3 | 9 | 4 | 3.00x | R4 `HALTED_COMPLETE` |
| t02 | 对抗 | 1 | 11 | 3 | 11.00x | R2 `HALTED_NO_PROGRESS` |
| t03–t09 | 生产 | 2 | 8 | 3 | 4.00x | R4 |
| t10 | 生产 | 3 | 10 | 4 | 3.33x | R4 |
| t11 | 生产 | 2 | 8 | 3 | 4.00x | R4 |
| t12 | 生产 | 3 | 10 | 4 | 3.33x | R4 |
| t13 | 生产 | 2 | 8 | 3 | 4.00x | R4 |
| t14 | 生产 | 3 | 10 | 4 | 3.33x | R4 |
| t15 | 生产 | 3 | 10 | 4 | 3.33x | R4 |
| t16–t20 | 对抗 | 1 | 10 | 3 | 10.00x | R2 |

合计 **182 → 65 次调用**（为基线的 36%），20/20 满足 `baseline ≥ optimal × 3`。

## 五、四条已知代价

1. **受控步数 = 最优 + 1，差的那一步不是网关能省的。** `t03` 受控 3 步、最优 2 步：
   第 2 步是 agent 自己 `describe_table`，它不产生新事实，但也不违反任何规则——钱已经
   付了，结果也是合法答案的一部分。网关只在**证据齐了**的那次调用上收束，不做"这次
   调用看起来没用，我替你省掉"这种事。那种判断需要的正是本项目拒绝替 agent 做的判断。
2. **一次 join 能同时带回两个事实，但证据模型只认一个。** `t10`/`t12`/`t15` 的三步最优，
   一个写过 SQL 的人可以用一次 join 压到两步。证据类型按 SQL 文本里的 `from <表>`
   归类（DESIGN 6.11），`from incidents i join repos r` 只会被记成 incidents。所以这里的
   最优是"网关能识别的证据单元数"，不是 SQL 方言能压到的极限。这是判据的代价，写在
   局限里，不是隐藏项。
3. **`t01` 的 ratio 恰好卡在 3.00。** 它的基线是 9 步（`ReactivePolicy` 的 4 步计划 +
   5 步再验），最优 3 步。之所以不是更高，是因为那条任务的 `describe_table` 和"再验"
   尾部都只有 5 项——计划越短，尾部占比越高。没有为凑比值改过任何一条任务：卡线就
   报卡线。
4. **3 倍是入选门槛，不是研究发现。** 它筛掉的是"9 步 vs 8 步"这种测不出东西的任务。
   真正的结论在第 4 列和第 5 列的绝对差，以及"停在哪一条规则上"——不是比值本身。

## 六、对抗任务为什么受控也是 3 步而不是 1 步

一次空结果是"这次没有"，两次同样的空结果是"再问也没用"。R2 判的是后者：它要看到
连续两次完全相同的状态（证据集 + 结果摘要集合），才允许说平台期成立。凭一次空结果
就断言不可答，会把"表里恰好没有这一行"和"这个查询写错了"混为一谈——而后者是 agent
最常犯的错，也正是它值得再问一次的理由。
