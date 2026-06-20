# skill-insight

一套 macOS 上的**双周定时任务**：每两周自动分析过去若干天的 Claude Code 使用记录，
用 `pi` 跑一份「Skill 使用洞察」报告（逐次调用打分、找漏触发/误触发、给改进建议），
跑完通过桌面横幅通知你。

> 数据来源是本机 `~/.claude` 下的对话记录。报告与中间数据都写在 `skill-log/`，
> 已被 `.gitignore` 排除，**不会进入仓库**。

## 组成

| 文件 | 作用 |
|---|---|
| `run_skill_insight.sh` | 主编排脚本，**自定位**（`BASE_DIR` 取自身所在目录，可随意挪） |
| `extract_skill_data.py` | 预提取：扫 `~/.claude` 把 skill 调用压成一份紧凑 JSON，供 `pi` 读 |
| `com.henry.skill-insight.plist` | launchd 任务模板（安装时改两处路径，见下） |
| `skill-log/` | 输出与状态（日志、报告、cache、标记）——本地、被 gitignore |

## 依赖

- **`pi`** —— LLM CLI，跑分析报告。脚本会把 PATH 扩到 `~/.local/bin:/opt/homebrew/bin:...` 来找它。
- **`terminal-notifier`** —— 桌面横幅通知：`brew install terminal-notifier`
  （首次可能需在「系统设置 → 通知」允许它）。
- **`python3`** —— 跑 `extract_skill_data.py`。
- 读取 `~/.claude/projects/**/*.jsonl`、`~/.claude/history.jsonl`、`~/.claude/skills`、`~/.claude/plugins`。

## 安装

1. 把本文件夹放到任意位置（脚本自定位，不依赖具体路径）。
2. 编辑 `com.henry.skill-insight.plist`，把两处绝对路径改成本机安装位置：
   - `ProgramArguments` 第二项 → `<安装目录>/run_skill_insight.sh`
   - `StandardOutPath` / `StandardErrorPath` → `<安装目录>/skill-log/skill_insight.log`
3. 安装并启动：
   ```sh
   cp com.henry.skill-insight.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.henry.skill-insight.plist
   ```
   卸载：`launchctl unload ~/Library/LaunchAgents/com.henry.skill-insight.plist`

## 手动跑一次

```sh
./run_skill_insight.sh --force     # 绕过节流立刻出报告；不影响定时节奏
```

## 运行机制

- **触发**：每周一 14:00（`StartCalendarInterval`）；外加 `RunAtLoad`——开机/登录也检查一次。
- **双周门槛**：距上次成功 < 13 天则静默跳过（标记 `skill-log/.skill_insight_last_run`）。
  于是有效节奏 ≈ 每两周一次，且天然兜底：错过的周一会在下次开机补跑。
- **数据窗口**：分析「距上次成功的实际天数」，夹在 [14, 28] 天——所以补跑也不漏不重。
- **失败退避**：失败后 12 小时内不重试、不再弹失败通知（标记 `.skill_insight_last_fail`）。
- **单实例锁**：`skill-log/.skill_insight.lock`，超 6 小时的陈旧锁会被抢占。
- **通知**：仅在「出报告 ✓ / 失败 ✗」时弹横幅；跳过/退避静默。

## 排错

- 看日志：`tail -f skill-log/skill_insight.log`
- 查任务状态：`launchctl list com.henry.skill-insight`（`LastExitStatus = 0` 为正常）
- 没收到通知：确认 `terminal-notifier` 已装，且「系统设置 → 通知」里允许其横幅。
