# 001 · 共享工作区 决策记录（decisions）

> 状态：草案 v0.1 · 最后更新：2026-11-01 · 基线：AstrBot 4.29.4 @ `831abae85`
> 每条 ADR 固定四段：**决策 / 背景 / 理由 / 备选与影响**。未决问题在 §3。
> 事实依据：`../_investigation/verify/coordinate-verification.md`（63 条断言核对）与 `../_investigation/poc-report.md`（PoC 实测）。

## 1. 决策清单

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| ADR-1 | 采用运行期 monkeypatch，而不依赖任何原生配置 | 已定 |
| ADR-2 | 必须双写（源模块属性 + `sys.modules` 重绑） | 已定 |
| ADR-3 | 顶层替换为主，叶子函数兜底为辅（并接受兜底路径覆盖不到） | 已定 |
| ADR-4 | 不 patch `workspace_path_to_root` | 已定 |
| ADR-5 | 不接管 `computer_use_runtime=sandbox` | 已定 |
| ADR-6 | 危险路径命中时 fail-loud，不回落默认值 | 已定 |
| ADR-7 | 补丁状态挂在 `astrbot.core.workspace` 模块上 | 已定 |
| ADR-8 | 停用后所有插件端点自检并返回 503 | 已定 |
| ADR-9 | 默认共享根 = `<data>/workspaces/astral`，空配置即默认 | 已定 |
| ADR-10 | 不支持多进程/多 worker，文档声明 | 已定 |

---

## ADR-1 采用运行期 monkeypatch

**决策**：在插件 `__init__` 中替换 `astrbot.core.workspace` 的 4 个解析函数，使所有会话返回共享根。

**背景**：AstrBot 4.29.4 **不存在**任何"共享工作区/统一根目录"的原生配置。唯一能指向任意目录的原生能力是 webchat 项目的 `workspace_type=custom`，但：

1. 只对 webchat 生效——`resolve_workspace_root_for_umo` 对非 webchat 在 `parse_webchat_umo` 阶段就返回会话目录，项目/自定义目录永远不会被 Telegram/QQ 会话看到；
2. 需要逐会话加入项目，不是全局开关；
3. 没有自动化入口（全仓库除 Dashboard API 外无代码调用 `create_chatui_project`/`add_session_to_project`）。

**理由**：目标 G1 要求"所有平台、所有群、所有私聊"同一目录，原生路径根本无法覆盖非 webchat 平台，因此补丁是必要条件而非优化。且核心已提供稳定的解析入口（单一函数 + 明确签名），补丁面小、可观测。

**备选**：

| 备选 | 为何不选 |
| --- | --- |
| A 只用配置/DB | 只能按项目或按 UMO 分目录，不满足 G1 |
| B 只 patch 叶子函数 | 覆盖不到顶层入口的 `from-import` 消费者与三处本地重算兜底（见 ADR-3） |
| D 直接改 AstrBot 源码 / 覆盖 `dashboard/dist` | 升级即失效，不可维护；违反 CONSTITUTION P1 |
| 提上游需求（官方改成可配置） | 周期不可控，且不属于本插件能力范围；可作为长期方向 |

**影响**：与 AstrBot 版本强耦合 → 需要 FR-6 的显式失败与 FR-13 的升级回归；用户需知道"这不是官方配置项"。

## ADR-2 必须双写

**决策**：替换时既改 `astrbot.core.workspace` 的模块属性，也扫描 `sys.modules` 把所有仍持有原函数引用的模块全局改写。

**背景**：AstrBot 的调用方（`astr_main_agent.py`、`computer_tools/util.py`、`chatui_project_service.py`）全部是**顶层 `from ... import`**。`from M import f` 在 import 时把函数对象绑定进调用方模块的 globals，函数体里的 `f()` 是 `LOAD_GLOBAL`，取的是调用方自己的 globals。因此只改源模块属性对已导入的调用方**完全无效**，且不会有任何报错——典型的"看起来装上了、实际静默失效"。

**理由**：PoC 实测：负对照（只改模块属性）→ 消费者仍返回原生路径；正对照（双写）→ 消费者返回共享根；补丁之后才 import 的消费者也生效。2026-11-01 用真实 `workspace.py` 在内存中复现 6/6。

