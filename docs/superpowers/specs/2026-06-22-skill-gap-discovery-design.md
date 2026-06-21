# Skill Gap Discovery — 设计文档

**日期**: 2026-06-22
**状态**: 已批准，待实现
**作者**: Henry + Claude

## 1. 背景与动机

当前 `skill-insight` 回答的问题是 **「你现有的 skill 好不好用」**：`extract_skill_data.py`
用 `grep -l '"name":"Skill"'` 只扫描*包含* skill 调用的会话（约 10% 的文件），把每次调用
当作测试用例评分。报告第 6 步的「触发类」建议也只覆盖**已安装 skill 的漏触发**。

本 feature 增加一个互补的问题 **「你还缺哪些 skill」**：从聊天记录里挖出那些*反复手动做、
却没有任何 skill 接住*的工作流，建议新建 skill。

- 现有能力：**审现有的**（skill 调用 → 评分）
- 新增能力：**找该有的**（无 skill 会话 → 反复工作流 → 建议新建）

**核心技术含义**：当前提取器只看*有* skill 调用的会话；发现新 skill 机会恰恰要看*没有*
skill 调用的会话（约 90% 的文件、GB 级）。这是一次方向相反的数据采样，是真正的新能力，
不是改改 prompt 就行。

## 2. 设计决策（已确认）

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 集成方式 | **现有报告加一节** | 复用同一 biweekly cron / 同一份报告 / 同一次 `pi -p` 调用，改动最小，一次运行两件事都看 |
| 核心信号 | **反复手工多步工作流** | 「你老在手动做这件事」是最硬的信号；跨会话复现的同类多步任务、Claude 每次重新手写的同类脚本 |
| 输出深度 | **候选清单 + 证据 + description 草稿** | 与现有报告「可粘贴 SKILL.md 文案」的粒度对齐；不自动建文件 |
| 自主性分工 | **薄索引 + 高自主** | Python 做廉价完整的*普查/索引*（贵且必须完整的部分），agent 自主*深挖判断*（它强的部分） |

### 2.1 为什么是「薄索引 + 高自主」而非纯自主

`extract_skill_data.py` 存在的理由，README 写得很直白：「compress GBs of logs into one
compact JSON **so the agent reads cheaply**」。对*更简单*的「评测已有 skill」任务，作者已经
判断过不能让 agent 裸奔 GB 级 jsonl。新任务的干草堆更大（无-skill 会话占 90%），纯自主的
风险更突出：

1. **成本/耗时不可控** — cron 无人值守，agent 一个 `cat` 撞上几 MB jsonl 就能炸 context
   或烧掉大量 token。现有 prompt 那句「不要整文件读入大 jsonl」是*预提取之上的护栏*，
   不是预提取的替代。
2. **不确定性** — 单次 agent pass 天然倾向 under-explore / 早停，记分卡式报告最怕跑一次很全、
   下次很懒的方差。
3. **覆盖盲区** — 「同一串命令跨 N 个会话复现」必须*跨文件聚合计数*；agent 临时 grep 只会
   撞见一部分簇，给不出完整的跨会话频次普查。

因此 Python 退成「**导航索引 + 频次普查**」——不是 agent 拿去聚类的有损摘要，而是告诉
agent *哪些*会话值得看、各类工作流出现*多少次*；然后 **agent 自主把有苗头的会话读全**，
自己聚类、判断、写提案。这正是项目现有的成熟范式：评测已有 skill 时 Python 出 `calls[]`，
但 prompt 明确允许 agent「拿不准时打开 `calls[].file` 精读」。新任务沿用同一招。

## 3. 架构与数据流

```
extract_skill_data.py ──同一次提取、同一份 JSON──► pi -p（同一次调用）──► 同一份报告 + 新增一节
   既出 calls[]（现有，不动）                         prompt 加一个分析步骤        ⑥ 建议新建的 Skill
   新增 no_skill_index（薄索引）                                                  （能力缺口）
```

cron / plist / 锁 / gate / 通知 / 失败退避 —— **全不动**。

## 4. 组件设计

### 4.1 提取器改动 `extract_skill_data.py`

**职责单一**：在现有提取流程旁，多产出一份「无 skill 会话」的薄导航索引 + 频次普查。

**预筛集合**：
- 复用现有 `candidate_files(window)` 得到窗口内修改过的 jsonl。
- 现有 `S` = 含 `"name":"Skill"` 的文件（评分路径，不动）。
- 新增 `M` = 含 `"name":"Bash"` / `"name":"Write"` / `"name":"Edit"` 之一、但**不在** `S` 中
  的文件（手工工作流候选会话）。批量 `grep -l`，沿用现有 200-文件 batching 与内存纪律。

