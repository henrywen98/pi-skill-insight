#!/bin/zsh
# Biweekly skill-usage insight report.
# Invoked by launchd (com.henry.skill-insight) every Monday 14:00. A last-success
# marker keeps the effective cadence at one run per >=13 days — this survives
# 53-ISO-week years and self-heals after missed Mondays (machine powered off):
# the next Monday trigger sees the marker is old enough and runs.
# Manual preview run (bypasses the gate, does NOT shift the schedule):
#   ./run_skill_insight.sh --force
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${BASE_DIR}/skill-log"
mkdir -p "$OUT_DIR"
REPORT_FILE="${OUT_DIR}/skill_usage_report_$(date +%Y-%m-%d).md"
LOG="${OUT_DIR}/skill_insight.log"
MARKER="${OUT_DIR}/.skill_insight_last_run"
FAIL_MARKER="${OUT_DIR}/.skill_insight_last_fail"
LOCK="${OUT_DIR}/.skill_insight.lock"

now=$(date +%s)

# desktop notification helper — fired only on real outcomes (report done / failed)
# so Henry knows the launchd job actually ran. Skips/backoff stay silent.
notify() {
  # terminal-notifier posts a real banner — osascript/Script Editor banners are
  # suppressed on this machine. Args pass through directly (CJK/quotes safe);
  # never fatal under set -e. $1=message, $2=subtitle.
  /opt/homebrew/bin/terminal-notifier \
    -title "Skill Insight 定时任务" -subtitle "${2:-}" -message "$1" -sound Glass \
    >/dev/null 2>&1 || true
}

# biweekly gate: skip unless >=13 days since last successful scheduled run
if [[ "${1:-}" != "--force" ]] && [[ -f "$MARKER" ]]; then
  last=$(cat "$MARKER")
  if (( now - last < 13 * 86400 )); then
    # throttle-skip is silent: with RunAtLoad this fires on every login — log only
    echo "$(date '+%F %T') last run $(( (now - last) / 86400 ))d ago (<13d), skipping" >> "$LOG"
    exit 0
  fi
fi

# failure backoff: after a failed run the success-marker stays old, so the gate
# above keeps saying "due" — without this, RunAtLoad would retry + re-notify on
# every login while pi is broken. Hold off 12h after a failure. --force overrides.
if [[ "${1:-}" != "--force" ]] && [[ -f "$FAIL_MARKER" ]]; then
  lastfail=$(cat "$FAIL_MARKER")
  if (( now - lastfail < 12 * 3600 )); then
    echo "$(date '+%F %T') last attempt failed $(( (now - lastfail) / 3600 ))h ago (<12h), backing off" >> "$LOG"
    exit 0
  fi
fi

# single-instance lock; a lock dir older than 6h is from a crashed run — steal it
if ! mkdir "$LOCK" 2>/dev/null; then
  if (( now - $(stat -f %m "$LOCK") > 6 * 3600 )); then
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') lock contention, skipping" >> "$LOG"; exit 0; }
  else
    echo "$(date '+%F %T') another run in progress, skipping" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# data window follows the actual gap since last success, clamped to [14, 28] days
# (Claude Code prunes transcripts after ~30 days, so >28 buys nothing)
WINDOW=14
if [[ -f "$MARKER" ]]; then
  WINDOW=$(( (now - $(cat "$MARKER") + 86399) / 86400 ))
  (( WINDOW < 14 )) && WINDOW=14
  (( WINDOW > 28 )) && WINDOW=28
fi

# real (unclamped) gap since last success, only for the success notification text;
# blank on the very first run. Data coverage itself is driven by WINDOW above.
GAP_DAYS=""
[[ -f "$MARKER" ]] && GAP_DAYS=$(( (now - $(cat "$MARKER")) / 86400 ))

export PATH="/Users/henry/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# pre-extract the heavy data so pi reads one compact JSON instead of GBs of jsonl
EXTRACT="${OUT_DIR}/.cache/skill_extract.json"
echo "$(date '+%F %T') extracting (window ${WINDOW}d)" >> "$LOG"
if ! nice -n 19 python3 "${BASE_DIR}/extract_skill_data.py" \
    --window "$WINDOW" --out "$EXTRACT" >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') FAILED at extraction — marker untouched, retries next Monday" >> "$LOG"
  echo "$now" > "$FAIL_MARKER"
  notify "数据提取失败 ✗" "12 小时后自动重试（或手动 --force）"
  exit 1
