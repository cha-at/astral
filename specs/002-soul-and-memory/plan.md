# 002 技术方案：播种、冻结快照与只读可见性

> 状态：草案 v0.1 · 依赖：001-shared-workspace · 最后更新：2026-11-01
> 基线：AstrBot 4.29.4 @ `831abae85`。本文件只写"怎么做"；需求与验收见 `spec.md`（FR-n / AC-f.n）。
> 路径约定见 `specs/README.md` §0：本文件中的 `astrbot/…`、`dashboard/…`、`fs.py:…` 等源码路径一律指 `../AstrBot-code/` 下的同名文件。

---

## 1 架构与模块

只新增 3 个 Python 模块 + 1 个页面，**不新增任何磁盘状态**（无 DB、无索引、无队列、无本地缓存文件）：

| 模块 | 职责 | 对外接口 | 显式不做 |
| --- | --- | --- | --- |
| `templates.py` | 四份中文模板、托管守则文案、`seed()`、`sync_managed_section()`、`atomic_write_text()` | `seed_workspace(root)`；`sync_managed_section(root)` | 不解析 Markdown、不做模板变量 |
| `snapshot.py` | 读文件 → 渲染块 → 冻结缓存；截断与降级 | `render(root) -> str`；`SnapshotCache.get_or_render(key, root)` | 不检索、不摘要、不写盘 |
| `main.py` | Star 子类：`initialize()`（播种+同步守则）、`on_llm_request`（注入）、`/astral memory` 指令、`register_web_api(".../files")` | — | 不在对话路径写盘 |
| `views/astral/index.html` | 只读页（状态 + 四文件预览） | bridge `apiGet` → 001 的 `state` + 本包 `files` | 无编辑、无刷新、无上传下载 |

指令与 API handler 直接内联在 `main.py`（各 20-40 行，不拆文件），符合 KISS 与"不为一次性逻辑抽函数"的仓库约定。

```text
插件加载/重载 Star.initialize():  seed() 空/缺才写  +  sync_managed_section() 幂等
对话 on_llm_request:             key=(umo,cid) → 命中则复用字符串；未命中则读 2 个文件渲染
                                 → req.system_prompt = old + "\n" + block
```

播种挂在 `initialize()` 而不是 `@filter.on_astrbot_loaded()`：后者全进程只在启动后派发一次（`core_lifecycle.py:374`），从 WebUI 重载插件不会重新触发，导致"删了文件不重播、守则改版不生效"。`initialize()` 在每次 `load()` 都被 await（`star_manager.py:1422-1424`），且此时不需要 provider/平台就绪。

关键取舍：**agent 写记忆走 core 的 `astrbot_file_*` 工具，Astral 不参与对话内写入**。因此 002 只有两个写盘时机：插件加载时播种、插件加载时同步托管区段。

---

## 2 文件与目录约定（与 001 一致，不重复定义）

```text
<共享根>/                  # 001 定义：默认 <data>/workspaces/astral
├── SOUL.md / MEMORY.md / AGENTS.md   # 002 播种；agent 写前两个，用户写 AGENTS
├── EXTRA_PROMPT.md        # 002 播种（含托管区段）；之后 002 只重写 astral:managed 区间
├── skills/  shared/       # 001 建
├── memory/archive/        # 001 建；002 只读（列索引）
└── archive/  .astral/     # 001 建；002 不读不写 .astral
```

| 文件 | 001（骨架） | 002（启动） | agent（对话中） | 用户（随时） |
| --- | --- | --- | --- | --- |
| `SOUL.md` | 只建目录，不建文件 | 空/缺 → 播种，否则不动 | 仅用户明确要求时 `file_edit` | 可手改 |
| `MEMORY.md` | 同上 | 同上 | 主要写者（局部追加/合并） | 可手改 |
| `AGENTS.md` | 同上 | 同上 | 不写 | 可手改 |
| `EXTRA_PROMPT.md` | 只建目录，不建文件 | 只重写标记之间 | 不写 | 可改标记之外 |

