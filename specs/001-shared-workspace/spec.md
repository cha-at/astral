# 001 · 共享工作区 需求规格（spec）

> 状态：草案 v0.1 · 最后更新：2026-11-01 · 基线：AstrBot 4.29.4 @ `831abae85`
> 依赖：无（本包是地基） · 下游：`002-soul-and-memory`
> 实现方案见同目录 `plan.md`，任务拆分见 `tasks.md`，取舍理由见 `decisions.md`。
> 术语一律按 `specs/CONSTITUTION.md` §5；源码坐标一律引用 `plan.md` §8，本文不自行给行号。

## 1. 背景与目标

AstrBot 的"工作区"不是独立服务，而是"一条 DB 记录 + 一个磁盘目录 + 每次请求现算的 cwd"。原生行为按会话（UMO）分目录；只有 webchat 的"项目"能指定自定义目录，且只对 webchat 生效（`parse_webchat_umo` 对非 webchat 直接返回会话目录）。因此 Telegram / QQ / 飞书 / 私聊之间**无法共享任何文件**，Agent 每换一个群就换一个"记忆位置"，用户也无法在一个地方维护公共资产。

本规格要把这件事实质性地改掉：所有平台、所有群、所有私聊共享同一个工作区根目录，且这套改造必须**可观测、可回滚、不修改 AstrBot 源码**。

### 1.1 目标（与 FR 的映射）

| 编号 | 目标 | 对应需求 |
| --- | --- | --- |
| G1 | 所有 bot / 所有群 / 所有私聊共享同一个工作区根 | FR-3 |
| G2 | 原生"按 UMO 分目录 / 按项目分目录"被完全替换 | FR-3、FR-5 |
| G3 | 共享根可配置，非法配置立即报错 | FR-1、FR-2 |
| G4 | 改造可观测、可回滚 | FR-4、FR-5、FR-7 |
| G5 | 共享根是一个"开箱可用"的工作区（有说明、有约定、有归档） | FR-8、FR-11 |

### 1.2 成功判据（一句话）

在同一个 AstrBot 实例上，Telegram 群、QQ 群、私聊各让 Agent 执行一次 `pwd`，三者返回同一路径；在一个群里写的文件，另一个群能读到。

## 2. 非目标

1. 不改内置 WebUI（不加按钮、不加 tab、不替换工作区文件面板）；Astral 的界面只能是自己的插件页。
2. 不覆盖内置 HTTP 路由；插件 API 只能注册在自己的前缀下。
3. 不做多进程 / 多 worker 支持（AstrBot 默认单进程；用户自行多 worker 时补丁只在各自进程内生效，README 说明）。
4. 不接管 `computer_use_runtime=sandbox`（Agent 在容器内 `/workspace` 干活，与宿主机工作区无关）。
5. 不做 webchat 的"项目"产品功能迭代；只要保证 Dashboard 面板与真实 cwd 一致（FR-9）。
6. 不做记忆与人格（见 `002-soul-and-memory`）。
7. 不提供 WebUI 内的文件上传/在线编辑（官方只读接口的既有限制）。

## 3. 用户与场景

**用户画像**：自己部署 AstrBot、接了 ≥2 个平台、有多个群，希望 Agent 在任何群里都能读写同一批资料。

| 场景 | 现状 | 目标行为 |
| --- | --- | --- |
| S1 多群协作 | A 群写的脚本，B 群看不到 | B 群的 Agent 直接能读到并使用 |
| S2 公共资产 | 每个群各维护一份提示词/知识文件 | 共享根下一份 `AGENTS.md` + `shared/`，全体生效 |
| S3 排障 | 升级后不确定补丁是否还生效 | `/astral status` 一眼看出"装没装、根在哪、谁在走补丁" |
| S4 回滚 | 想临时回到原生按群分目录 | 停用插件即恢复，无需改配置 |
| S5 面板对账 | Dashboard 面板显示的目录与 Agent 实际 cwd 不一致 | 面板与 cwd 指向同一处（webchat 见 AC-9.1，非 webchat 见 AC-9.2） |

## 4. 功能需求与验收标准

