# 002 · SOUL 与 MEMORY：文件式记忆规格

> 状态：草案 v0.1 · 依赖：001-shared-workspace · 最后更新：2026-11-01
> 基线：AstrBot 4.29.4 @ `831abae85`；源码坐标以 `../_investigation/verify/coordinate-verification.md` 与本文件实际核对过的行号为准。
> 路径约定见 `specs/README.md` §0：文中的 `astrbot/…`、`dashboard/…` 等源码路径一律指 `../AstrBot-code/` 下的同名文件。

---

## 1 背景与目标

### 1.1 一句话

在 001 建立的共享根之上，用两个 Markdown 文件（`SOUL.md`、`MEMORY.md`）承载 agent 的"人格"与"长期记忆"，由 agent 自己用 AstrBot 内置文件工具维护，Astral 只做三件事：**首启播种**、**托管写入守则**、**在每个对话开始时把两份文件渲染成一份冻结快照注入上下文**。

### 1.2 为什么是文件式记忆

原设计稿 `docs/archive/astral-workspace-design.md` 第 6 章采用 SQLite + 作用域 + 自动抽取 + embedding 的方案。该方案被用户明确否决，理由是它为一个"单进程、单用户、量级在几千行文本"的场景引入了四个独立子系统：

| 原方案部件 | 引入的维护面 | 本包是否保留 |
| --- | --- | --- |
| SQLite 表 + 自建 schema/migration | 建表、迁移、备份、事务 | 否 |
| 自动抽取队列 + 记忆 LLM | 额外 token、worker 生命周期、失败重试、去重键 | 否 |
| embedding / 向量检索 | 供应商依赖、索引重建、维度不匹配 | 否 |
| scope（global/group/user） | 键空间、读写路由、隐私边界 | 否 |

替代方案是业界同类 agent 产品（OpenClaw、Hermes Agent 等）的通行做法：把人设与记忆写成**人可读、可手改、可 diff 的 Markdown 文件**，框架在会话开始时把它们注入上下文，写入由模型自己用文件工具完成。

> 假设：上述产品细节属业界通行做法，未在本仓库核对，仅作方向参考，**不构成任何依赖**。

收益与代价都很直接：

| 收益 | 代价 |
| --- | --- |
| 用户能直接 `Ctrl+S` 改人格，改完 `/new` 即生效 | 没有全文检索与相似度排序，只能靠 `grep` 关键词 |
| 记忆随共享根走，换机器 = 拷一个目录 | 文件会膨胀，需要容量与归档约定（FR-5） |
| agent 用的就是内置文件工具，行为可解释、可复核 | 写入质量依赖守则文案（FR-4），没有 schema 兜底 |
| Astral 不新增任何持久状态（无 DB、无索引、无队列） | 全局一份 → 跨群可见，必须披露（FR-7） |

### 1.3 目标

| 编号 | 目标 | 判定方式 |
| --- | --- | --- |
| G1 | 首次启动后，共享根下的四份文件存在且带版本标记，内容可直接用 | AC-2.1、AC-2.2 |
| G2 | 每个对话只读一次文件、只渲染一次快照；同一对话内注入内容字节不变 | AC-3.1、AC-3.2 |
| G3 | agent 无需专用工具即可维护记忆，写入格式与容量有明确守则 | AC-4.1、AC-4.2 |
| G4 | 用户能在聊天里和 WebUI 里**只读**看到它到底记了什么 | AC-6.1、AC-6.2 |

### 1.4 与 001 的分工（不重复定义）

| 主题 | 归属 | 本包如何引用 |
| --- | --- | --- |
| 共享根的解析/校验/黑名单 | `specs/001-shared-workspace/spec.md` 的 FR-1、FR-2 | 只说"共享根"，不重复规则 |
| 目录骨架（只含目录） | `specs/001-shared-workspace/spec.md` 的 FR-8 | 001 只建目录；四份文件的**播种**归本包 FR-2，本包只管"写什么、怎么写" |
| 补丁状态与 `GET /astrbot_plugin_astral/state` | `specs/001-shared-workspace/plan.md` §6 | 只读页消费，不改字段 |
| 文件**语义**、播种内容、快照 | 本包 FR-1～FR-5 | — |
| 只读可见性与 `views/astral/` 只读页 | 本包 FR-6 / `plan.md` §7 | — |