**每个 M 会话只抽导航所需的最小信息**（薄索引，不是富指纹）：
```json
{
  "file": "<绝对路径>",
  "project": "<顶层目录名>",
  "first_user_msg": "<开场用户诉求，截 ~240 字，已滤系统噪音>",
  "n_turns": "<该会话真实用户发言条数>",
  "cmd_sig": ["<bash 命令头，如 'ffmpeg' / 'git rebase'，封顶 ~15>"],
  "wrote": ["<本会话新建文件的扩展名，如 'py' / 'sh'>"]
}
```

**跨会话频次普查**（Python 算便宜且必须完整的部分）：
- `cmd_census`: `{命令头: 跨会话出现次数}`，降序。

**输出**（挂到同一 JSON 新键，不碰现有键）：
```json
"no_skill_index": {
  "scanned": "<M 集合大小>",
  "truncated": "<被封顶丢弃的会话数，0 表示未截断>",
  "sessions": [ /* 上述对象，按活跃度（n_turns + 工具量）降序，封顶 400 */ ],
  "cmd_census": { /* 命令头 → 跨会话计数 */ }
}
```

**边界与纪律**：
- 封顶 `NO_SKILL_SESSION_CAP = 400`（按活跃度排序优先保留），**截断时在 stderr 记一行**
  （沿用项目「不静默截断」原则，`truncated` 字段也记数）。
- 命令头提取：取 bash 命令的首个有意义 token（必要时含子命令，如 `git rebase`），不抽完整
  命令行（避免泄露路径/密钥，也利于聚合）。
- 沿用现有内存纪律：grep 预筛 → 行式解析 → per-file 状态用完即弃，不整文件读入。

### 4.2 Prompt 改动 `run_skill_insight.sh`

在现有第 6 步之后，新增一个分析步骤（编号顺延），要点：

> 【第 X 步 · 能力缺口（建议新建 skill）】读 `no_skill_index`——**这是索引不是结论**。
> 用 `cmd_census` + 各会话 `first_user_msg` 找出**跨 ≥2 个会话**反复出现的工作流苗头，
> 然后**自主打开 `sessions[].file` 把有苗头的会话读全**（与评分时深挖 `calls[].file` 同理），
> 坐实工作流到底在反复做什么。对每个簇：
> - 先做**路由判断**：这该新建 skill，还是其实该进 CLAUDE.md（全局偏好，如端口约定）/
>   该扩展现有 skill（那归第 6 步触发类）？**只有真·无人覆盖的反复工作流进本节。**
> - 逐个与 `installed`（user_skills + plugin_skills）去重，已存在的不提。
> - 沿用现有「跨会话 ≥2 次才算模式」的阈值，避免把一次性个例写成建议。

### 4.3 报告新增一节

新增 `⑥ 建议新建的 Skill（能力缺口）`，原 ⑥「本期优化建议汇总」顺延为 ⑦，
原 ⑦「trigger 评测集附录」顺延为 ⑧（实现时以 prompt 现状为准微调编号）。

每个候选给出：
- 候选 skill 名 + 一句话职责
- **命中证据**：出现 N 次、跨 M 个项目；2–3 条用户原话（截短，不润色）；典型命令/脚本签名
- 一句**路由判断**（为什么是 skill 而非 CLAUDE.md / 扩展现有）
- `description` 草稿（遵循 pushy 原则：做什么 + 何时用 + 枚举触发语境）
- SKILL.md 主体要点 + 可能的 `scripts/` 草图（脚本该做什么）
- 置信度（高 / 中，基于频次与模式一致性）

## 5. 边界（YAGNI）

- **不**自动建文件、**不**自动调 skill-creator（cron 里写文件风险高）；报告给到「可直接喂
  skill-creator」的草稿即止。
- **不**改 cron / plist / 通知 / 锁 / gate / 失败退避。
- 与现有第 6 步触发类**不重叠**：那节管「已装 skill 漏触发」，本节管「压根没有的 skill」。
- 不引入 embedding / 外部 API：聚类由那一次 agent 调用完成，Python 只做词法层面的频次普查。

## 6. 测试与验证

- **提取器单测**：构造小 jsonl 夹具——含 Skill 的会话、含 Bash 但不含 Skill 的会话、
  既不含 Skill 也不含 Bash 的会话——验证 `M` 预筛正确、`no_skill_index` 的字段、
  `cmd_census` 计数、封顶与 `truncated` 计数、截断 stderr 日志。
- **端到端**：`./run_skill_insight.sh --force` 跑真实数据，人工核对新节：
  - 引用的用户原话能在对应 transcript 溯源（证据真实）；
  - 提议的 skill 不与 `installed` 重复；
  - 路由判断合理（没把该进 CLAUDE.md 的误报为新 skill）。

## 7. 不在本次范围

- 自动生成 / 安装 SKILL.md 文件。
- 跨期追踪「上期建议的新 skill 是否被采纳」（可作为后续迭代，复用现有第 5 步迭代框架）。
- 语义 embedding 聚类。