### FR-1 共享根解析与创建

`shared_root` 配置按下列规则解析，并在安装补丁前创建目录：

1. 空字符串 → `<AstrBot data>/workspaces/astral`；
2. `~` 与 `~/x` → 展开为当前用户主目录；
3. 相对路径 → 相对 `<data>/workspaces`；
4. 绝对路径 → 原样（`Path.resolve(strict=False)` 归一）；
5. 目录不存在则以 `parents=True, exist_ok=True` 创建；创建失败视为安装失败（见 FR-6 的失败语义）。

- **AC-1.1** 默认配置下 `/astral status` 显示的路径以 `<data>/workspaces/astral` 结尾，且该目录存在。
- **AC-1.2** 配置 `shared_root: "D:/astral-workspace"` 重启后，`/astral status` 显示该路径，`D:/astral-workspace` 被创建。
- **AC-1.3** 配置 `shared_root: "~/astral"` 时实际目录为 `<用户主目录>/astral`。
- **AC-1.4** 配置相对路径 `myws` 时实际目录为 `<data>/workspaces/myws`。
- **AC-1.5** 把 `shared_root` 指到不可写位置（例如只读盘或受保护目录）时，`/astral status` 显示 `installed=false` 且 `reason` 说明"无法创建/写入"，同时在日志中有一条 ERROR。

### FR-2 危险路径黑名单

安装前校验共享根不得等于、也不得位于下列路径之内（默认集合，可由 `forbidden_roots` 追加）：

| 类别 | 默认禁止路径 |
| --- | --- |
| AstrBot 源码 | `get_astrbot_path()` 指向的目录 |
| 数据敏感区 | `<data>/config`、`<data>/plugins`、`<data>/plugin_data` |
| 用户主目录敏感区 | 用户主目录本身、`~/.ssh`、`~/.aws`、`~/.config` |
| 系统目录 | Windows：`C:/Windows`、`C:/Program Files`；Linux/macOS：`/etc`、`/usr`、`/var`、`/System` |

命中任一规则时：**拒绝安装补丁**，记录 ERROR（含命中规则与被拒路径），状态接口返回 `installed=false` + `reason`。禁止自动回落到默认目录。

- **AC-2.1** 把 `shared_root` 配成 AstrBot 源码目录，重启后 `/astral status` 显示 `installed=false`、`reason` 含 `forbidden`，且 Agent 的 `pwd` 仍为原生会话目录。
- **AC-2.2** 在 `forbidden_roots` 里追加 `D:/secret` 后，把 `shared_root` 设为 `D:/secret/sub`，同样被拒绝。
- **AC-2.3** 合法路径（默认路径、`D:/astral-workspace`、`~/astral`）不被误判为禁止，安装正常完成。
- **AC-2.4** 被拒绝时**不得**产生"部分安装"：`status().installed` 为 false 且没有任何模块全局被改写（可用 `consumers` 为空验证）。

### FR-3 共享工作区补丁（顶层替换 + 双写）

安装后，下列 4 个函数在运行期被替换为"恒返回共享根"的包装：

| 函数 | 原语义 | 补丁后语义 |
| --- | --- | --- |
| `resolve_workspace_root_for_umo` | 按 UMO 解析 | 恒返回共享根 |
| `resolve_project_workspace_root` | 按项目记录解析（Dashboard 面板） | 恒返回共享根 |
| `default_workspace_root` | `<ws>/<normalize(umo)>` | 恒返回共享根（兜底） |
| `project_workspace_root` | `<ws>/project_<id>` | 恒返回共享根（兜底） |

替换必须**双写**：① 改 `astrbot.core.workspace` 模块属性；② 扫描 `sys.modules`，把所有仍持有原函数引用的模块全局一并改写。仅做 ① 会让已经 `from ... import` 过的消费者静默失效。