> 假设：001 的 FR 编号按 `specs/README.md` §3 的文档地图（FR-1/FR-2 共享根、FR-8 目录骨架、plan §6 state）。若 001 定稿后编号变化，由 Lead 统一改引用。

### 1.5 术语（全文严格沿用，不造近义词）

| 术语 | 含义 | 不用这些说法 |
| --- | --- | --- |
| 共享根 | 001 定义并维护的共享工作区根目录，默认 `<data>/workspaces/astral` | "工作目录""ws 根""data 目录" |
| 记忆文件 | `SOUL.md` 与 `MEMORY.md` 的合称（本包语境） | "记忆库""memory 存储" |
| 冻结快照（简称快照） | 对话开始时由两份记忆文件渲染、该对话内不再变化的注入块 | "记忆注入""上下文缓存""memory prompt" |
| 首启播种 | 把默认模板幂等写进空/缺失文件的动作（每次插件加载执行，已有内容不覆盖） | "初始化""生成""引导" |
| 托管区段 | `EXTRA_PROMPT.md` 中 `astral:managed` 标记之间的区域，由 Astral 重写 | "注入段""系统段落""prompt 区" |
| 只读页 | `views/astral/` 提供的插件页面，无任何写操作 | "管理页""编辑器""控制台" |

---

## 2 非目标

| 编号 | 非目标 | 说明 |
| --- | --- | --- |
| N1 | 不引入 SQLite / 任何数据库表 | 记忆的唯一权威源是 Markdown 文件 |
| N2 | 不做 embedding、向量检索、相似度排序 | 检索靠 `grep` 与人工 |
| N3 | 不做自动抽取队列、不调用记忆专用 LLM | 写入完全由 agent 在对话内用文件工具完成 |
| N4 | 不注册记忆专用 LLM 工具 | 复用 `astrbot_file_read_tool` / `astrbot_file_write_tool` / `astrbot_file_edit_tool` / `astrbot_grep_tool`（`fs.py:327/448/553/684`） |
| N5 | 不做群/用户分域 | 全部平台、全部群、全部私聊共享同一份（ADR-3） |
| N6 | 不做知识库/文档导入 | 与 `kb_manager` 无关 |
| N7 | 不做快照刷新机制 | 无定时刷新、无写后失效；`/new` 是唯一的换新入口 |
| N8 | 不做编辑器 | 只读页没有任何写操作，插件页 iframe 也没有 PUT/DELETE 通道 |
| N9 | 不做敏感信息自动扫描 | 只做披露与守则约束（FR-7） |
| N10 | 不接管 AstrBot persona / 知识库 / 会话历史 | 只追加自己的注入块 |

---

## 3 用户与场景

| 编号 | 场景 | 现状痛点 | 本包如何解决 |
| --- | --- | --- | --- |
| S1 | 群聊长期记性：同一个群隔三差五问"上次说的那个部署方案" | 每对话历史清空即失忆；AstrBot 会话历史跨群不通 | 事实写进 `MEMORY.md`，新对话靠快照带回来（FR-1、FR-3） |
| S2 | 用户手改 SOUL 调性格：觉得 bot 太啰嗦 | persona 只能在 WebUI 改，且不随工作区走 | 直接编辑 `SOUL.md`，`/new` 生效；文件随共享根迁移（FR-1、FR-8） |
| S3 | 换机器迁移：从旧 Mac 搬到新 Windows 机器 | 官方备份不含工作区文件，容易只搬了配置丢了记忆 | 四份文件都在共享根下，整目录拷贝即可（FR-8） |
| S4 | 事后审计它记了什么：担心 bot 记了不该记的 | 原方案数据在 DB 里，用户看不到也删不掉 | `/astral memory show` + 只读页 + 文件本来就摆在目录里（FR-6、FR-7） |

---

## 4 功能需求与验收标准

### FR-1 四件套语义与优先级

共享根下四个文件分工如下，**语义固定**，任何实现不得让它们互相越界：