`EXTRA_PROMPT.md` 的托管区段是**唯一**由 Astral 覆盖内容的位置，其余文件"存在即不碰"。

---

## 3 关键流程

### 3.1 首启播种

```python
async def initialize(self):                     # Star.initialize()，每次插件加载/重载都执行
    root = self.shared_root()                   # 001 配置项（U-2），不做路径推断
    if not root.is_dir():
        logger.warning("astral: shared root missing: %s", root); return   # 目录归 001
    logger.info("astral: seed=%s managed=%s",
                templates.seed_workspace(root),
                templates.sync_managed_section(root))
```

| 判定 | 动作 |
| --- | --- |
| 已存在且 `strip()` 后非空 | 跳过（不覆盖） |
| 不存在 / 空文件 | 渲染模板 → `atomic_write_text()` |
| 是目录或符号链接 | 跳过 + `WARNING` |
| 托管区段：标记对完整 | 区段字节不同才整文件原子重写，区段外逐字保留 |
| 托管区段：标记缺失 | 末尾追加标记对 + 内容 |
| 托管区段：重复/嵌套/顺序颠倒 | 不写 + `WARNING` + 状态 `managed_broken` |

`atomic_write_text()`：同行临时文件 `.{name}.tmp.{pid}` → `flush` + `os.fsync` → `os.replace`；异常时删临时文件并向上抛，调用方吞异常只记日志（启动不得失败）。

### 3.2 一次对话内的快照

```python
@filter.on_llm_request()
async def inject_snapshot(self, event, req):
    if not self.memory_enabled: return
    cid = getattr(req.conversation, "cid", None) or await self._curr_cid(event.unified_msg_origin)
    key = (event.unified_msg_origin, cid or "no-conv")
    try:
        block = await self.cache.get_or_render(key, self.root_provider)
    except Exception:
        logger.exception("astral: snapshot failed"); block = snapshot.DEGRADED_BLOCK
    req.system_prompt = f"{req.system_prompt or ''}\n{block}"
```

| 时点 | 动作 | 成本 |
| --- | --- | --- |
| 对话内第 1 条消息 | 读 2 个文件（+ 截断时列归档）→ 渲染 → 入缓存 | 2–3 次 IO |
| 对话内第 2..n 条 | 字典查找，复用同一字符串 | 0 次 IO |
| `/new` 之后 | 新 `cid` → 重新渲染 | 2–3 次 IO |
| `/reset` 之后 | `cid` 不变 → 复用旧快照 | 0 次 IO（已知，需披露） |
| 进程重启 | 缓存为空 → 首次请求重建 | 2–3 次 IO |

### 3.3 agent 写记忆之后

| 观察点 | 行为 |
| --- | --- |
| 写入所在对话 | 快照不变（冻结）；要用新内容须自己 `astrbot_file_read_tool` |
| 同 umo 的 `/new` | 快照含新内容 |
| 其它群/平台的新对话 | 可见（全局一份） |
| 只读页 / `GET files` / `show` | **立即**看到磁盘最新内容（每次读盘） |

---

## 4 快照渲染规格

### 4.1 块格式（逐字）

```text
[Astral Memory Snapshot] v1 | conv=<cid 前 8 位> | frozen_at=<ISO8601 本地时间>
优先级：AstrBot 安全策略 > SOUL.md 的边界 > Persona Instructions > SOUL.md 的身份与语气 > MEMORY.md 的事实。
本快照在本对话内冻结，不随后续消息刷新。

### SOUL.md（身份与边界 · <n> 字符）
<SOUL 正文，或降级提示行>

### MEMORY.md（长期记忆 · <m> 字符）
<MEMORY 正文，或降级提示行>

### 快照说明
- 以上内容在本对话开始时冻结，磁盘上的文件可能已更新。
- 需要最新内容：用 astrbot_file_read_tool 读取 SOUL.md / MEMORY.md（相对路径以当前工作区根为基准）。
- 想换成新快照：/new。
```