- **AC-3.1** 正常安装后 `GET /astrbot_plugin_astral/state` 的 `consumers.resolve_workspace_root_for_umo` 同时包含 `astrbot.core.astr_main_agent` 与 `astrbot.core.tools.computer_tools.util`。
- **AC-3.2** 同一响应的 `consumers.resolve_project_workspace_root` 包含 `astrbot.dashboard.services.chatui_project_service`。
- **AC-3.3** Telegram 群、QQ 群、私聊三处分别让 Agent 执行 `pwd`（或 shell 工具 `cd` 打路径），三者输出是同一个绝对路径。
- **AC-3.4**（负对照，pytest）只改模块属性、不扫 `sys.modules` 时，已导入的消费者仍返回原生路径 —— 证明双写是必需的。
- **AC-3.5**（正对照，pytest）双写后，消费者返回共享根；且在**补丁之后**才 import 的新消费者也返回共享根。
- **AC-3.6** 包装函数签名与基线一致：`async resolve_workspace_root_for_umo(umo, db=None)`、`resolve_project_workspace_root(project, *, fallback_umo)`、`default_workspace_root(umo)`、`project_workspace_root(project_id)`（以 `plan.md` §8 的签名表为准）。

### FR-4 幂等安装与重载安全

- 补丁状态挂在 `astrbot.core.workspace` 模块属性上（不是插件模块），插件模块被 `sys.modules` 清理后仍能取到原始函数。
- 每次 `install()` 重建包装函数并重扫；旧包装靠标记识别并替换，不叠加。
- 重复调用 `install()` 结果一致（幂等）。

- **AC-4.1** 连续调用 `install()` 3 次后，状态中的 `originals` 仍是 AstrBot 原始函数对象（不是上一版包装）。
- **AC-4.2** 在 WebUI 里重载插件 10 次，每次 `/astral status` 的 `consumers` 列表长度不增长、无重复项，Agent `pwd` 仍为共享根。
- **AC-4.3** 模拟"未调用 `terminate` 就再次安装"（直接再 `install()`），不产生叠加：`pwd` 仍为共享根，`originals` 未变。

### FR-5 卸载与还原

插件停用/重载时执行还原：把每个被改写的模块全局恢复为原始函数，并移除状态属性。还原后行为与原生完全一致。

- **AC-5.1** 停用插件后，Agent 在群 A 的 `pwd` 回到 `<data>/workspaces/<normalize(umo)>`；再启用后回到共享根。
- **AC-5.2**（pytest）`uninstall()` 后，4 个名字在所有模块中的引用等于 `originals`。
- **AC-5.3** `uninstall()` 可重复调用，第二次及以后返回 `uninstalled=false` 且不抛异常。

### FR-6 版本漂移必须显式失败

安装前检查 4 个目标函数是否存在且可调用：

- 全部存在 → 正常安装；
- 任一缺失/被改名 → **不安装任何函数**，记录 ERROR（列出缺失的名字），状态为 `installed=false` + `reason`；
- 目录不可创建、黑名单命中同样走这条失败语义（不降级）。

- **AC-6.1** 用桩模块模拟 `astrbot.core.workspace` 缺少 `resolve_project_workspace_root`，`install()` 返回失败，`status().installed` 为 false 且 `reason` 含该函数名。
- **AC-6.2** 失败状态下，`/astral status` 与 `GET state` 都能看到原因；插件其它功能不抛未捕获异常。

### FR-7 状态自检接口与聊天指令

- `GET /astrbot_plugin_astral/state`（仅 GET）返回 JSON，至少包含：
  `installed`、`shared_root`、`consumers`（按函数名分组的模块名列表）、`baseline`（版本 + commit）、`runtime`（`computer_use_runtime`）、`reason`（失败时）。
- 未安装（停用/失败）时返回 HTTP 503 且 body 仍含 `installed=false` 与 `reason`。
- `/astral status` 输出人类可读摘要：共享根、安装状态、consumers 数量、当前 runtime、失败原因（若有）。

- **AC-7.1** 正常运行下 `curl -b <dashboard cookie> http://127.0.0.1:6185/api/v1/plugins/extensions/astrbot_plugin_astral/state` 返回 200，`installed=true`，三个 consumers 键均非空。
- **AC-7.2** 停用插件后同一 URL 返回 503 且 body 含 `installed=false`。
- **AC-7.3** 在群里发 `/astral status`，回复包含共享根绝对路径与"已生效/未生效"。
- **AC-7.4** 当 `computer_use_runtime=sandbox` 时，`/astral status` 额外提示"当前为沙箱运行时，工作区不参与 Agent 执行"。