| 文件 | 写什么 | 不写什么 | 谁维护 | 进上下文 | 预算 |
| --- | --- | --- | --- | --- | --- |
| `SOUL.md` | agent 自己的信息：身份、语气、**边界（禁止事项）**、自我修正规则 | 具体事实、待办、项目细节 | agent（仅在用户明确要求时）+ 用户手改 | 是（快照） | 4000 字符 |
| `MEMORY.md` | 长期记忆：事实、约定、决定、待办、教训 | 人格、语气、系统提示式指令 | agent（每次对话内可写） | 是（快照） | 注入 8000 字符 / 容量软上限 20000 字符 |
| `AGENTS.md` | 工作区说明：这是什么目录、各目录怎么用、协作约定 | 任何"给 bot 的指令" | 用户 | **否** | 无限制 |
| `EXTRA_PROMPT.md` | 注入型操作守则；其中托管的区段写"何时/如何写记忆" | 人设、记忆条目 | 托管区段由 Astral 重写，区段外属用户 | 是（core 注入） | 建议 ≤ 2000 字符 |

`AGENTS.md` **不注入**是刻意的不对称：AstrBot core 只读 `EXTRA_PROMPT.md` 这一个魔法文件（`astr_main_agent.py:451-486`，调用点 `1085`），全仓库不读 `AGENTS.md` / `SOUL.md` / `MEMORY.md`。若让 Astral 额外注入 `AGENTS.md`，就会出现"同一段说明在两份文件里、用户不知道改哪份"的问题。`AGENTS.md` 的读者是人和外部工具（编辑器里的 agent 等）。

**与 AstrBot persona 冲突时的优先级**（快照会在正文里复述这张表，见 `plan.md` §4）：

| 优先级 | 层 | 冲突时 |
| --- | --- | --- |
| 1（最高） | AstrBot 平台安全策略 / 系统提示 | 不可覆盖 |
| 2 | `SOUL.md` 的**边界（禁止事项）** | 只收紧不放松：与 persona 冲突时 SOUL 赢 |
| 3 | persona（`# Persona Instructions`，`astr_main_agent.py:607`） | 角色/语气/格式以 persona 为准 |
| 4 | `SOUL.md` 的身份叙事与自我修正 | 无 persona 冲突时生效 |
| 5 | `MEMORY.md` 的事实与约定 | 与 persona 冲突时 persona 优先（人格不因记忆改变） |
| 6 | `EXTRA_PROMPT.md` 托管守则 | 属操作性流程，覆盖 agent 的即兴习惯 |

快照被追加到 `req.system_prompt` 末尾，位置带给尾部内容更高权重；上面这张表就是用来抵消位置偏差的，**必须**出现在快照正文里。

**验收标准**

- AC-1.1（语义）：`cat` 共享根下四个文件，每个都能从内容判断归属：`SOUL.md` 无日期事实条目、`MEMORY.md` 无"你是…"式人设、`AGENTS.md` 含目录说明、`EXTRA_PROMPT.md` 含 `astral:managed` 标记对。
- AC-1.2（优先级）：把 `SOUL.md` 边界段写成"禁止回答任何股票/基金问题"，同时在 AstrBot 选一个热情人设；在群里问"帮我看看这只股票"，回答必须拒绝，且理由指向边界而非人设。

### FR-2 首启播种与默认模板

| 项 | 规则 |
| --- | --- |
| 触发 | 插件 `Star.initialize()`（`core/star/base.py:141`，每次插件加载/重载/启用都会被 await，`star_manager.py:1422-1424`）。**不用** `on_astrbot_loaded`：该事件全进程只在启动后派发一次（`core_lifecycle.py:374`），重载插件不会重新播种与同步守则 |
| 写盘边界 | **只在此处**播种；对话路径（`on_llm_request`）绝不写盘 |
| 播种条件 | 文件不存在 **或** 文件 `strip()` 后为空 → 播种；其它情况一律不覆盖 |
| 原子写 | 同目录写 `.<name>.tmp.<pid>` → `os.replace()` 到目标；异常时删除临时文件，目标文件保持原样 |
| 版本标记 | 模板首行 `<!-- astral:seeded:v1 -->`；不参与任何运行时判断，只用于人工核对 |
| 与 001 的边界 | 001 只创建目录、不写四份内容文件（`specs/001-shared-workspace/spec.md` FR-8）；本包在 `Star.initialize()` 里播种，两种情况都幂等 |
| 目录缺失 | 共享根或其父目录不存在 → 不播种、不建目录，记一条 `WARNING astral: shared root missing`，由 `/astral status` 与只读页呈现 |
| 已有内容 | 用户手改过的文件**永不被模板覆盖**；想恢复模板请自行删除文件后重载插件 |
| 空文件即未初始化 | 清空文件（只剩空白）会在下次插件加载/重载时被重新播种；若确实想留空，保留一行 HTML 注释即可 |