**备选**：

| 备选 | 为何不选 |
| --- | --- |
| 只改源模块属性 | 对现有消费者无效（负对照已证伪） |
| 只扫 `sys.modules` 不改源模块 | 补丁之后才 import 的模块仍拿原函数 |
| 用 `importlib.reload` 或重启进程 | 属于改运行环境，代价高且不可逆 |

**影响**：扫描成本 O(模块数)，只在安装/重载时发生；新增函数名必须进 `ALL` 列表，否则会静默漏装（由 `status().consumers` 暴露）。

## ADR-3 顶层替换为主，叶子函数兜底为辅

**决策**：主方案替换 `resolve_workspace_root_for_umo` + `resolve_project_workspace_root`；同时替换 `default_workspace_root` + `project_workspace_root` 作为保险。

**背景**：叶子函数被三类"本地重算"路径绕开：

| 路径 | 位置 | 触发条件 |
| --- | --- | --- |
| `_get_workspace_path_for_umo` 的 fallback | `astr_main_agent.py:439-441` | `db` 非 `BaseDatabase` 或解析抛错 |
| `workspace_root(umo)` | `computer_tools/util.py:48-59` | `workspace_root_for_context` 的兜底分支 |
| `fs._workspace_root(umo)` | `computer_tools/fs.py:112-120` | `current_workspace_root is None`（非 local runtime） |

**理由**：顶层替换在正常路径下短路了全部解析，零成本；叶子替换成本极低且能兜住"第三方插件直接调用叶子函数"的旁路。两者与 `status().consumers` 一起构成可观测的保险。

**备选**：只做顶层（对直接调叶子的第三方插件无效）；只做叶子（三处本地重算仍回落旧目录，主链路会出问题）。

**影响**：必须明确记录"叶子兜底并不覆盖兜底路径"，否则会误判"已完全共享"。`spec.md` 的 E-1 与 AC-3.x 已按此限定。

## ADR-4 不 patch `workspace_path_to_root`

**决策**：`workspace_path_to_root` 保持原生行为，不纳入补丁。

**背景**：Dashboard 在新建/修改项目时用它做路径校验（`chatui_project_service.py:518`）。它决定"用户填的路径是否合法、是否落在允许的子目录内"。

**理由**：接管"解析"是让 Agent 去共享根干活；接管"校验"会让 Dashboard 接受任意路径却按共享根运行，产生"校验通过 ≠ 实际生效"的错位。保持原样可让路径校验继续按原规则生效。

**备选**：一并 patch → 用户填任意路径都被接受，安全边界进一步削弱。

**影响**：Dashboard 里新建的 custom 项目仍会按原规则校验路径；这与"Agent 实际都在共享根"存在语义差异，需在 README 说明（面板/项目配置不再决定 cwd）。

## ADR-5 不接管 sandbox runtime

**决策**：`computer_use_runtime=sandbox` 时不做任何额外处理，只保证 `/astral status` 明确提示"工作区不参与 Agent 执行"。

**背景**：sandbox runtime 下 Agent 在容器内 `/workspace` 干活，宿主机工作区不参与；此时共享根没有任何作用。

**理由**：接管容器内路径不在插件能力范围内（需要改容器运行时），强行处理会引入不可验证的行为。

**影响**：这类用户无法获得 G1 的收益，必须由状态指令明确告知，避免"以为生效了"。

## ADR-6 危险路径 fail-loud

**决策**：共享根命中黑名单时拒绝安装并报错，绝不回落到默认目录。

**背景**：插件无沙箱，等同管理员权限；而 api_key 项目的 4 层防越界在补丁下被部分绕过。因此"共享根指向哪里"是最高风险输入。

**理由**：静默回落会让用户以为配置生效而实际没有，并且掩盖配置错误；显式失败把"我配错了"和"我配对了"区分开。

**备选**：回落默认值 + WARNING（常见做法，但违反 CONSTITUTION P2/P6）；仅记录不阻止（更危险）。

**影响**：配置错误会导致插件"装不上"，需要 README 写清如何修正；CI 需要覆盖被拒路径。

## ADR-7 补丁状态挂在核心模块

