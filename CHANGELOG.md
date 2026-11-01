# 变更记录（CHANGELOG）

> 版本倒序排列。测试命令与环境见 `tests/README.md`。

## v0.1.0 · 2026-11-01

首个公开版本。

### 功能

- **共享工作区**：把所有平台、群和私聊的 Agent 工作区指向同一个共享根；共享根解析、危险路径黑名单、目录骨架、安装失败即回滚，停用插件即还原。
- **文件式记忆**：`SOUL.md` / `MEMORY.md` / `AGENTS.md` / `EXTRA_PROMPT.md` 四件套；首启播种只写空/缺失文件，已有内容不覆盖；对话级冻结快照注入，带字符预算与截断提示。
- **可观测**：`/astral status`、`/astral memory show`、`GET .../state`、`GET .../files`、`POST .../clean`、只读插件页（无写入口）。
- **安全与运维**：危险路径黑名单、配额告警、移动归档（不删除）、隐私披露与备份/恢复说明。

### 验证

- `pytest tests -q`：152 passed / 2 skipped（Windows 无符号链接权限）。
- `pytest tests/test_views_contract.py -q`：19 passed。
- 真实 AstrBot 4.29.4 实例：插件页、`state`/`files`/`clean`、停用后 503 与重新启用均验证通过。
- 另有 73 条验证脚本断言在非沙箱环境通过；脚本与输出未随仓库发布。

### 已知限制

- 不支持多进程 / 多 worker 部署。
- `computer_use_runtime=sandbox` 时工作区不参与 Agent 执行。
- 同一进程内快照冻结，改文件后要 `/new`；重启或重载插件后按当前文件重建。
- 第三方 agent runner 下的注入效果未验证。
- 群聊 `/astral memory show` 只给元信息与前 3 行（隐私要求）。
- 升级 AstrBot 后需按 `docs/dev-notes.md` 的升级回归清单核对源码坐标。
