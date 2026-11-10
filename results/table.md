# Baseline vs gateway (generated)

Produced by `python scripts/experiment.py`. Every number below is measured
on this machine: `optimal` is hand-counted and lives in `tasks/*.json`
(method: `docs/optimal-steps.md`), the step counts come from two real runs
per task, and `answer` comes from that task's checker in `tasks/checkers/`.

## Metrics

| 指标 | 基线 (`--no-rules`) | 网关 (R1/R2/R3/R4/R6) |
|---|---|---|
| 停对了 steps ∈ [可判最优−1, 可判最优+1] 且 checker 通过 | 0/20 | 20/20 |
| 停早了（误停）steps < 下界 **或** checker 失败 | 0/20 | 0/20 |
| 停晚了 steps > 上界 **或** 未自行停止 | 20/20 | 0/20 |
| **误停率**（early / 总任务数，A6 口径） | 0.0% | 0.0% |
| 调用数 | 182 | 65 |
| 省调用 (baseline − governed) / baseline | — | 64.3% |
| Accuracy@K（chosen ∈ 当轮窗口，K=5）‡ | 0.95 (n=182) | 1.00 (n=65) |

‡ Accuracy@K 在 D14 之前是**按构造的 1.00**（没有选择器，`offered` 就是全部
工具，正确工具必然在其中）。现在 `offered` 是这一轮真正展示给模型的窗口
（K=5，稳定性合并后至多 2K），所以两列的差别有了含义：

- 网关 **1.00**：65 次调用里没有一次需要窗口之外的工具。窗口足够，这是
  `scripts/gating_demo.py` 那条 K 曲线要证明的事（K=3 时掉到 0.92，K=1 时 0.79）。
- 基线 **0.95**：缺口全部出现在完成之后的“再验”阶段——那时窗口已经跟着 run
  收窄到它正在问的那几张表，而基线还在回头看它此刻没被展示的工具。这是
  “打转”在选择层的样子，不是选择器出错。

## Per task

| task | kind | optimal | band | baseline | governed | rule | answer | verdict |
|---|---|---|---|---|---|---|---|---|
| t01 | productive | 3 | [2, 4] | 9 | 4 | R4 | ok | right |
| t02 | adversarial | 1 | [2, 4] | 11 | 3 | R2 | ok | right |
| t03 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t04 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t05 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t06 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t07 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t08 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t09 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t10 | productive | 3 | [2, 4] | 10 | 4 | R4 | ok | right |
| t11 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t12 | productive | 3 | [2, 4] | 10 | 4 | R4 | ok | right |
| t13 | productive | 2 | [1, 3] | 8 | 3 | R4 | ok | right |
| t14 | productive | 3 | [2, 4] | 10 | 4 | R4 | ok | right |
| t15 | productive | 3 | [2, 4] | 10 | 4 | R4 | ok | right |
| t16 | adversarial | 1 | [2, 4] | 10 | 3 | R2 | ok | right |
| t17 | adversarial | 1 | [2, 4] | 10 | 3 | R2 | ok | right |
| t18 | adversarial | 1 | [2, 4] | 10 | 3 | R2 | ok | right |
| t19 | adversarial | 1 | [2, 4] | 10 | 3 | R2 | ok | right |
| t20 | adversarial | 1 | [2, 4] | 10 | 3 | R2 | ok | right |

`band` 的上界是 ±1 容差；对抗任务的中心是 `optimal + 2` 而不是 `optimal`，
理由写在 `gateway/metrics.py` 的模块注释里：平台期要看到两个相同答案，
而对抗策略在第一次查询前还会读一次表结构。`optimal` 列保持信息论下界不变。

`answer` 是 checker 对交付结果的判定，与步数无关：一个跑得很久但答错的结果，
`answer` 为 `wrong`，按 A6 口径计为误停。