固定开销 ≈ 400 字符。头部与说明段必须写成 `snapshot.py` 常量，便于测试断言与 `v1 → v2` 平滑升级。

### 4.2 预算与截断

| 项 | 默认值 | 配置键（U-1） |
| --- | --- | --- |
| SOUL 注入预算 | 4000 字符 | `memory.soul_budget_chars` |
| MEMORY 注入预算 | 8000 字符 | `memory.memory_budget_chars` |
| 注入总量上限 | 12 400 字符（含固定开销） | — |
| MEMORY 容量软上限 | 20000 字符 | `memory.soft_limit_chars` |
| 单文件读取上限（只读接口） | 512 KB | 常量（对齐 `chat_projects.py:19`） |
| 归档索引条数 | 10 | 常量 |

截断时在该段正文后追加（正文其余部分不动）：

```text
…
[已截断：MEMORY.md 原文 <total> 字符，此处仅保留前 8000 字符。完整内容：<abs_path>（可用 astrbot_file_read_tool 读取）]
[归档索引] memory/archive/：MEMORY-202608.md (12.3 KB)、MEMORY-202609.md (8.1 KB)…… 最多 10 条
```

优先保留**头部**（约定：身份在前、最新条目在前）；归档索引仅在该段被截断且 `memory/archive/` 可读时追加，读目录失败只记 `DEBUG` 并省略该行。

### 4.3 降级矩阵

| 状态 | 该段输出 | 日志 |
| --- | --- | --- |
| 文件不存在 | `（SOUL.md 不存在：<abs_path>；需要时可用 astrbot_file_write_tool 创建）` | `INFO`（每键一次） |
| 空文件 | `（SOUL.md 为空文件）` | 无 |
| 非 UTF-8 | `errors="replace"` 解码正文 + `（含非 UTF-8 字节，已按替换字符显示）` | `WARNING` |
| 读取异常 | `（读取失败：<异常类名>；详见日志）` | `WARNING` + 栈 |
| 符号链接越界 | `（SOUL.md 指向共享根之外，已拒绝读取）` | `WARNING` |
| 两文件都不可用 | 头部 + `（SOUL.md 与 MEMORY.md 均不可用：<共享根>）`，整块 ≤200 字符 | `WARNING` |
| 其它未预期异常 | `snapshot.DEGRADED_BLOCK`（≤200 字符） | `ERROR` |

**注入永不阻塞对话**：`on_llm_request` 内全部逻辑包在 `try/except` 里，最坏退化为 200 字符提示；`memory.enabled=false` 直接 `return`。

### 4.4 为什么追加到 `req.system_prompt` 而不是 `extra_user_content_parts`

| 维度 | `system_prompt += block`（采用） | `extra_user_content_parts` + `mark_as_temp()`（否决） |
| --- | --- | --- |
| 语义 | 系统级、对话级持久 | `mark_as_temp()` 只设 `_no_save`（"provider-facing only, not persisted"，`agent/message.py:68-71`）；`context.py:254` 定位为 per-turn context，与"对话级冻结"不一致 |
| 稳定性 | append-only，块在对话内逐字节稳定 → 整段 `system_prompt` 稳定 → 前缀缓存命中 | 挂在用户消息尾部、逐轮拼装，容易滑回"每轮重新决定注入什么"（ADR-2 已否决） |
| 实现 | 一次字符串拼接 | 需构造 `ContentPart`；`openai_source.py` / `anthropic_source.py` / `gemini_source.py` 处理各不相同 |
| 代价 | 占系统提示预算 | 不占系统提示预算，但受用户消息截断策略影响 |

