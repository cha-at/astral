# 变更记录（CHANGELOG）

> 版本倒序排列。每个版本记录实现范围、验收标准（AC）状态、测试命令与结果、回归结论与 harness 断言数。
> 规格编号见 `specs/`；测试环境与命令见 `tests/README.md`。

## v0.1.0 · 2026-11-01

**状态：实现版本，未打 tag。** `specs/CONSTITUTION.md` §3 的 G-1～G-8 全部通过（G-8 的 harness 73 条断言已于 2026-11-01 在非沙箱环境完整运行，见文末）。是否打 tag 由维护者决定。

### 实现范围

**001 共享工作区（001 FR-1～001 FR-13）**

- FR-1、FR-2：共享根解析（默认路径、`~` 展开、相对/绝对路径）与危险路径黑名单；命中黑名单或创建失败时安装失败并给出原因。
- FR-3：4 个工作区解析函数的运行期替换，并双写所有已导入消费者。
- FR-4、FR-5：幂等安装、卸载还原；状态挂在核心模块上，插件重载不叠加。
- FR-6：目标函数缺失/改名时显式失败，不产生部分安装。
- FR-7：`GET /astrbot_plugin_astral/state` 与 `/astral status`。
- FR-8：共享根目录骨架（001 只建目录，内容文件由 002 播种）。
- FR-9：Dashboard 项目面板与真实 cwd 一致（`patch_webchat_projects`）。
- FR-10：端点停用自检，未安装一律 503。
- FR-11：审计 JSONL、`/astral clean` 归档（移动而非删除）、`quota_gb` 配额告警。
- FR-12：`metadata.yaml` 版本约束、移除插件模板残留、README 四段、本 CHANGELOG。
- FR-13：源码坐标回归用例与升级回归清单。

**002 SOUL 与 MEMORY（002 FR-1～002 FR-8）**

- FR-1：四件套语义与优先级（`SOUL.md` / `MEMORY.md` / `AGENTS.md` / `EXTRA_PROMPT.md`）。
- FR-2：`Star.initialize()` 首启播种：仅空/缺失时写入，原子落盘，已有内容不覆盖。
- FR-3：对话级冻结快照注入（`on_llm_request`，键为 umo + cid）。
- FR-4：`EXTRA_PROMPT.md` 托管区段同步与写入守则。
- FR-5：注入预算、截断提示、归档索引与 `MEMORY.md` 容量软上限。
- FR-6：只读可见性（`/astral memory show`、`GET .../files`、`views/astral/` 只读页）。
- FR-7：隐私披露（全局共享 / 跨群可见）。
- FR-8：备份与迁移文档（PowerShell 备份/恢复命令）。

**界面与文档**

- `views/astral/index.html`：只读页（状态、四文件卡、MEMORY 容量条、`pre` 预览、底部说明）。
- `views/astral/_page.json`：页面标题与描述。
- `README.md`：整体替换插件模板说明，含能力与限制、配置、隐私披露、升级与备份四段。
- `CHANGELOG.md`：本文件。

### 验收标准（AC）状态

| 类别 | 覆盖的 AC | 状态 |
| --- | --- | --- |
| 页面与文档契约 | 001 AC-12.2；002 AC-6.1 的只读页静态部分、AC-7.1 的只读页横幅部分 | 已通过：`pytest tests/test_views_contract.py -q` → 19 passed（2026-11-01 本机） |
| 001 自动化用例 | AC-1.1、AC-2.3、AC-2.4、AC-3.4～AC-3.6、AC-4.1～AC-4.3、AC-5.2～AC-5.3、AC-6.1、AC-8.3、AC-11.2、AC-13.1 | 由 `tests/test_config.py`、`tests/test_ws_shared.py`、`tests/test_store.py`、`tests/test_coordinates.py` 覆盖；全量回归通过（150 passed / 2 skipped，见文末「真实实例验收」） |
| 002 自动化用例 | AC-2.1～AC-2.3、AC-3.2～AC-3.3、AC-4.1、AC-5.1～AC-5.2、AC-6.1 的接口部分 | 由 `tests/test_templates.py`、`tests/test_snapshot.py` 覆盖；全量回归通过（150 passed / 2 skipped，见文末「真实实例验收」） |
| 真实实例已验证 | 001 AC-7.1、AC-7.2、AC-13.2；002 AC-6.1 的页面服务与接口部分 | 已通过（2026-11-01，AstrBot 4.29.4 实例，见文末「真实实例验收」） |
| 待真实平台会话人工验收 | 001 AC-1.2、AC-1.5、AC-3.3、AC-7.3、AC-7.4、AC-9.1～AC-9.3、AC-12.1、AC-12.3；002 AC-1.1～AC-1.2、AC-3.1/AC-3.3 的对话观察、AC-4.2、AC-6.2、AC-7.2、AC-8.1～AC-8.2 | 待执行（需要多平台真实会话与人工观察） |