fi

PROMPT=$(cat <<EOF
你是一个每两周定时运行的「Skill 使用洞察」评测任务。方法论参考 skill-creator 的评测循环：把每次真实的 skill 调用当作一个测试用例——逐例评分（grader）、量化汇总（benchmark）、与基线和上一期对比（iteration）、分析师找隐藏模式（analyzer）、最后产出可验证的改进建议。与 skill-creator 的区别：它用人造测试用例做主动实验，你用 Henry 过去 ${WINDOW} 天的真实对话记录做被动评测——所有证据来自用户的实际行为，不许臆测。

背景：Henry 的习惯是，某个 skill 效果不好时，他会在调用之后连续发多条消息手动纠正和指引。这些「调用后的人工指引」每一条都是 skill 没写到位的证据，是本评测最核心的信号源。

数据源——主数据已经预提取好了，优先用它，省时省资源：
- ${EXTRACT} —— 预提取 JSON（本次运行刚生成）。结构：
  * per_skill_summary：每 skill 的 calls / sessions / projects / subagent_calls / explicit_trigger
  * calls[]：每次调用一条，含 skill、ts、project、in_subagent、trigger_cmd（非空=用户显式 /命令触发）、prompt_before（触发前最后一条用户发言）、after_user_msgs[]（调用后最多 6 条真实用户发言，系统噪音已滤掉，text 截断到 800 字）、same_file_repeats（同会话内该 skill 被调次数）、file（原始 transcript 路径）
  * explicit_slash_counts：窗口期内用户手动输入的 /命令 统计
  * installed：已安装的 user_skills 和 plugin_skills 清单
- 原始 transcript（calls[].file 指向的 jsonl）—— 仅在评分拿不准、需要更完整上下文时才打开个别文件精读；不要重新全量扫描
- ~/.claude/skills/<name>/SKILL.md —— 需要给具体修改文案时读对应 skill 的现状
- ${OUT_DIR}/ —— 历期报告（skill_usage_report_*.md），用于第 5 步迭代对比
- ~/.claude/history.jsonl 与 /Users/henry/.claude/skills/claude-memory/scripts/search.py —— 仅第 3 步（找未触发 skill 的同类任务会话做基线）和第 6 步（扫漏触发场景）需要查非 skill 会话时用，按关键词定向检索，不要全量扫描

评测流程：

【第 1 步 · 圈定用例】直接读 ${EXTRACT} 的 per_skill_summary 和 explicit_slash_counts 得到全部统计，不要自己扫 jsonl。

【第 2 步 · 逐例评分（grader）】优先覆盖 Henry 的自定义 skill（installed.user_skills 里的，无插件命名空间），其次插件 skill。对 calls[] 里的每次调用，依据 after_user_msgs 和 same_file_repeats，按固定评分表打分：
  - A 成功：无人工干预，或仅确认性回复（"好/可以/继续"）
  - B 轻度干预：1-2 条纠正（"不对/改成…"）或补充约束（"注意要…/不要…"）
  - C 重度干预：>=3 条纠正，或用户手把手拆步骤（"你先…然后…再…"），或同一会话内重调同一 skill（重试信号）
  - D 失败：[Request interrupted] 后放弃/换路子，或用户最终自己给出做法/答案
  每个 B/C/D 评分必须附用户原话证据（截短即可）。汇总每个 skill 的「干预率」=(B+C+D)/总数、「失败率」=D/总数。

【第 3 步 · 基线对照】对干预率最高的 2-3 个 skill：在记录里找「同类任务但该 skill 未触发」的会话作为基线，对比有无 skill 的结果差异——skill 到底是在帮忙还是在帮倒忙？（skill-creator 的 with/without 对照思想，搬到观察数据上）

【第 4 步 · 分析师视角（analyzer）】找汇总数字掩盖的模式：
  - 无区分度的干预：几乎所有 skill 调用后都出现的同类纠正 → 系统性问题（该进 CLAUDE.md 或全局配置），不要算到单个 skill 头上
  - 高方差 skill：同一 skill 有时 A 有时 D → 找出语境差异（什么项目/任务类型下失效），这往往意味着 skill 该声明适用边界
  - 重复劳动：skill 触发后 Claude 跨会话反复手写相似的辅助脚本或重复同样的多步操作 → 该固化为这个 skill 的 scripts/ 捆绑脚本，指出脚本该做什么

【第 5 步 · 上期建议追踪（iteration）】找 skill-log/ 里最近的上一份报告：上期建议逐条核对——对应 skill 的 SKILL.md/description 改了没有（已采纳/未采纳）？已采纳的，该 skill 本期干预率较上期升还是降（见效/未见效）？没有上期报告就跳过本步。这是闭环里最重要的一节：没有它，建议永远只是建议。

【第 6 步 · 改进建议（improve）】按优先级排序，分两类：
  - 执行类（改 SKILL.md 正文）：依据第 2/4 步。同一 skill 跨会话 >=2 次的同类指引才算缺陷模式；给出可直接粘贴的具体文案，从模式归纳「通用原则 + 解释 why」，不要把单次个例写成 ALWAYS/NEVER 死规则（过拟合）；模型有 theory of mind，讲清楚为什么比堆大写命令更有效。
  - 触发类（改 description）：对照已安装清单列出近 ${WINDOW} 天零调用的 skill；粗扫用户 prompt 找「本该触发却没触发」的漏触发场景。建议遵循 pushy description 原则：Claude 天然倾向 under-trigger，description 要「做什么」+「什么时候用」+ 枚举具体触发语境（用户常说的词、文件类型、场景），宁可偏推销也不要含蓄。

【第 7 步 · trigger 评测集】把漏触发用户原话（should_trigger: true）和误触发场景原话（should_trigger: false）按 skill 整理成 JSON 数组附录：[{"query": "<用户原话>", "should_trigger": true/false}, ...]。原话不要改写润色（最多截短）。这份评测集后续会喂给 skill-creator 的 description 自动优化循环。

输出要求：把完整报告写入 ${REPORT_FILE}（中文 Markdown），结构为：
① 本期记分卡（skill / 调用次数 / A-D 分布 / 干预率 / 失败率 / 项目数 / 触发方式）
② 逐 skill 干预分析（问题 skill 各一小节：评分明细、干预模式聚类、用户原话证据、建议粘贴进 SKILL.md 的具体文案）
③ 基线对照与分析师发现（系统性问题 / 高方差 / 重复劳动）
④ 上期建议追踪（已采纳·见效 / 已采纳·未见效 / 未采纳，逐条）
⑤ 本期优化建议汇总（执行类 vs 触发类，按优先级，附依据）
⑥ 附录：按 skill 分组的 trigger 评测集 JSON
篇幅向 ② 和 ④ 倾斜。

注意：transcripts 是 GB 级数据，全部用 find/grep/wc 等管道做统计，不要整文件读入大 jsonl。
EOF
)

echo "$(date '+%F %T') starting skill insight run (window ${WINDOW}d) -> ${REPORT_FILE}" >> "$LOG"
if nice -n 19 pi -p --no-session "$PROMPT" >> "$LOG" 2>&1; then
  # --force runs are previews and must not shift the biweekly anchor
  [[ "${1:-}" != "--force" ]] && echo "$now" > "$MARKER"
  rm -f "$FAIL_MARKER"
  echo "$(date '+%F %T') finished OK" >> "$LOG"
  notify "运行完成 ✓ 报告已生成${GAP_DAYS:+（距上次 ${GAP_DAYS} 天）}" "${REPORT_FILE##*/}"
else
  rc=$?
  echo "$(date '+%F %T') FAILED (exit $rc) — marker untouched, retries next Monday" >> "$LOG"
  echo "$now" > "$FAIL_MARKER"
  notify "运行失败 ✗ (exit ${rc})" "12 小时后自动重试（或手动 --force）"
  exit $rc
fi
