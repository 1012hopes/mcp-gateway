# G2 演示脚本（照着念）

> 目标：在 40 秒内让人相信「这条 run 不是被预算掐断的，是被判据判停的，而且判据的依据就在文件里」。
> 全部命令都在 `E:\mcp-gateway` 下执行，需要一个装了 `mcp` 的解释器（`.venv`）。

## 0. 准备

```powershell
# 一条命令：打转任务跑两遍（无规则 / 有规则）+ 8 项自检
.venv\Scripts\python.exe scripts\g2_demo.py
```

任务 `t02` 问的是"库里有一个叫 `ghost/typo` 的 repo 吗"。答案是没有，但 Agent 不接受：

```
baseline -- no rules installed
   1. sqlite.list_tables   state=d15447bea5
   2. sqlite.query         state=03666e90a3
   3. sqlite.query         state=03666e90a3  <- same state
   ...
  11. sqlite.query         state=03666e90a3  <- same state
  stop: max_turns after 11 steps
```

## 1. 先把"这不是重复调用"钉死

**台词**：11 步，`select` 的写法一步一个样——`= 'ghost/typo'`、`lower()`、`like '%ghost%'`、`glob`、`instr()`、`substr()`……**没有一次是逐字重复**，所以 R1 一次都不会响。它每步都在"工作"，每步都在得到同一份空结果。

自检里这一条是硬的：

```
[PASS] baseline makes no exact repeat (so this is not R1's catch)
[PASS] every baseline answer after the first is identical
```

## 2. 再看装上规则之后

```
governed -- rules installed
   1. sqlite.list_tables state=d15447bea5
   2. sqlite.query       state=03666e90a3
   3. sqlite.query       state=03666e90a3  <- same state
  stop: halted after 3 steps
  halt: rule=R2 state=HALTED_NO_PROGRESS
        reason=2 consecutive calls left the run's information state unchanged
               (state 03666e90a3); the last result repeated what the run already
               had, so rephrasing is not progress
steps: 11 -> 3  (saved 8)
```

**台词**：第 3 步停下，是因为**第 3 步的答案和第 2 步逐字节相同**。判据不是我事后看日志说"这里在打转"，而是 `state_hash` 相等——`state` 是「证据集 ∪ 已交付结果摘要」的指纹，两个集合都只增，所以它**当且仅当运行学到东西时**才变。两行相同，就是"这两步之间什么都没学到"。

## 3. 平台期在文件里，不在我的话里

```powershell
.venv\Scripts\python.exe -m gateway.trace verify traces\runs\run-g2-demo-governed.jsonl
```

打开那条轨迹，看最后两条记录的 `stop_check.state_hash`：

```
seq 2  state_hash = 03666e90a3…
seq 3  state_hash = 03666e90a3…   <- 与前一条相等，R2 就是在这里判的
```

**台词**：我不需要读者相信我的解释——相邻两条记录的值摆在那儿。这也是为什么 D8 把 `state_hash` 写进了记录（schema v1 → v2）：**判据的依据要能被复看，否则它只是断言。**

## 4. 主动说清楚 R2 判不了什么

**台词**：这个判据的前提是"答案能逐字节比较"。上游只要在每次响应里带一个时间戳或请求 id，R2 就完全失效——所以它只是**兜底**。

真正承担"该收了"的是 R4：`required ⊆ Evidence`。它判的是"任务需要的证据齐了没有"，不关心答案是否重复。`t01` 那条正常任务就是例子：R1 把 9 步压到 5 步，R2 一步也没省——因为那 4 步的答案各不相同，状态一直在变。**R2 不是压缩器，是安全带。**

## 5. 录屏时的三个镜头

| 镜头 | 命令 | 时长 |
|---|---|---|
| 一 | `g2_demo.py` 的 baseline 段（11 步、不断重复的同一状态） | 12 s |
| 二 | 同一次运行的 governed 段（3 步、`<- same state`） | 8 s |
| 三 | `verify` + 打开轨迹指 `state_hash` 那两行 | 12 s |

镜头一和镜头二要连着放，中间不要剪：**先看见它真的在打转，再看见它在第 3 步停**。剪开就变成"我跑了个能停的例子"。

## 6. 常见追问与答法

| 追问 | 答 |
|---|---|
| 这不就是把 max_turns 调小？ | 不是。`max_turns` 是"步数到 11 就砍"，砍在 11；R2 砍在 3，砍的依据是"第 3 步没学到东西"。前者是常量，后者是判据——`reason` 里写着是哪两步相同 |
| 为什么不直接把这种 prompt 写进 system message？ | 因为判据在网关侧。Agent 侧的自律是 prompt，模型一换就失效；网关侧的决定不依赖模型是否听话，而且会留痕 |
| 换个措辞就停，会不会误杀正常任务？ | 只在**两个连续答案逐字节相同**时才停。一次空结果是正常的（探路），第二次还是同一份空结果才叫停滞。`window` 可配，`window=1` 被代码直接拒绝 |
| 那上游每次都返回一样的东西呢？ | 那就是真的没进展，停是对的。反过来，上游每次都返回不同的东西时 R2 会失效——这条写在 `DESIGN` 局限 9 里 |