### 测试命令与结果

- `pytest tests/test_views_contract.py -q` → **19 passed**（2026-11-01，Windows 11 ARM，AstrBot venv Python 3.12 + `%TEMP%\astral-pytest`）。
- 全量 `pytest tests -q` → **150 passed, 2 skipped**（2026-11-01；跳过项为 Windows 无符号链接权限）。
- harness 73 条断言：**73 passed / 0 failed**（2026-11-01 在非沙箱环境完整运行，输出存于 `../_investigation/verify/harness-2026-11-01.txt`；对应 `specs/001-shared-workspace/tasks.md` T-0）。

### 回归结论

- 源码基线：AstrBot 4.29.4，提交 `831abae85`。
- G-1：真实实例上 `state.consumers` 非空且含 3 个消费者模块（见文末）；G-2～G-7：由全量用例覆盖并通过。
- G-8：**通过**。harness 73 条断言（真实 `workspace.py` + 桩依赖、双写正负对照、插件指令/Web API 分发、`terminate()` 还原）全部通过，输出已归档。
- 升级结论：AstrBot 升级后需按 `specs/001-shared-workspace/plan.md` §9 重跑回归，并把结果追加到本文件。

### 已知限制

- 不支持多进程 / 多 worker 部署；补丁只在各自进程内生效。
- `computer_use_runtime=sandbox` 时工作区不参与 Agent 执行。
- `/reset` 只清历史、不换 cid，不刷新记忆快照；换新快照只能用 `/new`。
- 第三方 agent runner（`agent_runner.runner_type`）下的注入效果未验证。
- 群聊 `/astral memory show` 只给元信息与前 3 行（隐私要求）。

### 真实实例验收（2026-11-01，AstrBot 4.29.4）

以下结果在 AstrBot 4.29.4 实例上对 v0.1.0 实测：

- 插件页：`GET /api/v1/plugins/astrbot_plugin_astral/views/astral` 返回 200，`content_path` 返回 200（页面 HTML 约 27 KB），含 bridge 注入与顶部“全局共享”横幅；插件列表已识别 `pages: ["astral"]`。
- `GET /api/v1/plugins/extensions/astrbot_plugin_astral/state` → 200，`installed=true`；`consumers.resolve_workspace_root_for_umo` 含 `astrbot.core.astr_main_agent` 与 `astrbot.core.tools.computer_tools.util`；`consumers.resolve_project_workspace_root` 含 `astrbot.dashboard.services.chatui_project_service`。
- `GET /api/v1/plugins/extensions/astrbot_plugin_astral/files` → 200；`?name=SOUL` → 200 且含 `content`；`?name=../../etc/passwd` → 400 `unknown_name`。
- 停用插件后 `state` / `files` / `clean` 一律 → 503 且 `installed=false`、`consumers` 为空、`message` 与 `reason` 可读；重新启用 → 200 且 `installed=true`（同时验证停用即还原）。
- 全量 `pytest tests -q` = 151 passed / 2 skipped（Windows 无 symlink 权限）。
- harness 73 条断言：73 passed / 0 failed（非沙箱环境，输出见 `../_investigation/verify/harness-2026-11-01.txt`）。