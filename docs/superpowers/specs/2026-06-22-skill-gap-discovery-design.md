# Skill Gap Discovery — 设计文档

**日期**: 2026-06-22
**状态**: 已批准（v2，并入 Codex 二次评审），待实现
**作者**: Henry + Claude（+ Codex 设计评审）

## 1. 背景与动机

当前 `skill-insight` 回答的问题是 **「你现有的 skill 好不好用」**：`extract_skill_data.py`
用 `grep -l '"name":"Skill"'` 只扫描*包含* skill 调用的会话（约 10% 的文件），把每次调用
当作测试用例评分。报告第 6 步的「触发类」建议也只覆盖**已安装 skill 的漏触发**。

本 feature 增加一个互补的问题 **「你还缺哪些 skill」**：从聊天记录里挖出那些*反复手动做、
却没有任何 skill 接住*的工作流，建议新建 skill。

- 现有能力：**审现有的**（skill 调用 → 评分）
- 新增能力：**找该有的**（手工工作流会话 → 反复工作流 → 建议新建）

**核心技术含义**：当前提取器只看*有* skill 调用的会话；发现新 skill 机会要看*手工干活*的
会话（含 Bash/Write/Edit，约 60% 的文件、GB 级）。这是一次方向相反的数据采样，是真正的
新能力，不是改改 prompt 就行。

## 2. 设计决策（已确认）

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 集成方式 | **现有报告加一节** | 复用同一 biweekly cron / 同一份报告 / 同一次 `pi -p` 调用，改动最小，一次运行两件事都看 |
| 核心信号 | **反复手工多步工作流** | 「你老在手动做这件事」是最硬的信号；跨会话复现的同类多步任务、Claude 每次重新手写的同类脚本 |
| 输出深度 | **候选清单 + 证据 + description 草稿** | 与现有报告「可粘贴 SKILL.md 文案」的粒度对齐；不自动建文件 |
| 自主性分工 | **薄索引 + 高自主** | Python 做廉价完整的*普查/索引*（贵且必须完整的部分），agent 自主*深挖判断*（它强的部分） |
| 索引体积 | **token 预算有界，不按会话数封顶** | 武断的数量上限不好，但完全不封顶（≈26 万 token）违背「让 agent 读得便宜」。改为按序列化体积卡 |

### 2.1 为什么是「薄索引 + 高自主」而非纯自主

`extract_skill_data.py` 存在的理由，README 写得很直白：「compress GBs of logs into one
compact JSON **so the agent reads cheaply**」。对*更简单*的「评测已有 skill」任务，作者已经
判断过不能让 agent 裸奔 GB 级 jsonl。新任务的干草堆更大，纯自主的风险更突出：

1. **成本/耗时不可控** — cron 无人值守，agent 一个 `cat` 撞上几 MB jsonl 就能炸 context
   或烧掉大量 token。现有 prompt 那句「不要整文件读入大 jsonl」是*预提取之上的护栏*，
   不是预提取的替代。
2. **不确定性** — 单次 agent pass 天然倾向 under-explore / 早停，记分卡式报告最怕跑一次很全、
   下次很懒的方差。
3. **覆盖盲区** — 「同一串命令跨 N 个会话复现」必须*跨文件聚合计数*；agent 临时 grep 只会
   撞见一部分簇，给不出完整的跨会话频次普查。

因此 Python 退成「**导航索引 + 频次普查**」——不是 agent 拿去聚类的有损摘要，而是告诉
agent *哪些*会话值得看、各类工作流出现*多少次*；然后 **agent 自主把有苗头的会话读全**，
自己聚类、判断、写提案。这正是项目现有范式：评测已有 skill 时 Python 出 `calls[]`，但 prompt
明确允许 agent「拿不准时打开 `calls[].file` 精读」。

### 2.2 索引有界的取舍（v2，并入 Codex 评审）

用户实测窗口（28 天）：2441 个会话，235 个含 Skill，**1548 个含 Bash/Write/Edit**（手工
工作流候选）。薄索引每会话 ~200 token，若全量铺开 ≈26 万 token，注意力稀释、成本/方差都
不可接受。结论：**保留有界，但否决「按数量封顶 400」**（魔法数 + 武断截断）。改为：

- `cmd_census` **永不封顶**（体积只取决于不同命令头数量，与会话数无关），且加料。
- 意图轴（`first_user_msg`）**先去重成意图组，再按 token 预算填充**（按实际序列化体积卡，
  目标整个 `no_skill_index` ≈ 60–80K token）。