钩子在 `build_main_agent` 之后触发，此刻 `req.system_prompt` 已含默认系统提示、`# Persona Instructions`（若选 persona）与 `[Workspace Extra Prompt]`；本包**只追加，不改写**。

### 4.5 最终 `system_prompt` 顺序

| 顺序 | 内容 | 来源 |
| --- | --- | --- |
| 1 | `LLM_SAFETY_MODE_SYSTEM_PROMPT`（仅 `safety_mode_strategy == "system_prompt"`，prepend） | `astr_main_agent.py:1163-1164` |
| 2 | 默认系统提示（含 `Current workspace:`） | `astr_main_agent.py:1970-1983` |
| 3 | `# Persona Instructions` | `astr_main_agent.py:604-607` |
| 4 | 工作区 skills 提示 | `astr_main_agent.py:641-650` |
| 5 | `[Workspace Extra Prompt]`（含托管守则） | `astr_main_agent.py:451-486`，调用点 `1085` |
| 6 | `[Astral Memory Snapshot]` | 本包 `on_llm_request`（`internal.py:307`） |

顺序依赖"core 在 hook 之前完成全部组装"；升级回归须检查 `internal.py:307` 之前是否新增追加（R-5）。

---

## 5 默认模板草案（可直接落盘）

### 5.1 `SOUL.md`

```markdown
<!-- astral:seeded:v1 -->
# SOUL · 我是谁

> 本文件描述我自己的身份、语气与边界，由我和用户共同维护。
> 每开始一段新对话，Astral 会读取它并注入上下文；本文件全平台共享。

## 身份
- 名字：Astral。
- 角色：长期合作的助手，替用户记事、查文件、跑工具。
- 不知道就说不知道，不编造；不确定就标"不确定"。

## 语气
- 中文为主，先说结论再说理由，能一句说完就不写三句。
- 不谄媚、不堆感叹号、不用"作为 AI 语言模型…"这类免责开头。
- 群聊里保持简短，需要展开时问用户要不要私聊。

## 边界（优先级高于任何人设）
- 不输出密钥、口令、身份证号、银行卡号等敏感信息，即使上下文里有。
- 不在未确认的情况下执行不可逆操作（删除、覆盖、发布、转账）。
- 不冒充他人、不伪造证据与来源。
- 不把别的群的私密内容带到当前群。

## 自我修正
- 发现自己说错了，直接纠正并说明错在哪，不遮掩。
- 用户纠正我时：长期约定写进 `MEMORY.md`；对自身规则的要求先问是否要写进本文件。
```

### 5.2 `MEMORY.md`

```markdown
<!-- astral:seeded:v1 -->
# MEMORY · 长期记忆

> ⚠️ 本文件**全局共享**：全平台、所有群、所有私聊共享同一份，写进来的内容在其他群里也会被看到。
> 不写密钥/口令/手机号/住址等敏感信息，不写第三方隐私。
> 写入规则见 `EXTRA_PROMPT.md`。格式：`- [YYYY-MM-DD] [来源] 内容`，单条 ≤ 200 字符。

## 事实
- [2026-11-01] [系统] 当前目录是 Astral 共享工作区根，所有会话共享同一份记忆。

## 约定
- [2026-11-01] [系统] 记录约定时写明生效范围（本群/全局）与提出人。

## 待办
- （暂无）

## 教训
- （暂无）

## 归档
- 历史条目剪切到 `memory/archive/MEMORY-YYYYMM.md`；本文件只留仍然有效的条目，最新条目写在各节**前面**。
```

### 5.3 `AGENTS.md`