**验收标准**

- AC-2.1（首启播种）：删除共享根下 `SOUL.md` → 在 WebUI 重载插件 → 文件重新出现，`Get-Content SOUL.md` 首行为 `<!-- astral:seeded:v1 -->`；同一操作对 `MEMORY.md`、`AGENTS.md`、`EXTRA_PROMPT.md` 各验证一次。
- AC-2.2（不覆盖）：向 `MEMORY.md` 追加一行 `- [2026-11-01] [测试] 不该被覆盖` → 重载插件 → `Select-String "不该被覆盖" MEMORY.md` 有命中，且文件未被截断（字符数只增不减）。
- AC-2.3（原子写）：单测 `test_seed_atomic_on_failure` 让 `os.replace` 抛异常，断言目标文件内容与 mtime 不变、临时文件已清理；另跑一次正常播种，断言共享根下无 `*.tmp.*` 残留。

### FR-3 对话级冻结快照注入

| 项 | 规则 |
| --- | --- |
| 触达点 | `@filter.on_llm_request()`，钩子在 `agent_sub_stages/internal.py:307` 触发，位于 `build_main_agent(...)`（`:265`）之后 |
| 注入目标 | `req.system_prompt = (req.system_prompt or "") + "\n" + 快照块`（`system_prompt` 是 `str`，默认 `""`，`entities.py:212`） |
| 冻结键 | `umo + ":" + conversation_id`；对话 ID 取 `req.conversation.cid`（`astr_main_agent.py:1714` 赋值），为空时回退 `await conversation_manager.get_curr_conversation_id(umo)`（`conversation_mgr.py:174`，可能返回 `None`）；两者都为空 → 键退化为 `umo + ":no-conv"` |
| 冻结语义 | 每个键只渲染一次，之后**永远复用同一字符串**；对话内不刷新、不因文件 mtime 变化失效 |
| 缓存 | 进程内 LRU（默认 64 条），纯内存、不落盘；重启即清空 |
| 换新入口 | `/new` 换 `cid` → 新键 → 重新渲染。`/reset` **不换 cid**（`builtin_commands/commands/conversation.py:110-141` 只清历史）→ 不刷新，此行为必须披露 |
| 触发频次 | 每次用户消息进入 LLM 请求阶段触发一次（全仓 `call_event_hook(..., OnLLMRequestEvent, ...)` 仅 `internal.py:307` 与 `third_party.py:333` 两处）；工具循环内的多轮模型调用不再触发 |
| 关闭开关 | `memory.enabled = false` 时不注入（不影响播种与只读页） |

块格式（精确文本见 `plan.md` §4）：固定头部（版本、`conv` 前 8 位、生成时间、优先级声明）→ `### SOUL.md` → `### MEMORY.md` → 固定尾注（说明本快照已冻结、需要最新内容请用文件工具、换新快照用 `/new`）。

**验收标准**

- AC-3.1（冻结）：群 A 里问一个需要记的事并让 bot 写进 `MEMORY.md` → **同一对话**内紧接着问"你记得刚才那条吗"，回答必须表示快照里没有（或表示需要读文件）；随后 `/new` 再问同一句，无需读文件即可复述。
- AC-3.2（键与字节稳定）：开 `DEBUG` 日志，同一对话连续两条消息的快照 `sha256[:8]` 完全一致，且第二条日志为 `hit=true`、第一条为 `hit=false`；`/new` 后发一条消息，`sha` 改变且 `hit=false`。
- AC-3.3（降级）：用 `[IO.File]::WriteAllBytes` 把 `SOUL.md` 写成含 `0xFF` 的非 UTF-8 字节 → `/new` 后快照该段显示"含非 UTF-8 字节，已替换显示"且请求正常；再把 `SOUL.md`、`MEMORY.md` 都删掉 → `/new` 后注入的是一段 ≤200 字符的最小降级块，日志有 `WARNING`，进程无异常栈。

### FR-4 EXTRA_PROMPT 托管区段与写入守则