- 选取在预算内给「只有 Write/Edit、无特征命令的会话」「不同项目」留配额；活跃度只当
  tie-breaker（短而反复的工作流往往比长会话更是好候选）。
- **故意不做**长尾轮转采样（rotating tail sample）：biweekly 数据窗口本身在滚动、天然轮转，
  这层复杂度不值当（YAGNI）。

### 2.3 关闭整会话排除盲区（Codex 指出）

原 v1 把含 Skill 的会话整个剔除（`M = 含工具 − 含Skill`），会丢掉「先用了个不相关 skill、
后面又手动干了一段该固化成新 skill 的活」这类混合会话的证据。v2 改为：**`M = 所有含
Bash/Write/Edit 的会话**（不减去含 Skill 的），每条标 `has_skill: true/false`，混合会话交给
agent drill-in 时自行识别该会话里的手工工作流段落。完整的 episode/span 级切分（在一个会话内
精确分出 skill 段与手工段）成本高，列为后续，不在 v1。

## 3. 架构与数据流

```
extract_skill_data.py ──同一次提取、同一份 JSON──► pi -p（同一次调用）──► 同一份报告 + 新增一节
   既出 calls[]（现有，不动）                         prompt 加一个分析步骤        ⑥ 建议新建的 Skill
   新增 no_skill_index（有界薄索引 + 普查）                                       （能力缺口）