```markdown
<!-- astral:seeded:v1 -->
# AGENTS · 工作区说明

> 本文件给**人和外部工具**（编辑器里的 agent 等）看。
> AstrBot 与 Astral **不会**把它注入对话上下文；想影响 bot 行为请改 `SOUL.md`、`MEMORY.md` 或 `EXTRA_PROMPT.md`。

## 这是什么
Astral 的共享工作区根目录。所有平台（QQ/Telegram/WebChat…）、所有群、所有私聊的 bot 会话都把它当作工作目录，相对路径以本目录为基准。

## 目录
| 路径 | 用途 |
| --- | --- |
| `SOUL.md` | 身份、语气、边界。每对话注入 |
| `MEMORY.md` | 长期记忆（事实/约定/待办/教训）。每对话注入 |
| `AGENTS.md` | 本文件，不注入 |
| `EXTRA_PROMPT.md` | 注入型操作守则；`astral:managed` 标记之间由 Astral 重写，之外随便改 |
| `skills/` | 工作区技能，每个技能一个 `SKILL.md` |
| `shared/` | 跨会话共享的交付物，别把成品散落在根目录 |
| `memory/archive/` | 记忆归档，`MEMORY-YYYYMM.md` |
| `archive/` | 其他归档 |
| `.astral/` | Astral 自用状态，不要手改 |

## 约定
- 交付物优先放 `shared/`；临时文件放 `archive/` 或删掉。
- 不要提交 `.astral/` 下的内容。
- 不确定文件该放哪就问用户。
```

### 5.4 `EXTRA_PROMPT.md` 托管区段

```markdown
<!-- astral:managed:begin -->
<!-- astral:managed:v1 -->
[Astral 记忆守则 · 本区段由 Astral 自动维护，手改会在下次启动被覆盖]

## 何时写记忆
满足任一条件才写，纯闲聊不写：
1. 用户明确说"记住 / 以后都这样 / 别再…"。
2. 出现长期有效的约定、决定、截止时间、待办。
3. 出现可复用的事实（偏好、项目背景、术语表）。
4. 出现可复用的教训（踩过的坑、失败的做法）。

## 怎么写
- 只写 `MEMORY.md`；用 `astrbot_file_edit_tool` 做局部追加或替换，**不要**用 `astrbot_file_write_tool` 整文件覆盖。
- 追加前先用 `astrbot_grep_tool` 搜关键词；已有同类条目就合并成一条，不重复。
- 格式：`- [YYYY-MM-DD] [来源] 内容`，单条 ≤ 2 行、≤ 200 字符；来源写群名或"用户"。
- 条目按"最新在前"插入对应小节（事实 / 约定 / 待办 / 教训）。
- 相对路径以当前工作区根为基准（`MEMORY.md`、`memory/archive/...`）。

## 写 SOUL 的门槛
- 只有用户**明确要求**改性格、语气或边界时才改 `SOUL.md`，改前用一句话复述要改什么。
- 用户的长期偏好若只是"事实/约定"，写 `MEMORY.md`，不要写 `SOUL.md`。

## 容量
- `MEMORY.md` 超过 20000 字符时：先合并重复与过期条目，再把历史条目剪切到 `memory/archive/MEMORY-YYYYMM.md`，然后继续写。

## 禁止
- 不写密钥、口令、token、私钥、身份证/护照/银行卡号、手机号/邮箱/住址，不写第三方私密内容或未经同意的个人信息。
- 需要记录"某人偏好"时去标识化：写"群里有人偏好中文回复"，不写群号+姓名+手机号。
- 用户转述他人隐私并要求记住时，先问"是否要记、记到什么粒度"。

## 快照是旧的
- 上下文里的 `[Astral Memory Snapshot]` 是对话开始时的冻结副本，可能已经过期。
- 需要最新内容或准备改写时，先用 `astrbot_file_read_tool` 读当前文件，以文件为准。

## 隐私披露
- 本工作区全局共享：写进 `MEMORY.md` 的内容在其他群也可能被看到，写入前自行判断能否公开。
<!-- astral:managed:end -->
```

四份文案与本节守则**只存在于 `templates.py`**；改动要同步 `spec.md` FR-4 的"守则必须覆盖"清单与 T-11。

---

## 6 接口契约：`GET /astrbot_plugin_astral/files`