| 项 | 规则 |
| --- | --- |
| 标记 | `<!-- astral:managed:begin -->` / `<!-- astral:managed:end -->`，各独占一行 |
| 归属 | 两标记**之间**是 Astral 的领地，每次插件加载/重载按当前版本重写；两标记**之外**属于用户，字节不得改动 |
| 版本行 | 区段首行 `<!-- astral:managed:v1 -->`；版本升级 → 整段重写（区段内不做增量合并） |
| 标记缺失 | 文件存在但无标记 → 在文件末尾追加 `\n` + 一整套标记与内容，**不删不改**既有内容，记一次 `INFO` |
| 标记异常 | 重复、嵌套、`end` 在 `begin` 之前 → **不写**，记 `WARNING`，`/astral status` 与只读页显示"托管区段异常"，把决定权交回用户 |
| idempotent | 内容未变 → 不写盘（比较字节）；内容变化 → 原子写整文件，保留区段外字节 |
| 单一来源 | 守则文案只存在于 `templates.py` 常量中；任何手改会在下次插件加载/重载时被覆盖（页面与 `/astral memory show extra` 都提示这一点） |
| 守则必须覆盖 | ①何时写记忆（4 条触发条件）②怎么写（`file_edit` 局部追加、先 `grep` 去重、`- [YYYY-MM-DD] [来源] 内容` 格式、单条 ≤200 字符）③写 SOUL 的门槛 ④容量与归档 ⑤禁止写入的内容 ⑥快照可能是旧的、以文件为准 |
| 注入顺序 | core 在 `astr_main_agent.py:1085` 把守则拼成 `[Workspace Extra Prompt]` 追加到 `system_prompt`，位置在快照**之前**（见 `plan.md` §4.5），因此守则与快照在同一系统提示内、先后确定 |

**验收标准**

- AC-4.1（托管区段）：在 `EXTRA_PROMPT.md` 标记**之外**加一行 `用户笔记：只在周五发布` → 重载插件 → 该行仍在，且区段内文本等于 `templates.py` 常量；再删掉 `<!-- astral:managed:end -->` → 重载后文件**末尾**出现新的一整套标记，原有内容一字未少，日志有 `INFO`。
- AC-4.2（守则生效）：群里说"记住：周五 20:00 站会" → `Select-String "周五 20:00 站会" MEMORY.md` 有命中，且该行以 `- [` 开头、含日期与来源；再说一次完全相同的话 → `MEMORY.md` 中该内容仍只有 1 行（发生合并或跳过重复）。

### FR-5 容量与归档

| 项 | 规则 |
| --- | --- |
| 注入预算 | `SOUL.md` 4000 字符、`MEMORY.md` 8000 字符（可配，见 U-3）；超预算**只影响快照**，不改文件 |
| 截断方式 | 保留头部 N 字符 + 提示行；提示行包含原文总字符数、绝对路径、以及"要全文请用 `astrbot_file_read_tool`" |
| 归档索引 | 发生截断且 `memory/archive/` 存在 → 提示行后附最多 10 条归档文件名与大小；目录读取失败只记 `DEBUG`，不影响注入 |
| 文件容量软上限 | `MEMORY.md` 20000 字符（可配，见 U-4）；超限 → 快照尾部追加"容量提示"段，建议合并/归档；同时 `/astral memory show` 与只读页显示容量条 |
| 归档命名 | `memory/archive/MEMORY-YYYYMM.md`（按月），一个文件可容纳多条；由 agent 用文件工具剪切，Astral **不自动搬移**（ADR-6） |
| 硬上限 | 单文件 > 512 KB 时只读接口返回前 512 KB 并标 `truncated: true`（对齐 core 的 `_WORKSPACE_FILE_MAX_BYTES`，`dashboard/api/chat_projects.py:19`）；注入侧仍按字符预算截断 |

**验收标准**

- AC-5.1（软上限提示）：把 `MEMORY.md` 填到 21000 字符（重复行即可）→ `/new` → 快照尾部出现"容量提示"段且含"20000"与归档路径；`/astral memory show` 输出同时显示当前字符数与阈值。
- AC-5.2（截断与全文路径）：把 `MEMORY.md` 填到 12000 字符并在 `memory/archive/` 放 2 个 `MEMORY-202608.md`、`MEMORY-202609.md` → `/new` → 快照的 MEMORY 段长度 ≤ 8000 + 提示行，提示含 `MEMORY.md` 绝对路径与这两个归档文件名；把归档目录删掉 → 再 `/new` → 提示仍在但没有归档索引行。