### FR-8 共享根骨架初始化（只补不覆盖）

首次安装时由 001 创建下列**目录**与 Astral 自用文件；四个内容文件（`AGENTS.md`、`EXTRA_PROMPT.md`、`SOUL.md`、`MEMORY.md`）的内容与播种归 `002 FR-2`（在 `Star.initialize()` 中执行），001 只保证目录存在、不写它们，且**任何已存在的路径一律不改写**：

```text
<shared_root>/
├─ AGENTS.md            # 工作区说明（002 播种，用户可改）
├─ EXTRA_PROMPT.md      # 全局提示词入口（002 播种 + 托管区段，见 002 FR-4）
├─ SOUL.md              # 自我认知（002 播种，见 002 FR-2）
├─ MEMORY.md            # 长期记忆（002 播种，见 002 FR-2）
├─ skills/              # AstrBot 工作区技能（可为空）
├─ shared/              # 跨 bot/群的公共资产
├─ memory/archive/      # 记忆归档（002 使用）
├─ archive/             # 工作区归档快照（FR-11 使用）
└─ .astral/             # Astral 自身元数据（不含任何密钥）
   ├─ state.json        # 最近一次安装/根信息快照
   └─ audit.jsonl       # 追加式审计日志
```

- **AC-8.1** 在空目录上首次启动后，上表所有**目录**由 001 创建；四个内容文件由 002 播种（联合验收：001 装好且 002 启用后，上表全部路径存在）。
- **AC-8.2** 用户手工修改 `AGENTS.md` 与 `SOUL.md` 后重启插件，两文件内容与修改时间不变（哈希相同）。
- **AC-8.3** `.astral/` 下不出现 token/密钥类字段；`audit.jsonl` 为可追加的 JSONL。
- **AC-8.4** `skills/` 为空时不报错；当用户放入 `skills/<name>/SKILL.md` 后，下一次请求能被工作区技能发现（仅 local runtime）。

### FR-9 Dashboard 面板与真实 cwd 一致

接管 `resolve_project_workspace_root`（由 `patch_webchat_projects` 控制，默认 `true`），使 Dashboard 的"工作区文件"面板与 Agent 的 cwd 指向同一目录。

> 已知既有事实：面板解析时总是自己合成一个 webchat 形式的 `fallback_umo`，因此**非 webchat 会话**被加入项目时，"面板路径 ≠ Agent cwd"在打补丁之前就已存在。补丁后两者都等于共享根，该不一致被顺带消除。

- **AC-9.1**（webchat 会话归入项目）打开该会话的工作区面板，列出的目录内容与 Agent `pwd` 指向同一路径。
- **AC-9.2**（非 webchat 会话/无项目）打开面板，显示的目录为共享根（补丁前会显示 `webchat_..._default` 形式的目录）。
- **AC-9.3** 把 `patch_webchat_projects` 设为 `false` 并重启：面板回到项目自身目录、Agent 仍在共享根，且 `/astral status` 明示"面板与 cwd 不一致"。

### FR-10 停用后端点自检（平台缺陷缓解）

AstrBot 4.29.4 中 `Context.registered_web_apis` 是类属性且停用插件时不清空，因此停用后 Astral 的 HTTP 端点仍可能被路由命中。每个端点入口必须检查补丁状态：

- 未安装 → 返回 503 + `{"installed": false, "reason": ...}`；
- 已安装 → 正常处理。

- **AC-10.1** 停用插件后调用 `GET .../state` 返回 503，body 含 `installed=false`。
- **AC-10.2** 重新启用后同一 URL 恢复 200 且 `installed=true`。
- **AC-10.3** 停用状态下不会因为端点残留而改写任何 Agent 行为（`pwd` 仍为原生目录）。

### FR-11 审计、归档与配额（P2）