`context.register_web_api("/astrbot_plugin_astral/files", handler, ["GET"], "Astral read-only file preview")`。路由首段必须是插件目录名（`dashboard/api/plugins.py:250`），对外路径为 `/api/v1/plugins/extensions/astrbot_plugin_astral/files`。

参数：`name` ∈ `SOUL` \| `MEMORY` \| `AGENTS` \| `EXTRA`（大小写不敏感）；缺省 → 返回清单（不含正文）。

```json
// 200 · 清单（扁平 JSON，与 001 的 state 一致，不使用 status/data 信封）
{"shared_root":"C:\\...\\data\\workspaces\\astral","memory_soft_limit":20000,
 "files":[{"name":"SOUL","path":"SOUL.md","abs_path":"…\\SOUL.md","exists":true,
           "size":1234,"chars":1180,"mtime":"2026-11-01T09:50:44+08:00",
           "seeded_version":1,"managed_ok":null}],
 "archive":{"dir":"memory/archive","count":3,"names":["MEMORY-202608.md"]}}

// 200 · 单文件
{"name":"MEMORY","path":"MEMORY.md","abs_path":"…\\MEMORY.md",
 "exists":true,"size":5678,"chars":5600,"mtime":"2026-11-01T09:50:44+08:00",
 "encoding":"utf-8","truncated":false,"content":"# MEMORY · 长期记忆\n…"}
```

| HTTP | 场景 | 响应体 |
| --- | --- | --- |
| 400 | `name` 不在白名单（含 `../` 等注入尝试） | `{"error":"unknown_name","message":"unknown name: <原值>"}` |
| 404 | 合法 name 但文件不存在 | `{"error":"not_found","message":"file not found","name":"SOUL"}` |
| 500 | 读取失败 | `{"error":"read_failed","message":"read failed","detail":"<异常类名>"}` |
| 405 | 非 GET | FastAPI 默认（只注册 GET） |

约束：单响应 ≤512 KB（超出返回前 512 KB 且 `truncated=true`）；只读，不提供 PUT/POST/DELETE；不返回目录树、不返回归档正文（只给文件名与数量）；不回传异常栈。001 的 `state` 字段由 001 定义，本包只消费"补丁状态 + 共享根"，缺失时显示"状态未知"。

---

## 7 只读页：`views/astral/index.html`

| 区块 | 内容 | 交互 |
| --- | --- | --- |
| 顶部横幅 | "全局共享：所有平台/所有群/私聊共享同一份记忆；不要写入敏感信息" | 无 |
| 状态行 | 补丁状态徽标（001 `state`）+ 共享根绝对路径 + `managed_ok` | 无 |
| 文件卡 | 四张卡（`SOUL.md`/`MEMORY.md`/`AGENTS.md`/`EXTRA_PROMPT.md`），显示字符数与更新时间 | 点击切换预览 |
| 容量条 | `MEMORY.md` 当前字符数 / 20000，超阈值变红 | 无 |
| 预览区 | `<pre>` 只读渲染，等宽、可滚动 | 无 |
| 底部 | "托管区段由 Astral 重写，标记之外属你；备份请整目录拷贝共享根" | 无 |

实现约束：①加载即 `bridge.ready()` → 并行 `apiGet("state")`、`apiGet("files")`，点卡片再 `apiGet("files", {name})`；②**没有**任何写入口（无 `textarea`/`contenteditable`、无保存/上传/下载/刷新按钮）；③一律 `textContent` 渲染，禁止 `innerHTML`（防 `MEMORY.md` 里的 HTML 注入 iframe）；④单文件、内联 CSS/JS、无外部依赖（iframe 沙箱无 same-origin）；⑤错误态红条显示 `message`，不弹 alert；⑥只依赖两个 GET，与 bridge 能力集吻合（`plugin_page_bridge.js:207-285`）。

---

## 8 非功能要求