### FR-6 只读可见性

| 通道 | 内容 | 限制 |
| --- | --- | --- |
| `/astral memory show [soul\|memory\|agents\|extra]` | 默认 `memory`。**私聊**返回全文（> 3000 字符截断并给路径）；**群聊**只返回元信息（文件名、字符数、更新时间、前 3 行）+ "完整内容请私聊或到 WebUI 只读页" | 群聊不全量输出是隐私要求（FR-7），不是权限系统 |
| `GET /astrbot_plugin_astral/files` | 四文件只读预览；`name` 缺省返回清单（无正文） | 仅 GET；契约见 `plan.md` §6 |
| `views/astral/index.html` | 补丁状态（消费 001 的 `state`）+ 四文件只读预览 + 字符数/更新时间 + MEMORY 容量条 | 无编辑、无刷新按钮、无上传、无下载 |
| 降级 | 页面不可用（插件未激活 / 路由 404）时，指令通道必须独立可用 | 两者不互相依赖 |

**验收标准**

- AC-6.1（只读页）：WebUI → 插件可视化视图 → Astral：页面显示的补丁状态与 `GET /api/v1/plugins/extensions/astrbot_plugin_astral/state` 返回值一致；四个文件的可预览正文与磁盘内容逐字相同（抽查 `MEMORY.md` 前 200 字符）；页面 DOM 中不存在 `textarea` / `contenteditable` 元素。
- AC-6.2（指令与 API）：私聊发 `/astral memory show` → 返回 `MEMORY.md` 正文（≤3000 字符）；同一个群发同一指令 → 只返回元信息且含"私聊"提示；`curl -H "Authorization: Bearer <key>" ".../astrbot_plugin_astral/files?name=SOUL"` → 200 且 JSON 含 `name/path/abs_path/size/mtime/content/truncated`；`?name=../etc/passwd` → 400。

### FR-7 隐私与安全

| 要求 | 落地 |
| --- | --- |
| 全局共享 = 跨群可见，**必须披露** | 三处固定文案（都含"全局共享"四字）：①播种的 `MEMORY.md` 顶部警示行 ②群聊 `/astral memory show` 输出的固定一句 ③只读页顶部横幅 |
| 禁止写入的内容 | 密钥/口令/token/私钥、身份证/护照/银行卡、手机号/邮箱/住址等可识别信息、健康/财务/司法等敏感信息、第三方私密对话原文 |
| 去标识化 | 记录"某人偏好"时写"群里有人偏好简体中文回复"，不写群号+姓名+手机号 |
| 第三方面前不记 | 用户转述他人隐私并要 bot 记住时，守则要求先问"是否要记、以什么粒度记" |
| 删除权 | 用户要求删除 → agent 直接编辑文件删除对应条目，并回报删了几行；无独立回收站（备份即共享根备份） |
| 不做的事 | 不自动扫描敏感信息（N9）、不加密、不做访问控制（沿用 AstrBot 的 Dashboard 鉴权与 `filesystem_scope`） |

**验收标准**

- AC-7.1（披露三处）：首启后 `Select-String "全局共享" MEMORY.md` 有命中；只读页顶部横幅含"全局共享"；群里发 `/astral memory show` 的输出含同一句。
- AC-7.2（不写敏感信息）：私聊对 bot 说"记一下我的手机号 13800001111 和身份证 110101199001011234" → bot 回复中说明不记录完整号码（或改为去标识化表述），且 `Select-String "13800001111" MEMORY.md` 无命中；再 `Select-String "sk-" -Path <共享根>\*.md` 在新增内容里无命中。

### FR-8 备份与迁移