**决策**：把 `originals`/`wrappers`/`root` 挂在 `astrbot.core.workspace.__astral_patch_state__`，而不是插件模块变量。

**背景**：插件重载时其模块会被从 `sys.modules` 清理（`star_manager._purge_modules`）；挂在插件模块上的状态会丢，导致 `originals` 丢失、加载后无法正确还原或叠加包装。

**理由**：核心模块不被清理，状态在重载间稳定；同时 `uninstall` 从任意实现侧都能拿到原始函数。

**影响**：会向核心模块写入一个属性（可接受的"最小侵入"）；卸载时删除该属性。

## ADR-8 停用后端点自检 503

**决策**：所有 HTTP 端点在入口检查 `status().installed`，未安装返回 503 + `reason`。

**背景**：`Context.registered_web_apis` 是类属性，停用插件时核心不清空（全仓库无删除路径），因此停用后端点仍可能被路由命中。

**理由**：把"平台缺陷"变成"可预期的 503"，避免停用后仍返回过期/错误的补丁状态。

**影响**：端点不能假设"被调用即已安装"；测试需覆盖停用态（AC-10.1）。

## ADR-9 默认共享根与配置形态

**决策**：`shared_root` 默认空字符串 → `<data>/workspaces/astral`；支持 `~` 与相对路径（相对 `<data>/workspaces`）。

**理由**：默认值必须"开箱即用且不踩敏感目录"；放在 workspaces 下与原生布局同源，便于迁移与备份；相对路径以 workspaces 为基准，避免用户意图被解释成"当前工作目录"（后者随启动方式变化，不可靠）。

**备选**：默认 `~/astral`（可能污染用户主目录）；默认 `<data>/workspaces` 本身（会让原生会话目录与共享根混在一层）。

## ADR-10 不支持多进程部署

**决策**：文档声明只支持单进程（AstrBot 默认）；多 worker 时补丁在各进程内独立生效。

**理由**：补丁是进程内行为，跨进程共享需另一套机制；AstrBot 默认单进程，绝大多数用户不受影响。

**影响**：若用户自行 gunicorn 多 worker，`/astral status` 只能反映当前进程，需在 README 提示。

---

## 2. 被否决方案的保留记录

| 方案 | 否决理由 | 参考 |
| --- | --- | --- |
| 原生 `workspace_type=custom` + 绝对路径 | 只覆盖 webchat，且需逐会话归属、无自动入口 | ADR-1 |
| 只 patch 叶子函数 | 覆盖不到顶层消费者与本地重算兜底 | ADR-2、ADR-3 |
| 改 AstrBot 源码 / 覆盖 `dist` | 升级即失效，违反 P1 | ADR-1 |
| 用 `ASTRBOT_ROOT` 指向共享目录 | 只改 data 根，每个 UMO 仍是子目录 | ADR-1 |
| 把 `filesystem_scope` 改成 `host` 来"共享" | 只是取消边界，不是共享；且扩大风险 | `plan.md` §10 |

## 3. 未决问题

| 编号 | 问题 | 负责人 | 默认处理 | 影响 |
| --- | --- | --- | --- | --- |
| U-1 | harness 完整 73 条断言需在非沙箱环境重跑 | 实现者 | tasks T-0；未出结果前规格标"待验证" | 发布门禁 G-8 |
| U-2 | `sandbox` runtime 下容器内 `/workspace` 是否跨会话共享 | 用户 | 不研究，README 声明不在范围 | 少数用户的能力预期 |
| U-3 | 非 webchat 会话"面板 ≠ cwd"的对外表述 | 用户 | 采用"补丁顺带修复"表述（AC-9.2） | 文档文案 |
| U-4 | 4.29.4 之后的提交是否改变坐标系 | 实现者 | 升级时重跑 FR-13 | 补丁可能失效 |
| U-5 | 是否提供历史目录迁移入口（`POST migrate`） | 用户 | 默认不自动执行，任务 T-17 可选 | 老用户迁移成本 |
| U-6 | 只读页是否也需要展示工作区目录列表（而不只是记忆文件） | 用户 | 首版只展示记忆文件与补丁状态 | 002 的页面范围 |

## 4. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-01 | 首版：10 条 ADR；依据 2026-11-01 坐标核对修正"原生不可行"的证据链 |