| 编号 | 要求 | 指标 / 验证 |
| --- | --- | --- |
| NFR-1 | 每对话一次文件 IO | 命中 0 次读盘；未命中 2 次读 + 最多 1 次目录列表（AC-3.2 用 mtime 断言） |
| NFR-2 | 缓存内存上限 | LRU 64 条 × ≤12.4k 字符 ≈ ≤800 KB；重启即清空，不落盘 |
| NFR-3 | token 预算 | 默认注入 ≤12 400 字符，按 1.5 字符/token 估 ≈8k tokens（假设 A4），可配下调 |
| NFR-4 | 无网络、无 LLM、无 DB | 播种与快照路径不调 `llm_generate`、不联网、不读 `data_v4.db` |
| NFR-5 | 并发安全 | 纯读 + 单事件循环字典操作；同 umo 由会话锁串行（`internal.py:239`）；跨 umo 各写各键；播种靠 `os.replace` 原子性 |
| NFR-6 | 可观测 | `DEBUG`：`conv=<cid[:8]> sha=<8位> chars=<n> hit=<bool>`；`WARNING`：共享根缺失/读失败/区段异常；**日志不打印正文** |
| NFR-7 | 故障隔离 | 快照失败不导致 LLM 请求失败（退化块）；播种失败不阻止插件加载 |

---

## 9 测试策略

### 9.1 单元测试（pytest，插件 `tests/`）

| 用例 | 输入 | 断言 | 关联 |
| --- | --- | --- | --- |
| `test_render_basic` | 正常两文件 | 含两段标题、字符数、尾注；总长 = 各段 + 固定开销 ±20 | AC-1.1 |
| `test_render_missing_and_empty` | 缺 `SOUL.md`；另跑空文件 | 分别为"不存在"/"空文件"提示；另一段正常 | AC-3.3 |
| `test_render_non_utf8` | 写 `b"\xff\xfe"` | 不抛异常，含"非 UTF-8"提示 | AC-3.3 |
| `test_render_both_missing` | 两文件都没有 | 退化块 ≤200 字符 | AC-3.3 |
| `test_render_truncate` | MEMORY 12000 字符 | 该段 ≤8000 + 提示行，含绝对路径 | AC-5.2 |
| `test_render_archive_index` | 截断 + 归档 2 个 / 12 个 | 索引含文件名；12 个时只列 10 个 | AC-5.2 |
| `test_render_soft_limit` | MEMORY 21000 字符 | 尾部含"容量提示"与 20000 | AC-5.1 |
| `test_cache_hit_same_conv` | 同 key 两次 | 第二次 0 次 `open`（mock 计数），字符串相同 | AC-3.2 |
| `test_cache_miss_new_conv` | 换 cid | 重新读盘，块内 `conv` 前 8 位改变 | AC-3.1 |
| `test_cache_fallback_no_conv` | cid 与 `req.conversation` 皆空 | 键退化为 `no-conv`，仍能渲染 | FR-3 |
| `test_seed_idempotent` | 空目录 / 0 字节文件 / 已有内容 | 分别为：创建四文件 / 重播 / 逐字不变 | AC-2.1、AC-2.2 |
| `test_seed_atomic_on_failure` | 让 `os.replace` 抛错 | 目标不变、临时文件已清理 | AC-2.3 |
| `test_managed_rewrite_keeps_outside` | 标记外有用户文本 | 区段被重写，区段外字节相同 | AC-4.1 |
| `test_managed_markers_missing_appends` | 删掉标记 | 末尾追加完整标记对，原内容不变 | AC-4.1 |
| `test_managed_markers_broken` | `end` 在 `begin` 前 | 不写盘，返回 `managed_broken` | FR-4 |
| `test_hook_appends_system_prompt` | 假 `req`（`system_prompt=""`） | 结果以 `\n[Astral Memory Snapshot]` 开头，原内容为前缀 | AC-3.1 |
| `test_hook_disabled` | `memory.enabled=false` | `system_prompt` 未改变 | FR-3 |
| `test_files_api_errors` | `name=../../x`；删掉 SOUL 后请求；600 KB 文件 | 400 / 404 / `truncated=true` | AC-6.2、FR-5 |