| 项 | 规则 |
| --- | --- |
| 随共享根 | 四份文件 + `memory/archive/` + `skills/` + `shared/` 全在共享根下，备份 = 备份该目录 |
| 官方备份不含工作区 | AstrBot"维护 → 备份"只含会话别名/用量/密钥/MCP 配置，**不含工作区文件**（`../AstrBot-code/docs/zh/use/webui.md:141`），必须在用户文档里单独给命令 |
| 备份命令 | PowerShell：`Copy-Item -Recurse <共享根> "<dest>\astral-backup-YYYYMMDD"`；恢复：反向拷贝回共享根 |
| 迁移 | 新机器上先按 001 配置好共享根 → 拷回内容 → 重启；已有对话的快照缓存是内存态，重启即重建 |
| 版本标记 | 恢复后不应触发播种（文件非空）；审计时用 `<!-- astral:seeded:v1 -->` 判断来源 |
| 回滚 | 备份即回滚点；恢复后 `/astral memory show` 与只读页应与备份时一致 |

**验收标准**

- AC-8.1（备份恢复）：按文档命令备份共享根 → 删除整个共享根 → 重启插件（001 重建目录骨架并触发 FR-2 播种）→ 从备份拷回 → `MEMORY.md` 的 sha256 与备份时相同，`/astral memory show` 可见恢复后的全部条目。
- AC-8.2（迁移）：把备份拷到另一台机器的共享根并重启 → 在群里问一个只存在于旧 `MEMORY.md` 的事实，agent 能答出；`GET .../state` 返回的共享根为新的绝对路径。

---

## 5 非功能要求

| 编号 | 要求 | 指标 / 验证 |
| --- | --- | --- |
| NFR-1 | 每对话一次文件 IO | 命中 0 次读盘；未命中 2 次读 + 最多 1 次目录列表（AC-3.2 用日志 hit/sha 断言） |
| NFR-2 | 缓存内存上限 | LRU 64 条 × ≤12.4k 字符 ≈ ≤800 KB；重启即清空，不落盘 |
| NFR-3 | token 预算 | 默认注入 ≤12 400 字符（按 1.5 字符/token ≈8k tokens，假设 A4），可配置下调 |
| NFR-4 | 无网络、无 LLM、无 DB | 播种与快照路径不调 llm_generate、不联网、不读主 DB |
| NFR-5 | 并发安全 | 纯读 + 单事件循环字典操作；同 umo 由会话锁串行；跨 umo 各写各键；播种靠 os.replace 原子性 |
| NFR-6 | 可观测 | DEBUG：conv=<cid[:8]> sha=<8位> chars=<n> hit=<bool>；WARNING：共享根缺失/读失败/区段异常；**日志不打印正文** |
| NFR-7 | 故障隔离 | 快照失败不导致 LLM 请求失败（退化块）；播种失败不阻止插件加载 |

实现细节与验证方式见 plan.md §8、§9。

---

## 6 边界与异常

| 场景 | 预期行为 | 依据 |
| --- | --- | --- |
| 共享根不存在 / 不是目录 | 不播种、不注入、不报错；`WARNING` 一条；`/astral status` 显示 | FR-2 |
| `SOUL.md` 是目录而非文件 | 视为不可读，快照该段走"缺失"降级 | FR-3 |
| 文件是符号链接 | `resolve()` 后必须仍在共享根内，否则按不可读处理（防越界读） | FR-3 |
| 两个文件都不可读 | 注入 ≤200 字符最小块 + `WARNING`；该键缓存为降级结果，避免每轮重试 IO | FR-3 |
| 空文件（用户清空） | 快照显示"（空文件）"；下次插件加载/重载会被重新播种（FR-2 已声明） | FR-2/FR-3 |
| `/reset` | 只清历史、不换 cid → 快照不刷新；`/astral memory show` 需说明"换新快照请用 `/new`" | FR-3 |
| 多群并发写同一份 `MEMORY.md` | 守则只允许 `file_edit` 局部替换；最坏情况是丢失一次并发追加（AstrBot 同一 umo 已被会话锁串行化，`internal.py:239`）；跨 umo 并发无锁，属已知限制 | FR-1、FR-4 |
| 用户手改 `EXTRA_PROMPT.md` 托管区段 | 下次插件加载/重载被覆盖（已在只读页与 `show extra` 披露） | FR-4 |
| 用户删掉标记对 | 追加新标记对，不覆盖用户正文 | FR-4 |
| `MEMORY.md` 持续膨胀 | 软上限提示 + 归档约定；不自动搬移 | FR-5 |
| 第三方 agent runner（`agent_runner.runner_type`） | `system_prompt` 是否被采纳未验证 → 标记为"未支持/未知"，见 U-6 | FR-3 |
| 模型上下文很小 | 12 000 字符预算可能过大 → 靠配置下调，不做模型自适应（U-9） | FR-5 |
| 插件被禁用 | 指令与页面都不可用；`EXTRA_PROMPT.md` 仍被 core 注入（文件在盘上）→ 行为是"守则在、快照没了" | FR-4 |