```

cron / plist / 锁 / gate / 通知 / 失败退避 —— **全不动**。

## 4. 组件设计

### 4.1 提取器改动 `extract_skill_data.py`

**职责单一**：在现有提取流程旁，多产出一份「含工具会话」的有界薄导航索引 + 命令频次普查。

**预筛集合**：
- 复用现有 `candidate_files(window)` 得到窗口内修改过的 jsonl。
- 现有 `S` = 含 `"name":"Skill"` 的文件（评分路径，不动）。
- 新增 `M` = 含 `"name":"Bash"` / `"name":"Write"` / `"name":"Edit"` 之一的文件（**不**减去
  `S`，每条用 `has_skill` 标注是否同时含 skill）。批量 `grep -l`，沿用现有 200-文件 batching
  与内存纪律。

**每个 M 会话只抽导航所需的最小信息**（薄索引，不是富指纹）：
```json
{
  "file": "<绝对路径>",
  "project": "<顶层目录名>",
  "has_skill": false,
  "first_user_msg": "<开场用户诉求，截 ~240 字，已滤系统噪音>",
  "n_turns": "<该会话真实用户发言条数>",
  "cmd_sig": ["<bash 命令头（泛命令带子命令），封顶 ~15>"],
  "wrote": ["<本会话新建文件的扩展名，如 'py' / 'sh'>"],
  "bytes": "<会话 jsonl 字节数，供 agent 判断大文件，避免一次读全>"
}
```

**命令头规整（避免泛命令噪声）**：
- 取 bash 命令首个有意义 token；对 `git` / `docker` / `npm` / `pip` / `cargo` / `kubectl` 等
  泛命令头，**带上子命令**（`git rebase` / `docker build` / `npm run`），单独的泛头区分度太低。
- 不抽完整命令行（避免泄露路径/密钥，也利于聚合）。

**命令频次普查 `cmd_census`（永不封顶，加料）**：
```json
"cmd_census": {
  "git rebase": { "sessions": 18, "projects": 6, "examples": ["<file>", "..."] },
  ...
}
```
- `sessions`：跨会话出现次数；`projects`：**跨项目数**（单项目反复 → 多半该进该项目
  CLAUDE.md，跨项目反复才 → 全局 skill，这是路由判断的关键廉价信号）；`examples`：3–5 个
  代表会话路径供 agent 直接 drill-in。

**意图组 `intent_groups`（去重 + token 预算填充）**：
- 把 M 会话按 `first_user_msg` 的廉价规整 key（小写、去空白、token 集合 / 前缀）聚成组，
  近乎重复的诉求压成一条带计数的组，而非逐条铺开或丢弃：
```json
"intent_groups": [
  { "representative_msg": "<代表诉求，截短>", "similar_sessions": 9, "projects": 4,
    "examples": ["<file>", "..."], "no_distinctive_cmd": true }
]
```
- 选取按组大小（=复现强度，本节要的信号）排序填充，预算内给「`no_distinctive_cmd`（只有
  Write/Edit、无特征命令）」「不同项目」留配额；活跃度仅 tie-breaker。
- 按**实际序列化体积**卡预算（目标整个 `no_skill_index` ≈ 60–80K token）。

**索引元信息与诚实截断**：
```json
"no_skill_index": {
  "scanned": "<M 集合大小>",
  "selected": "<进入 intent_groups 的会话数>",
  "omitted": "<预算外丢弃的会话数>",
  "estimated_tokens": "<no_skill_index 估算 token>",
  "cmd_census": { ... },
  "intent_groups": [ ... ]
}
```
- 截断**既写 stderr 也落 `omitted`/`estimated_tokens` 字段**；且 prompt 要求**报告正文显式
  声明本次有实质截断**（无人值守，没人看 stderr）。

**纪律**：沿用现有内存纪律——grep 预筛 → 行式解析 → per-file 状态用完即弃，不整文件读入。

### 4.2 Prompt 改动 `run_skill_insight.sh`

在现有第 6 步之后新增分析步骤（编号顺延），要点：

> 【第 X 步 · 能力缺口（建议新建 skill）】读 `no_skill_index`——**这是索引不是结论**。
> - 用 `cmd_census`（看 `sessions`/`projects`）+ `intent_groups`（看 `similar_sessions`/
>   `projects`）找出**跨 ≥2 会话**反复的工作流苗头；**跨 ≥2 项目**才更可能是全局 skill，
>   单项目反复优先考虑该项目 CLAUDE.md。
> - **最小取证策略（防 under-explore）**：每个进入报告的候选簇，必须实际打开 `examples` 里
>   **≥2 个会话**读全坐实工作流，并引用真实用户原话；只凭索引不开会话不得下结论。
> - **大文件护栏**：`bytes` 偏大的会话用 `grep`/`head` 定点看，不要整文件读入。
> - 对每个簇做**路由判断**：该新建 skill / 该进 CLAUDE.md（全局或项目级偏好）/ 该扩展现有
>   skill（那归第 6 步触发类）——**只有真·无人覆盖的反复工作流进本节**；逐个与 `installed`
>   去重，已存在的不提。
> - 若 `omitted > 0`（有实质截断），在本节开头一句声明覆盖范围。

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

节首：若有实质截断，一句覆盖范围声明（`scanned`/`selected`/`omitted`）。

## 5. 边界（YAGNI）

- **不**自动建文件、**不**自动调 skill-creator；报告给到「可直接喂 skill-creator」的草稿即止。
- **不**改 cron / plist / 通知 / 锁 / gate / 失败退避。
- 与现有第 6 步触发类**不重叠**：那节管「已装 skill 漏触发」，本节管「压根没有的 skill」。
- 不引入 embedding / 外部 API：语义聚类由那一次 agent 调用完成，Python 只做词法层面的
  频次普查与诉求去重。
- **不做**长尾轮转采样（biweekly 窗口天然滚动）。
- **不做**会话内 episode/span 级精确切分（v1 用 `has_skill` 标注 + agent drill-in 近似）。

## 6. 测试与验证

- **提取器单测**：构造小 jsonl 夹具——含 Skill 的会话、含 Bash 但不含 Skill 的会话、
  含 Bash 且含 Skill 的混合会话、只有 Write/Edit 无 Bash 的会话——验证：
  - `M` 预筛包含混合会话且 `has_skill` 标注正确；
  - `cmd_sig` 泛命令头带子命令、`cmd_census` 的 `sessions`/`projects`/`examples` 计数正确；
  - `intent_groups` 近乎重复诉求被聚合、`no_distinctive_cmd` 标注正确；
  - token 预算填充与 `scanned`/`selected`/`omitted`/`estimated_tokens` 一致、截断写 stderr。
- **端到端**：`./run_skill_insight.sh --force` 跑真实数据，人工核对新节：
  - 引用的用户原话能在对应 transcript 溯源（证据真实）；
  - 提议的 skill 不与 `installed` 重复；
  - 路由判断合理（没把单项目偏好误报为全局新 skill）；
  - 有截断时报告正文如实声明覆盖范围。

## 7. 不在本次范围（后续可迭代）

- 自动生成 / 安装 SKILL.md 文件。
- 会话内 episode/span 级精确切分（更彻底地从混合会话里只取手工段落）。
- 跨期追踪「上期建议的新 skill 是否被采纳」（复用现有第 5 步迭代框架）。
- 语义 embedding 聚类、长尾轮转采样。