### 9.2 手工验收

| 场景 | 命令 / 观察点 | 关联 |
| --- | --- | --- |
| persona 冲突 | 设边界 + 选热情人设 → 问被禁话题 | AC-1.2 |
| 守则生效 | 群里"记住：周五 20:00 站会" → `Select-String` | AC-4.2 |
| 只读页 | 与 `state` 对比；DOM 中 `textarea` 数为 0 | AC-6.1 |
| 披露 | 三处固定文案 | AC-7.1 |
| 敏感信息 | 让 bot 记手机号/身份证 | AC-7.2 |
| 备份恢复 / 迁移 | 备份→删→恢复比 sha256；拷到另一台机器问旧事实 | AC-8.1、AC-8.2 |
| 冻结 | 同对话写入后立刻追问 | AC-3.1 |

### 9.3 明确不测

多进程同时写同一共享根（AstrBot 单实例部署，超出范围）；模型"是否自觉遵守守则"（只断言文件与指令的机械结果）；第三方 runner 下的注入效果（U-6）。

---

## 10 与 001 的接口依赖表

| 002 需要 | 来自 001 | 形式 | 缺失时的表现 |
| --- | --- | --- | --- |
| 共享根路径 | FR-1 / FR-2 | 001 的共享根配置项（U-2） | 退化为按 UMO 目录 → 播种撒错地方，启动 `WARNING` |
| 目录骨架 | FR-8 | 目录已存在 | 002 不建目录，跳过播种 + `WARNING` |
| 补丁状态 | plan §6 `GET .../state` | HTTP（页面）/ 内部状态（指令） | 显示"状态未知" |
| 托管标记约定 | FR-4 边界约定 | 文件内容 | 002 在末尾追加标记对 |
| `_conf_schema.json` | 001 建立 | 文件 | 002 追加 `memory` 分组（U-1） |
| `astral` 指令组 | 001 的 `/astral status` | `@filter.command_group("astral")` | 002 复用；若未建则自建并保留 `status` 名 |

反向依赖：无。001 不读 `SOUL.md` / `MEMORY.md`，只在骨架里占位。

---

## 11 风险与缓解

| 编号 | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R-1 | 快照陈旧被当成 bug | 回答与文件不一致 | 尾注 + 守则"以文件为准" + U-7；`/new` 一键换新 |
| R-2 | `MEMORY.md` 膨胀 | 截断后尾部不可见、token 变高 | 容量提示 + 守则"最新在前" + 软上限（U-4） |
| R-3 | 多群并发覆盖 | 丢失一次追加 | 守则强制 `file_edit` 局部替换；同 umo 有会话锁；跨 umo 为已知限制 |
| R-4 | 隐私泄露 | 跨群可见 | 三处披露 + 群聊只给元信息（U-5）+ 禁止清单；不自动扫描（N9） |
| R-5 | 注入位置漂移 | 快照不再在末尾，权重下降 | 升级回归加一条：核对 `internal.py:307` 之前是否新增 system_prompt 追加 |
| R-6 | 模板被用户改写 | 误以为会被覆盖 | `seeded:v1` 标记 + 页面/`show` 说明"只有托管区段会被重写" |
| R-7 | 前缀缓存失效 | 成本上升 | `frozen_at` 只在渲染时取一次随块冻结；单测断言同键两次渲染字节相同 |
| R-8 | 页面与 001 状态不一致 | 状态显示错误 | 只读既有字段，缺失即"状态未知"，不猜 |
| R-9 | 大上下文成本 | 每轮多付约 8k token（首请求后靠缓存） | 预算可配（U-3/U-9），默认值保守 |