- 审计：`.astral/audit.jsonl` 逐行记录 Astral 自身动作（播种、归档、清理、根变更），字段至少 `ts`、`action`、`detail`。
- 归档：`/astral clean [days]` 把 `archive/` 之外超期的临时产物移动到 `archive/<yyyyMMdd-HHmmss>/`，**移动而非删除**。
- 配额：`quota_gb` 配置项（默认 0 = 不限制）；超限时只在 `/astral status` 与状态接口里给 warn，**不自动删除**。

- **AC-11.1** 执行 `/astral clean 0` 后 `archive/` 下出现时间戳子目录，原文件以移动方式落位（原位置不再存在）。
- **AC-11.2** `audit.jsonl` 每行能被 `json.loads` 解析，且包含 `ts/action/detail`。
- **AC-11.3** 设置 `quota_gb` 小于当前占用时，`/astral status` 显示配额告警，且没有任何文件被删除。

### FR-12 发布物与版本约束

- `metadata.yaml`：`name: astrbot_plugin_astral`、`display_name: Astral`、`version`、`repo`、`astrbot_version: ">=4.29,<5"`。
- 删除插件模板遗留的 `@register("helloworld", ...)`（`metadata.yaml` 优先级更高，留着只会误导）。
- README 必含四段：能力与限制（不能改内置 WebUI）、配置说明、**隐私披露**（共享=跨群可见）、**升级与备份注意事项**（官方备份不含工作区文件）。
- `CHANGELOG.md` 记录版本与回归结果。

- **AC-12.1** 在 4.29.4 实例上停用→启用插件无报错，插件列表显示 `Astral` 与版本号。
- **AC-12.2** README 中出现"不能修改内置 WebUI""所有群可见""需要单独备份共享根"三条说明。
- **AC-12.3** 插件目录中检索 `helloworld` 无结果。

### FR-13 升级回归

提供可重复执行的坐标核对（脚本或 pytest 用例）与回归清单（`plan.md` §9）：

- 核对 4 个函数存在性与签名、3 个消费者的 import 形态；
- 记录 harness（`../_investigation/poc/harness/harness.py`）的完整运行结果；
- 升级 AstrBot 后更新 `plan.md` §8 坐标表并在 `CHANGELOG.md` 记录。

- **AC-13.1** 坐标核对用例在基线版本上通过；在人为改名的桩版本上失败并列出漂移项。
- **AC-13.2** CHANGELOG 中每个版本都有"回归结论 + harness 断言数"的记录。

## 5. 非功能需求

| 编号 | 要求 | 验收方式 |
| --- | --- | --- |
| NFR-1 性能 | 补丁函数直接返回常量，不查库、不做 IO；相较多一次 `stat` 可忽略 | 代码评审 + 压测：单群 100 条消息无额外文件 IO |
| NFR-2 可观测 | 安装/卸载/根变更各一条 INFO；失败一条 ERROR 带原因 | 日志检查（AC-2.1、AC-6.1） |
| NFR-3 安全 | 插件等同管理员权限；只读接口限制在共享根内、拒绝符号链接逃逸、单文件 ≤ 512 KB | AC-2.x + 002 的 files 接口用例 |
| NFR-4 兼容 | Python 3.10+；Windows/macOS/Linux；AstrBot 4.29.x | CI 矩阵或手工三平台冒烟 |
| NFR-5 可维护 | 补丁逻辑集中在 `ws_shared.py`，单测用桩不依赖运行实例 | 单测目录结构评审 |
| NFR-6 可回滚 | 停用插件即恢复原生行为，不改任何配置 | AC-5.1 |

## 6. 边界与异常