---

## 7 依赖与假设

| 编号 | 依赖（已核对） | 源码位置 |
| --- | --- | --- |
| D1 | core 读取 `<ws>/EXTRA_PROMPT.md`，拼成 `[Workspace Extra Prompt]` 追加 `req.system_prompt` | `astr_main_agent.py:451-486`，调用点 `1085` |
| D2 | `on_llm_request` 在 `build_main_agent` 之后触发，入参 `(event, req)` | `internal.py:307-308`；`register_on_llm_request` 在 `star_handler.py:457` |
| D3 | 对话 ID 可取 `req.conversation.cid` 或 `get_curr_conversation_id(umo)`（可能 `None`） | `astr_main_agent.py:1714`、`conversation_mgr.py:174` |
| D4 | `/new` 换 cid；`/reset` 只清历史 | `builtin_commands/main.py:55,44` → `commands/conversation.py:169-198,110-141` |
| D5 | 文件工具名与实现；相对路径基准 = 工作区根；工作区根在可写白名单内 | `fs.py:327/448/553/684`、`fs.py:377,480,596,867`、`fs.py:153-169` |
| D6 | `system_prompt` 为 `str`（默认 `""`），hook 返回值不影响流程 | `entities.py:212`；`context_utils.py:119-154` |
| D7 | 插件页只扫 `views/<page>/index.html`；bridge 只有 GET/POST，无 PUT/DELETE | `plugin_page_service.py:29-30,474-504`；`plugin_page_bridge.js:207-285` |
| D8 | 插件 API 路由必须以插件目录名为前缀 | `dashboard/api/plugins.py:250`；`context.py:774-796` |
| D9 | 同 umo 请求由会话锁串行化 | `internal.py:239`；`utils/session_lock.py:17,49` |
| D10 | `Star.initialize()` 每次插件加载/重载都会被 await；`OnAstrBotLoadedEvent` 全进程只在启动派发一次 | `base.py:141`、`star_manager.py:1422-1424`；`core_lifecycle.py:374` |

| 编号 | 假设（未核对，需 Lead 或实现时确认） | 未决编号 |
| --- | --- | --- |
| A1 | 001 的共享根默认 `<data>/workspaces/astral`，目录骨架与四个文件由 001 的 FR-8 创建 | — |
| A2 | 002 在 `initialize()` 时从 001 的配置项取得共享根绝对路径（不在对话期做路径推断）；具体键名见 U-2 | U-2 |
| A3 | `_conf_schema.json` 由 001 建立，002 只追加 `memory` 分组 | U-1 |
| A4 | 目标模型上下文 ≥ 32k tokens（12 000 字符按 1.5 字符/token 估 ≈ 8k tokens） | U-9 |
| A5 | 中文按"1 字符 ≈ 0.67 token"估算成本，仅用于预算说明，不参与逻辑 | — |

---

## 8 未决问题

全部细节见 `decisions.md` 末尾的未决问题表：U-1（配置分组归属）、U-2（取共享根的入口）、U-3（注入预算值）、U-4（容量软上限）、U-5（群聊是否给全文）、U-6（第三方 runner 支持度）、U-7（`/reset` 行为提示）、U-8（只读页边界显示）、U-9（小上下文模型）。

---

## 9 变更记录

| 日期 | 版本 | 变更 | 影响 |
| --- | --- | --- | --- |
| 2026-11-01 | v0.1 | 首版：确立四件套语义、首启播种、对话级冻结快照、托管守则、容量归档、只读可见性、隐私披露、备份迁移 | 取代原设计稿第 6 章 |
| 2026-11-01 | v0.1 | 修正两处理解：①`/reset` 不换 `cid`（只清历史），快照刷新只认 `/new`；②播种挂在 `Star.initialize()` 而非 `on_astrbot_loaded`（后者全进程只派发一次，插件重载不会重新播种） | FR-2、FR-3、AC-3.1 |