| 编号 | 情况 | 期望行为 |
| --- | --- | --- |
| E-1 | 三处"本地重算"兜底路径（`_get_workspace_path_for_umo` 的 fallback、`computer_tools/util.py` 的 `workspace_root`、`fs.py` 的 `_workspace_root`） | 顶层替换在正常路径下已短路它们；**仅打叶子补丁时这些路径仍回落到按 UMO 目录**，因此"叶子兜底"只是保险，不得作为主方案 |
| E-2 | 调用方在**函数内部**延迟 `from ... import` | `sys.modules` 扫描扫不到；升级回归清单必须检查是否出现延迟 import |
| E-3 | `computer_use_runtime=sandbox` | 共享不生效（Agent 在容器 `/workspace`）；`/astral status` 必须提示 |
| E-4 | 用户自行多进程 / 多 worker 部署 | 补丁在各进程内独立生效；README 说明不支持 |
| E-5 | 非 webchat 会话的面板 `fallback_umo` 恒为 webchat 形式（既有平台行为） | 补丁后两侧都等于共享根；关闭 `patch_webchat_projects` 时面板与 cwd 不一致属预期，状态接口明示 |
| E-6 | api_key 创建的项目有 4 层防越界（创建/更新/启动迁移/运行时） | 补丁绕过了其中"路径校验"部分，因此 FR-2 黑名单是必须的补偿手段 |
| E-7 | 删除会话/项目不会删除目录（核心从不清理） | 清理与归档由 FR-11 负责；README 说明磁盘只涨不跌的风险 |
| E-8 | 插件停用后端点残留（平台缺陷） | FR-10 的 503 自检 |
| E-9 | 共享根被外部程序删除或改成文件 | 下次请求前补丁函数不感知；`/astral status` 必须检测并提示（目录不存在/不是目录） |
| E-10 | `EXTRA_PROMPT.md` 被用户全文改写、托管区段丢失 | 002 负责重插区段；001 只保证文件存在，不得覆盖用户内容 |
| E-11 | 首次启动时 `data/workspaces` 尚未创建 | 创建共享根时递归创建父目录 |

## 7. 依赖与假设

### 7.1 已验证的基线事实（2026-11-01 核对，详见 `../_investigation/verify/coordinate-verification.md`）

- `astrbot/core/workspace.py` 共 217 行，9 个解析/归一函数签名与设计稿一致；
- 解析函数的消费者只有 3 处且全部是顶层 `from ... import`（`astrbot/core/astr_main_agent.py`、`astrbot/core/tools/computer_tools/util.py`、`astrbot/dashboard/services/chatui_project_service.py`），另有 1 处只导入常量；
- `EXTRA_PROMPT.md` 由 `astr_main_agent` 在每个请求读取并追加到 system prompt；
- `Context.registered_web_apis` 为类属性且停用不清空；
- 只改模块属性 → 消费者仍返回旧路径（负对照成立）；双写 → 生效；`uninstall` → 完全还原（内存复现 6/6）。

### 7.2 假设

1. 目标实例为单进程部署（AstrBot 默认）。
2. 用户不会在 `EXTRA_PROMPT.md` 托管区段内手工编写内容（区段外随便写）。
3. 4 个函数在 4.29.x 全系保持现状；跨大版本升级后必须重跑 FR-13。
4. 停用插件后 Dashboard 仍会路由到残留端点（平台缺陷），因此端点自检是必要而非冗余。

### 7.3 未验证项

| 编号 | 内容 | 处理 |
| --- | --- | --- |
| U-1 | harness 完整 73 条断言：本沙箱内只跑到 11/73（`%TEMP%` 嵌套目录被拒），已用内存脚本复现核心 6/6 | 任务 T-0 在非沙箱环境重跑并记录输出 |
| U-2 | `computer_use_runtime=sandbox` 时容器内 `/workspace` 是否跨会话共享 | 不在本规格范围，README 声明 |
| U-3 | 非 webchat 会话面板不一致的对外表述 | 采用"补丁顺带修复"表述（AC-9.2） |
| U-4 | 4.29.4 之后的提交是否改变坐标系 | 升级回归时重跑 FR-13 |

## 8. 未决问题

见 `decisions.md` §3。其中 U-1..U-4 与本文件 §7.3 一一对应，U-5（历史目录迁移）、U-6（只读页是否展示工作区目录）属于实现期决策。

## 9. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-01 | v0.1 | 首版：从 `docs/archive/astral-workspace-design.md` 第 1–5、7–10 章扩展为 13 条 FR + 45 条 AC（`AC-<FR>.<n>` 形式），并按核对报告修正 4 条断言、补充 3 条遗漏 |
