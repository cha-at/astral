> **状态：已归档（2026-11-01）**。本文已被 [`specs/`](../../specs/README.md) 取代，仅作为调研与设计过程记录保留。
>
> - 第 6 章「记忆功能设计」（SQLite + 作用域 + 自动抽取 + embedding）**已作废**，现行方案见 [`specs/002-soul-and-memory/`](../../specs/002-soul-and-memory/spec.md)。
> - 附录 A 的源码坐标存在 4 处漂移（`workspace_type` 默认值表述、`converted_files` 用 md5 后 6 位、`star_manager.load` 实际区间、权限矩阵区间）；升级核对一律以 [`specs/001-shared-workspace/plan.md`](../../specs/001-shared-workspace/plan.md) §8 为准。
> - 共享工作区补丁方案本身仍然有效，已扩展为 `specs/001-shared-workspace/`。
> - 本文正文里的相对路径（`_investigation/…`、`AstrBot-code/…`）均相对**项目根**，即本文件向上三级（`../../../`）；插件仓库自身的规格见 `../../specs/`。

---
# Astral · AstrBot 工作区改造与记忆功能 设计文档

> 适用插件：astrbot_plugin_astral（展示名 Astral）
> 目标 AstrBot：4.29.4（代码基线 AstrBot-code @ 831abae85）
> 文档状态：v1 设计稿，供 Astral 开发使用
> 依据：本仓库 _investigation/ 下的调研与 PoC（见附录 C）

## 目录

- [0. 结论摘要与范围](#0-结论摘要与范围)
- [1. 需求、目标与非目标](#1-需求目标与非目标)
- [2. AstrBot 工作区机制（开发必读）](#2-astrbot-工作区机制开发必读)
- [3. 总体方案：一个共享工作区](#3-总体方案一个共享工作区)
- [4. 共享工作区的目录设计](#4-共享工作区的目录设计)
- [5. 并发、隔离与安全](#5-并发隔离与安全)
- [6. 记忆功能设计](#6-记忆功能设计)
- [7. 施工计划（分阶段）](#7-施工计划分阶段)
- [8. 接口与集成清单](#8-接口与集成清单)
- [9. 测试计划与验收标准](#9-测试计划与验收标准)
- [10. 风险登记册与运维](#10-风险登记册与运维)
- [11. 附录](#11-附录)

---

## 0. 结论摘要与范围

结论：可行，且不需要修改 AstrBot 源码。

- AstrBot 的"工作区"不是独立服务，而是 一条 DB 记录 + 一个磁盘目录 + 每次请求现算的 cwd。
- 真正决定"agent 在哪个目录干活"的只有一个函数：astrbot.core.workspace.resolve_workspace_root_for_umo。
  插件与核心同进程、同 sys.modules、无沙箱，所以可以在插件加载时替换它，让所有平台、所有群、所有会话都返回同一个目录。
- 前端（工作区文件面板）是编译进 astrbot/dashboard/dist 的静态资源，插件改不了；Astral 的 UI 只能做成并列的插件页面（views/ + bridge）。
- webchat 的"项目（Project）"按需求不做，但它的解析函数 resolve_project_workspace_root 仍会被 Dashboard 文件面板调用，建议一并接管，否则会出现"面板显示的目录 != Agent 实际 cwd"。

一句话的施工结论：

> 顶层双写替换 2 个解析函数 + 2 个叶子函数兜底 + 一个可配置共享根 + 记忆子系统（SQLite + on_llm_request 注入 + llm_tool 写入）。

三条硬边界（写进 README，避免用户误解）：

1. 不能改内置 WebUI 的布局/按钮/面板（只能新增插件页面）。
2. 不能覆盖内置 HTTP 路由（插件 API 只在 /api/v1/plugins/extensions/... 与 /api/plug/... 下）。
3. patch 与 AstrBot 版本强耦合，升级必须回归（附录 A 有坐标）。

### 快速开始（开发顺序）

1. 抄 3.3 的 ws_shared.py 到插件目录，按 3.5 在 __init__ 安装、terminate 还原。
2. 用附录 B 的 20 行最小版先验证"所有平台/群是否真的共享"（/astral 指令或让 agent 执行 pwd）。
3. 按 4.1 建目录、写 AGENTS.md / EXTRA_PROMPT.md，接入 5.5 的黑名单。
4. P0 完成后再做记忆：先 6.5 的显式工具，再加 6.4 的注入，最后做 6.6 的自动抽取。
5. 每次升级 AstrBot 先跑 9.3 的回归清单。

---

## 1. 需求、目标与非目标

### 1.1 目标

| 编号 | 目标 | 验收方式 |
| --- | --- | --- |
| G1 | 所有 bot、所有群、所有私聊共享同一个工作区根目录 | 任意两个不同平台/群的会话，工具里查看 cwd 返回同一路径 |
| G2 | 原版"按 UMO 分目录 / 按项目分目录"的行为被完全替换 | 共享根之外不再产生新的会话/项目目录 |
| G3 | 共享根可配置（默认 data/workspaces/astral，支持绝对路径） | 改配置重启后生效；非法路径有明确报错 |
| G4 | 在共享工作区之上提供记忆能力（全局 / 群 / 用户作用域） | 同一群多轮对话可召回；不同群不串味 |
| G5 | 可观测、可回滚 | /astral/state 能看到补丁状态；停用插件后恢复原版行为 |

### 1.2 非目标

- 不做 webchat 项目工作区（session/project/custom 三类型的产品功能不迭代）。但仍需接管其解析函数（见 3.6），保证 Dashboard 面板与真实 cwd 一致。
- 不改内置 WebUI（不加按钮、不加 tab、不改工作区文件面板）。
- 不做多进程/多 worker 支持（AstrBot 默认单进程；若用户自行 gunicorn 多 worker，patch 只在各自进程内生效）。
- 不接管 sandbox runtime（computer_use_runtime=sandbox 时 agent 用容器内 /workspace，与 host 工作区无关）。

### 1.3 约束（必须知道）

1. 插件代码 = 管理员权限：能读写整个数据目录、所有会话、配置与 DB（官方文档也承认插件不在本地隔离范围内）。
2. AstrBot 的本地工具（shell/python/文件工具）只有 local runtime 才把工作区当 cwd；且 member 默认 filesystem_scope="workspace"、admin 默认 "host"。
3. api_key:* 创建的项目有 4 层防越界（创建/更新/启动迁移/运行时校验），Astral 的共享根方案会绕过其中一部分——需要自己加白名单（见 5.5），否则等于把这条安全边界关掉。
4. 工作区目录核心从不清理：删项目/删会话都不删目录。共享后的清理必须由 Astral 负责。

## 2. AstrBot 工作区机制（开发必读）

### 2.1 一句话模型

    [写入侧]
      WebUI ProjectDialog --POST/PATCH /api/v1/chat/projects--> ChatUIProjectService（唯一校验层）
          -> db.create/update_chatui_project
          -> SQLite: chatui_projects(workspace_type, workspace_path)
                     session_project_relations(session_id -> project_id)

    [读取侧]
      任意平台消息 -> build_main_agent()
          |- _get_workspace_path_for_umo() -> resolve_workspace_root_for_umo()
          |      |- EXTRA_PROMPT.md 注入 system_prompt
          |      |- <ws>/skills/* 发现 skill（仅 local runtime）
          |      |- 提示词里的 "Current workspace: ..."
          +- 每次工具调用: shell/python/file_read/write/edit/grep/send_message
                 -> computer_tools.util.workspace_root_for_context() -> 同一个解析

      WebUI 文件面板 --GET /chat/projects/{id}/workspace/{files,file,file/download}-->
          ChatUIProjectService -> resolve_project_workspace_root(project, fallback_umo)   ← 另一条入口

没有 ProjectFile 表、没有索引、没有缓存。目录内容就是"工作区内容"。
所以"换工作区"= 换一个目录路径；"共享工作区"= 让解析函数永远返回同一个目录。

### 2.2 数据模型（仅两张表参与）

astrbot/core/db/po.py:469-529：

| 表 | 字段 | 说明 |
| --- | --- | --- |
| chatui_projects | project_id, creator, title, emoji, description, workspace_type, workspace_path | creator 是用户名或 "api_key:<key_id>"；workspace_type 默认 session（DB）/ project（API 层） |
| session_project_relations | id, session_id(唯一), project_id | 一个会话最多属于一个项目 |

三种 workspace_type 的运行时含义（astrbot/core/workspace.py:121-165）：

| type | creator | 结果 |
| --- | --- | --- |
| session | 任意 | <data>/workspaces/<normalize(umo)>/ |
| project | 任意 | <data>/workspaces/project_<project_id>/ |
| custom | 普通用户 | workspace_path（绝对路径原样；相对路径必须落在 <data>/workspaces 子目录内） |
| custom | api_key:* | 强制降级为 project_workspace_root |
| 其它/脏数据 | 任意 | 回落 default_workspace_root |

<data> = ASTRBOT_ROOT/data（astrbot/core/utils/astrbot_path.py:30-41, 86-88），默认由启动 cwd 决定；桌面打包运行时是 ~/.astrbot。

### 2.3 解析链（唯一入口）

    async def resolve_workspace_root_for_umo(umo, db=None) -> Path:        # workspace.py:191-217
        parsed = parse_webchat_umo(umo)                 # :168-188，只认 webchat
        if not parsed:
            return default_workspace_root(umo)          # 非 webchat：永远是会话目录
        creator, session_id = parsed
        project = await db.get_project_by_session(session_id, creator)   # 每次查库，无缓存
        if not project:
            return default_workspace_root(umo)
        return resolve_project_workspace_root(project, fallback_umo=umo)  # :121-165

    def resolve_project_workspace_root(project, *, fallback_umo) -> Path:   # :121-165
        # 按上表的 workspace_type 分派；api_key 项目收尾再做一次越界校验
        ...

配套纯函数（workspace.py:22-118）：

| 函数 | 行 | 作用 |
| --- | --- | --- |
| normalize_umo_for_workspace(umo) | 22-32 | [^A-Za-z0-9._-]+ 替换为 _，空则 unknown |
| normalize_project_workspace_type(v) | 35-47 | 小写 + 白名单，非法值 -> session |
| normalize_workspace_path(p) | 50-62 | strip，空 -> None |
| default_workspace_root(umo) | 65-76 | <ws_root>/<normalize(umo)> |
| project_workspace_root(project_id) | 79-91 | <ws_root>/project_<safe_id> |
| workspace_path_to_root(path) | 94-118 | 绝对路径原样；相对路径必须在 <ws_root> 子目录内 |
| parse_webchat_umo(umo) | 168-188 | webchat:<MsgType>:webchat!<creator>!<cid> |

### 2.4 调用点矩阵（谁在消费工作区）

| 调用方 | 位置 | 何时 | 用途 |
| --- | --- | --- | --- |
| _get_workspace_path_for_umo | astrbot/core/astr_main_agent.py:429-448 | 每次构建 Agent 请求 | 主链路唯一入口；异常时回落会话目录 |
| _apply_workspace_extra_prompt | astr_main_agent.py:451-486（调用点 1085，无条件） | 每个 LLM 请求 | 读工作区 EXTRA_PROMPT.md 注入 system_prompt |
| 工作区 skills | astr_main_agent.py:626-632 | 每个请求（仅 local） | list_workspace_skills(ws_root) |
| Current workspace 提示 | astr_main_agent.py:1970-1983 | 每个请求（仅 local） | 告诉模型 cwd |
| workspace_root_for_context | astrbot/core/tools/computer_tools/util.py:62-80 | 每次工具调用 | 工具执行期唯一入口 |
| shell / python | shell.py:117-122、python.py:160-177 | 工具调用 | cwd = workspace；目录按需 mkdir |
| 文件工具 | fs.py:377,480,596,867 | 工具调用 | 相对路径基准 + 允许根 |
| 发文件 | message_tools.py:144-186 | 工具调用 | 相对路径只在工作区内解析 |
| Dashboard 文件面板 | dashboard/services/chatui_project_service.py:216,374,450 | HTTP 请求 | 列目录/预览/下载（另一条解析函数） |
| 定时任务/主动消息 | core/cron/manager.py:482-486 | 定时 | 走同一个 build_main_agent |
| 子代理 | core/subagent_orchestrator.py | 子任务 | 继承同一 event/umo，间接用同一解析 |

结论：Agent 侧只有一个入口（resolve_workspace_root_for_umo），Dashboard 侧是另一个入口（resolve_project_workspace_root）。两条必须分别接管。

### 2.5 工作区里的"魔法路径"

| 路径 | 读取方 | 效果 |
| --- | --- | --- |
| EXTRA_PROMPT.md | astr_main_agent.py:451-486 | 文本追加到 system_prompt（[Workspace Extra Prompt]）；与 runtime 无关，纯聊天 bot 也生效 |
| skills/<name>/SKILL.md | core/skills/skill_manager.py:394-469 | 请求级 skill；同名覆盖全局/插件 skill；仅 local runtime |
| converted_files/<name>_<md5前6>/text.txt | core/computer/file_read_utils.py:577-595 | 文档转文本落盘 |

### 2.6 生命周期与时序（决定 patch 装在哪）

    InitialLoader.start()                      # astrbot/core/initial_loader.py:26-52
     |- core_lifecycle.initialize()            # core_lifecycle.py:161-301
     |   |- db / sp / html_renderer
     |   |- config manager / persona / provider / platform / conversation / KB / cron（构造对象）
     |   |- Context(...)
     |   |- PluginManager(...) + reload()      # ← 插件被 import、类被实例化、initialize() 被 await
     |   |- provider_manager.initialize()
     |   |- kb_manager.initialize()
     |   |- load_pipeline_scheduler()
     |   |- EventBus(...)
     |   +- platform_manager.initialize()
     +- Dashboard server 创建（此时插件已加载）
    core_lifecycle.start()
     |- _load()
     |- 逐个 await OnAstrBotLoadedEvent handler    # ← 只在启动时触发一次
     +- gather(curr_tasks)

要点：

- 插件 __init__ 执行时，DB/配置/Context 已经就绪，但 provider/平台/pipeline/EventBus 还没有。
- on_astrbot_loaded 只在启动时触发一次；WebUI 里"重载插件"不会再次触发。所以补丁必须在 __init__ 安装，on_astrbot_loaded 只当"再扫一遍"的保险。
- 重载/停用流程会调用 terminate()（star_manager.py:1917, 1944-1966），但不会清理 Context.registered_web_apis（类属性，context.py:135）→ 停用后 Astral 的 HTTP 端点仍可能命中到进程重启。

### 2.7 前端与 API 的能力边界

| 事实 | 影响 |
| --- | --- |
| 工作区面板只有 3 个 GET（dashboard/api/chat_projects.py:160-212） | 没有上传/写入接口；文件由 agent 或 Astral 自己写 |
| 预览不超过 512KB、必须 UTF-8、不跟符号链接 | 大文件/二进制不要指望内置面板 |
| "选择文件夹"按钮只在桌面端（ProjectDialog.vue:246-249） | Docker/Web 部署只能手输路径；Astral 自己的页面要自己做目录浏览 |
| 前端是 astrbot/dashboard/dist 编译产物 | 不能改内置页面/加组件；只能 views/ 自带页面 |
| 插件 API 前缀：/api/v1/plugins/extensions/<plugin_path>（v1）；/api/plug/<plugin_path>（旧，仅 GET/POST） | 不能影子内置路由；路径首段会被当作 plugin_name |
| 鉴权：Dashboard JWT（Cookie astrbot_dashboard_jwt / Bearer）或带 plugin scope 的 API Key | 没有匿名端点 |
| 插件页面是受限 iframe（允许 scripts/forms/downloads，无 same-origin） | 拿不到 Dashboard cookie；必须走 bridge；bridge 只有 apiGet/apiPost/upload/download/subscribeSSE（无 PUT/DELETE） |

### 2.8 关键源码坐标速查

| 主题 | 位置 |
| --- | --- |
| 解析链全部 | astrbot/core/workspace.py（217 行，建议通读） |
| Agent 侧消费 | astrbot/core/astr_main_agent.py:429-486, 626-632, 1085, 1970-1983 |
| 工具侧消费 | astrbot/core/tools/computer_tools/util.py:11-14, 62-80；shell.py:117-122；python.py:160-177；fs.py:377,480,596,867 |
| Dashboard 侧消费 | astrbot/dashboard/services/chatui_project_service.py:8-17, 216, 374, 450 |
| 项目表/关系表 | astrbot/core/db/po.py:469-529 |
| 项目 CRUD | astrbot/core/db/sqlite.py:2446-2628；抽象声明 core/db/__init__.py:937-1016 |
| api_key 启动清理 | astrbot/core/db/sqlite.py:213-223 |
| 插件加载/实例化 | astrbot/core/star/star_manager.py:1076-1437 |
| 重载/停用 | astrbot/core/star/star_manager.py:1018-1074, 1905-1966 |
| 插件 Web API 注册 | astrbot/core/star/context.py:774-796 |
| 插件 API 路由分发 | astrbot/dashboard/api/plugins.py:196-272, 398-440, 1485-1491 |
| 插件页面扫描 | astrbot/dashboard/services/plugin_page_service.py:29-30, 474-504 |
| 本地权限矩阵 | astrbot/core/tools/computer_tools/util.py:62-199；默认值 core/config/default.py:22-33, 182-199 |
| 会话锁（按 umo） | astrbot/core/utils/session_lock.py；使用点 pipeline/process_stage/method/agent_sub_stages/internal.py:239 |

---

## 3. 总体方案：一个共享工作区

### 3.1 候选方案对比

| 方案 | 能否"所有 bot/所有群共享一个目录" | 改动面 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| A. 只用配置/DB（ASTRBOT_ROOT、项目 custom） | 不能：只能按项目或按 UMO 分目录 | 0 | 无 | 不满足 G1 |
| B. 只 patch 叶子（default_workspace_root + project_workspace_root） | 能覆盖 session/project 两类（绝大多数情况） | 2 处赋值，不需要扫 sys.modules | custom 分支不接管 | 可用，但需配合 C |
| C. 顶层双写（resolve_workspace_root_for_umo + resolve_project_workspace_root + 扫 sys.modules） | 能，彻底覆盖所有类型 | 2 个函数 + 扫描 | 必须双写，否则静默失效 | 主方案 |
| D. 直接改 data/dist / 改源码 | 能 | 大 | 升级被覆盖，不可维护 | 禁止 |

选定：C 为主 + B 兜底。理由：

- C 覆盖所有分支（包括 custom 与 webchat 项目），语义上"彻底替换"。
- B 很便宜（两处赋值），能兜住那些直接调用叶子函数的旁路（fallback、第三方插件），且天然不需要扫描，和 C 互为保险。
- 两套都只依赖 4 个函数名，升级面很小，且失效是可观测的（/astral/state 能看出来）。

### 3.2 共享根的选择与配置

_conf_schema.json：

    {
      "shared_root": {
        "type": "string",
        "description": "共享工作区根目录",
        "hint": "留空 = <AstrBot data>/workspaces/astral；也可填绝对路径（如 D:/astral-workspace 或 ~/astral）。",
        "default": ""
      },
      "patch_webchat_projects": {
        "type": "bool",
        "description": "同时接管 webchat 项目工作区解析（让 Dashboard 文件面板显示共享目录）",
        "hint": "关闭后：Agent 仍在共享目录，但 webchat 项目面板会显示项目自己的目录，容易误解。建议开启。",
        "default": true
      },
      "forbidden_roots": {
        "type": "list",
        "description": "禁止被设为共享根的路径前缀（安全护栏）",
        "hint": "默认包含 AstrBot 源码目录、data/config、data/plugins、用户主目录关键子目录等。",
        "default": []
      }
    }

解析规则（实现在 ws_shared.py）：

1. 空 -> <data>/workspaces/astral。
2. ~ 展开；相对路径 -> 相对 <data>/workspaces。
3. resolve(strict=False) 后检查与 forbidden_roots 的前缀关系，命中则拒绝启动 patch 并在日志/状态里报错（不要静默回落，否则用户以为生效了）。
4. 启动时 mkdir(parents=True, exist_ok=True)。

### 3.3 补丁实现（完整代码）

建议单独成文件 ws_shared.py，只依赖标准库 + AstrBot 内部模块，便于单测。

    # astrbot_plugin_astral/ws_shared.py
    """把 AstrBot 的工作区解析统一指向一个共享根目录。

    设计要点（务必读）：
    1) AstrBot 的调用方几乎都是 from astrbot.core.workspace import X，
       这是 import 时名字绑定：只改 astrbot.core.workspace.X 对已经
       import 过的模块无效（补丁会看起来装上了但静默失效）。
       因此必须双写：改源模块属性 + 扫描 sys.modules 改所有调用方全局。
    2) 补丁状态挂在 astrbot.core.workspace 模块上（不是挂在本插件模块），
       这样插件重载（sys.modules 会被清掉 data.plugins.*）也不会丢原始函数。
    3) 卸载必须把每个被改过的模块全局还原，否则会污染后续插件/原生行为。
    """

    from __future__ import annotations

    import importlib
    import sys
    from pathlib import Path
    from typing import Any, Callable

    _WS_MODULE = "astrbot.core.workspace"
    _STATE_ATTR = "__astral_patch_state__"

    # 顶层解析函数：覆盖 Agent 侧与 Dashboard 侧
    _TOP_NAMES = ("resolve_workspace_root_for_umo", "resolve_project_workspace_root")
    # 叶子函数：兜底直接调用叶子的旁路（fallback、第三方插件）
    _LEAF_NAMES = ("default_workspace_root", "project_workspace_root")
    _ALL_NAMES = (*_TOP_NAMES, *_LEAF_NAMES)

    def _ws():
        return importlib.import_module(_WS_MODULE)

    def _make_wrappers(root: Path, patch_project: bool) -> dict[str, Callable[..., Any]]:
        """构造替换函数；签名必须与 AstrBot 原函数一致。"""

        async def _resolve(umo: str, db: Any = None) -> Path:
            return root

        def _resolve_project(project: Any, *, fallback_umo: str) -> Path:
            return root

        def _default(umo: str) -> Path:
            return root

        def _project_root(project_id: str) -> Path:
            return root

        wrappers = {
            "resolve_workspace_root_for_umo": _resolve,
            "default_workspace_root": _default,
            "project_workspace_root": _project_root,
        }
        if patch_project:
            wrappers["resolve_project_workspace_root"] = _resolve_project
        for name, fn in wrappers.items():
            fn.__astral_name__ = name  # 标记：用于识别旧 wrapper，避免重载叠加
        return wrappers

    def _iter_modules_bound(name: str, original: Any, ws):
        """产出所有该名字仍指向原函数/旧 wrapper 的模块。"""
        for module in list(sys.modules.values()):
            if module is None or module is ws:
                continue
            try:
                current = getattr(module, name, None)
            except Exception:
                continue
            if current is None:
                continue
            if current is original or getattr(current, "__astral_name__", None) == name:
                yield module

    def install(shared_root, *, patch_webchat_projects: bool = True) -> dict:
        """安装/更新共享工作区补丁。幂等，可重复调用（例如重载插件后）。"""
        ws = _ws()
        root = Path(shared_root).expanduser().resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)

        state = getattr(ws, _STATE_ATTR, None)
        if state is None:
            originals = {n: getattr(ws, n) for n in _ALL_NAMES if hasattr(ws, n)}
            state = {"originals": originals, "wrappers": {}}
            setattr(ws, _STATE_ATTR, state)

        wrappers = _make_wrappers(root, patch_webchat_projects)
        state["wrappers"] = wrappers
        state["root"] = str(root)

        # 1) 源模块属性（覆盖补丁之后才 import 的模块）
        for name, wrapper in wrappers.items():
            if name in state["originals"]:
                setattr(ws, name, wrapper)

        # 2) 扫描 sys.modules（覆盖补丁之前已 import 的模块）
        rebound = []
        for name, wrapper in wrappers.items():
            original = state["originals"].get(name)
            if original is None:
                continue
            for module in _iter_modules_bound(name, original, ws):
                try:
                    setattr(module, name, wrapper)
                    rebound.append(f"{getattr(module, '__name__', '?')}.{name}")
                except Exception:
                    continue

        return {"installed": True, "root": str(root), "rebound": sorted(set(rebound))}

    def uninstall() -> dict:
        """还原所有被改写的函数。重复调用安全。"""
        ws = _ws()
        state = getattr(ws, _STATE_ATTR, None)
        if not state:
            return {"uninstalled": False, "reason": "not installed"}

        restored = []
        for name, original in state["originals"].items():
            wrapper = state["wrappers"].get(name)
            if getattr(ws, name, None) is wrapper:
                setattr(ws, name, original)
                restored.append(f"{_WS_MODULE}.{name}")
            for module in list(sys.modules.values()):
                try:
                    if getattr(module, name, None) is wrapper:
                        setattr(module, name, original)
                        restored.append(f"{getattr(module, '__name__', '?')}.{name}")
                except Exception:
                    continue

        delattr(ws, _STATE_ATTR)
        return {"uninstalled": True, "restored": sorted(set(restored))}

    def status() -> dict:
        """自检：补丁是否生效、当前根、有哪些模块走的是 wrapper。"""
        ws = _ws()
        state = getattr(ws, _STATE_ATTR, None)
        if not state:
            return {"installed": False}
        consumers = {}
        for name, wrapper in state["wrappers"].items():
            consumers[name] = sorted(
                {
                    getattr(m, "__name__", "?")
                    for m in sys.modules.values()
                    if getattr(m, name, None) is wrapper
                }
            )
        return {"installed": True, "root": state.get("root", ""), "consumers": consumers}

> 说明：完整可运行、带 5 个 Web API 与可视化页面的版本见 _investigation/poc/astrbot_plugin_ws_lab/（73 条断言验证过双写/还原/路由）。
>
> 注意：如果把 patch_webchat_projects 从 true 改成 false，已接管的 resolve_project_workspace_root 不会自动还原。
> 实现时请让这个开关"重启生效"，或在 install() 里对"在 originals 中但不在 wrappers 中"的名字做一次还原（遍历 sys.modules 把旧 wrapper 换回原函数）。

### 3.4 为什么必须双写（这是最容易踩的坑）

astrbot/core/astr_main_agent.py:141-144（真实源码）：

    from astrbot.core.workspace import (
        normalize_umo_for_workspace,
        resolve_workspace_root_for_umo,
    )

from M import f 会把函数对象绑进调用方模块的 globals；函数体里的 await resolve_workspace_root_for_umo(...) 是 LOAD_GLOBAL，取调用方自己的 globals，不是 astrbot.core.workspace 的模块属性。所以：

    # 错误：对已经 import 过的调用方完全无效（静默失效）
    astrbot.core.workspace.resolve_workspace_root_for_umo = my_wrapper

AstrBot 4.29.4 中相关调用方全部是 from-import（astr_main_agent.py:141、computer_tools/util.py:11、chatui_project_service.py:8），没有一处 import astrbot.core.workspace as m。所以扫 sys.modules 不是可选优化，而是必需步骤。

已验证：只改模块属性 -> 已导入调用方仍返回旧路径（负对照）；双写后 -> 两种调用方都返回共享根；uninstall() 后完全还原（正对照）。

### 3.5 安装、卸载与重载安全

main.py 片段：

    from pathlib import Path

    from astrbot.api import logger
    from astrbot.api.star import Context, Star
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    from . import ws_shared

    class AstralPlugin(Star):
        def __init__(self, context: Context, config=None):
            super().__init__(context, config)
            self.config = config or {}

            raw = (self.config.get("shared_root") or "").strip()
            root = (
                Path(raw).expanduser()
                if raw
                else Path(get_astrbot_data_path()) / "workspaces" / "astral"
            )
            try:
                report = ws_shared.install(
                    root,
                    patch_webchat_projects=bool(self.config.get("patch_webchat_projects", True)),
                )
                logger.info("astral: shared workspace installed -> %s", report)
            except Exception:
                logger.error("astral: install shared workspace failed", exc_info=True)

            context.register_web_api(
                "/astrbot_plugin_astral/state",
                self.api_state,
                ["GET"],
                "Astral 状态",
            )

        async def terminate(self):
            """停用/重载时还原，避免污染原生行为。"""
            logger.info("astral: uninstall -> %s", ws_shared.uninstall())

插件自己的数据目录可用 StarTools.get_data_dir("astrbot_plugin_astral")（见 6.2）。

重载安全的三条规则：

1. 状态挂核心模块：__astral_patch_state__ 挂在 astrbot.core.workspace 上。插件模块会被 sys.modules purge（star_manager.py:742-774），但核心模块不会，所以重载后仍能拿到原始函数。
2. 幂等安装：install() 每次都重建 wrapper 并重扫；旧 wrapper 靠 __astral_name__ 标记被识别替换，不会叠加。
3. terminate 必还原：核心在停用/重载时会调用（star_manager.py:1917, 1944-1966）。若开发期热替换导致没调用，下一次 install() 也能靠 __astral_name__ 剥掉旧 wrapper。

已知平台缺陷（4.29.4，需在文档/日志里提示）：Context.registered_web_apis 是类属性且停用不清空，停用 Astral 后它的 HTTP 端点仍可能可达；端点里应检查 ws_shared.status()["installed"]，未安装时返回 503。

### 3.6 旁路与一致性

| 旁路 | 是否需要处理 | 说明 |
| --- | --- | --- |
| Dashboard 工作区文件面板 | 需要：patch resolve_project_workspace_root | 否则面板显示项目目录、Agent 用共享根，两者不一致 |
| _get_workspace_path_for_umo 的异常兜底 | 不必 | 只在 db 不是 BaseDatabase 或解析抛错时触发；我们的 wrapper 直接 return，不会抛 |
| fs._workspace_root(umo) 兼容函数 | 不必 | 只在 current_workspace_root is None（sandbox/非 local）时用，此时工作区语义本来就不相关 |
| workspace_path_to_root | 不要 patch | Dashboard 新建/修改项目时用它做路径校验（chatui_project_service.py:518）；patch 它会让校验与实际解析不一致 |
| 第三方插件自己算 data/workspaces/<umo> | 尽力而为 | 无法拦截；在 README 中说明共享根只影响 AstrBot 内置解析 |

### 3.7 生效范围

- 所有平台适配器（aiocqhttp / telegram / discord / slack / lark / wecom / satori / webchat ...）：resolve_workspace_root_for_umo 对非 webchat 直接返回 default_workspace_root，被我们接管。
- 群聊、私聊、频道：同一根目录。
- 定时任务 / 主动消息：走同一个 build_main_agent。
- 子代理：继承同一 event/umo。
- 纯聊天 bot（computer_use_runtime=none）：没有 cwd 概念，但 EXTRA_PROMPT.md 仍会被读取（见 2.5）。
- 不生效：computer_use_runtime=sandbox（agent 在容器 /workspace 内干活，host 工作区不参与）。

### 3.8 自检接口（强烈建议）

GET /api/v1/plugins/extensions/astrbot_plugin_astral/state 返回：

    {
      "installed": true,
      "shared_root": "C:/Users/x/.astrbot/data/workspaces/astral",
      "consumers": {
        "resolve_workspace_root_for_umo": ["astrbot.core.astr_main_agent", "astrbot.core.tools.computer_tools.util"],
        "resolve_project_workspace_root": ["astrbot.dashboard.services.chatui_project_service"]
      },
      "memory": {"scope_count": 12, "db": ".../memory.db"}
    }

consumers 为空 = 补丁没生效（典型原因：函数改名、AstrBot 大版本变更）。

---

## 4. 共享工作区的目录设计

### 4.1 目录树（建议）

    <shared_root>/                      # 默认 <data>/workspaces/astral
    |- AGENTS.md                        # 给 agent 的工作区说明（Astral 生成/可手改）
    |- EXTRA_PROMPT.md                  # 全局提示词（AstrBot 自动注入；低频修改）
    |- skills/                          # 全局工作区技能（AstrBot 自动发现，仅 local runtime）
    |  +- memory/SKILL.md               #   例：教模型怎么用记忆工具
    |- shared/                          # 跨 bot/群的公共资产（知识、素材、脚本）
    |- memory/                          # 记忆的人可读镜像（Astral 维护）
    |  |- global.md
    |  |- groups/<platform>_<group_id>.md
    |  +- users/<platform>_<user_id>.md
    |- sessions/<scope>/                # 可选：每会话/每群临时产物（约定，不强制）
    |- tasks/<task_id>/                 # 可选：一次性任务目录
    |- converted_files/                 # AstrBot 自动写入（文档转文本）
    |- archive/                         # 归档/快照（Astral 定时任务写入）
    +- .astral/                         # Astral 自身元数据（agent 也可读，不要放密钥）
       |- state.json
       |- audit.jsonl
       +- quota.json

### 4.2 与内置约定的关系（重要）

| 约定 | 共享后的语义 | 注意 |
| --- | --- | --- |
| EXTRA_PROMPT.md | 全局提示词，所有 bot/群都读同一份 | 想按群定制必须用 on_llm_request 注入；频繁改它会让 system_prompt 每轮变化、破坏 prompt cache |
| skills/ | 全局技能包，所有会话可见 | 可作为 Astral 的能力包分发；仅 local runtime 生效 |
| converted_files/ | 所有会话共用 | 命名含内容 md5，同内容会复用，冲突概率低 |
| 文件列表 | 所有群在 Dashboard 面板里看到同一份 | 这是完全共享的预期行为，但要在产品文案里说清楚 |

### 4.3 命名规范与"每个群一个子目录"的取舍

用户诉求是完全共享，所以不要强制每群一个目录。推荐约束：

- 公共资产放 shared/，临时产物放 tasks/<task_id>/ 或 sessions/<scope>/。
- 这些约定写进 AGENTS.md / EXTRA_PROMPT.md，由 Agent 自己遵守（软约束）。
- 需要硬约束时，由 Astral 提供 astral_task_dir 之类的 LLM 工具，返回并创建唯一目录，鼓励 Agent 使用。

### 4.4 首次初始化与迁移

首次安装 Astral 时：

1. mkdir -p 共享根与子目录；
2. 写默认 AGENTS.md（说明共享工作区、目录约定、并发注意事项）；
3. 写默认 EXTRA_PROMPT.md（全局人设补充，保持稳定）；
4. 可选：从 <data>/workspaces/* 迁移历史文件——建议复制而不是移动，按 mtime 冲突策略合并，原目录保留一个版本周期。

---

## 5. 并发、隔离与安全

### 5.1 AstrBot 的并发模型

- 事件总线对每个事件 create_task（core/event_bus.py:73）-> 不同会话会并发处理。
- 只有同一 UMO 的 LLM 请求会被串行化：session_lock_manager.acquire_lock(event.unified_msg_origin)（utils/session_lock.py，使用点 agent_sub_stages/internal.py:239）。
- 结论：两个不同的群完全可能同时在同一目录里执行 shell / 写文件。

### 5.2 共享目录的竞态清单

| 竞态 | 场景 | 影响 | 对策 |
| --- | --- | --- | --- |
| 同名文件覆盖 | 两个群同时让 agent 写 report.md | 后写覆盖先写 | 约定 + tasks/<id>/；关键写入用 filelock |
| 目录创建竞争 | 同时 mkdir | 一般无害（exist_ok=True） | 无需处理 |
| converted_files | 同时转同一文件 | 内容相同，可接受 | 无需处理 |
| git 操作 | 两个群在同一 repo 里 checkout/commit | 冲突/索引锁 | tasks/<id>/ 独立 clone/worktree；或给 git 操作加 filelock |
| shell 长时间任务 | 同 cwd 下两个进程互踩中间文件 | 结果错乱 | 文档提示 + 任务目录约定 |
| 临时文件 | tmp_xxx 同名 | 覆盖 | 用 tempfile/uuid |

建议实现：filelock 已经是 AstrBot 依赖，可直接 import。Astral 自己的写操作（记忆镜像、审计、快照）用 FileLock(<shared_root>/.astral/astral.lock) 串行；Agent 侧的写入靠约定 + 提示词，不做强制。

### 5.3 权限矩阵与"共享"带来的隐私问题

默认值（core/config/default.py:22-33）：

| 角色 | allow_execution | allow_network | filesystem_scope |
| --- | --- | --- | --- |
| member | False | False | workspace |
| admin | True | True | host |

- member 的沙箱可读写根 = data/skills、插件 skills、当前工作区、临时目录。共享后"当前工作区"就是全体共享目录 -> 所有群的成员都能读到彼此的文件（这正是 G1 的语义，但必须在产品文档中写明）。
- admin 默认 host -> 对管理员来说工作区从来不是边界。
- 若要"共享但只读一部分"：可以在 on_using_llm_tool 里拦截写类工具（astrbot_file_write_tool 等），或把 filesystem_scope 收窄（属于改核心配置，不推荐由插件代劳）。

### 5.4 只读模式（可选，实验性）

若要给用户一个"共享只读资产库"模式：

- 方案一：不 patch 权限，而是拦截写类 LLM 工具（on_using_llm_tool 返回拒绝），简单但只挡工具调用。
- 方案二：patch computer_tools.util.get_local_permission_policy，把 filesystem_scope 收窄为 none 或改 allow_execution（会同时影响 shell/python，需谨慎）。
- 方案三：共享根放在只读挂载/只读 ACL 上，靠操作系统兜底（最硬）。

### 5.5 危险路径黑名单（必须做）

插件无沙箱，一旦把共享根指向敏感目录，Agent 的 shell/admin 工具就能直接改它们。启动时校验 shared_root 不得是/位于：

- AstrBot 源码目录（get_astrbot_path()）
- <data>/config、<data>/plugins、<data>/plugin_data
- 用户主目录本身（至少禁止 ~、~/.ssh、~/.aws、~/.config）
- 系统目录（Windows 的 C:/Windows、Linux 的 /etc /usr /var、macOS 的 /System）

命中时：拒绝安装并报错（不要静默改用默认值）。

### 5.6 清理、归档与配额

- 核心不清理工作区 -> Astral 应提供 archive/ 归档策略（按时间/大小）、/astral clean 指令、管理页按钮。
- 可选配额：定时统计共享根体积，超限时在 EXTRA_PROMPT 或管理页提示（不要自动删）。

---

## 6. 记忆功能设计

### 6.1 记忆分层与作用域

| 层 | 内容 | 写入时机 | 读取时机 |
| --- | --- | --- | --- |
| profile（稳定事实） | "这个群的负责人在做 X 项目" | 显式工具 / 抽取 | 每轮（按 scope） |
| episodic（事件摘要） | "今天讨论并决定了 Y" | 回合结束抽取 / 定时摘要 | 检索命中时 |
| semantic（知识条目） | 领域知识、操作手册 | 显式工具 / 导入 | 检索命中时 |
| working（本轮上下文） | 临时状态 | 不落库 | 当轮 |

作用域（scope）设计：

| scope | 键 | 用途 |
| --- | --- | --- |
| global | 固定 | 所有 bot/群共享的记忆（人设补充、全局规则） |
| group:<platform>:<group_id> | 平台群 ID | 群记忆（默认主作用域） |
| user:<platform>:<sender_id> | 平台用户 ID | 跨群用户画像 |
| group_member:<platform>:<gid>:<uid> | 组合 | 群内个体记忆（可选） |

默认策略：群聊读写 group + global；私聊读写 user + global。可通过配置 memory_scopes 调整。

### 6.2 存储选型

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| AstrBot 主 DB（context.get_db()） | 现成、事务 | 无 migration 钩子，改核心表风险大 | 不推荐建表 |
| 插件 KV（star.put_kv_data） | 简单 | 只适合小对象，无查询 | 只存开关/元数据 |
| 插件数据目录下 SQLite（推荐） | 独立、可迁移、可索引 | 需要自己维护 schema | 采用 |
| memory/*.md 文件 | 人可读、可手改 | 并发写、检索弱 | 作为镜像/导出 |

推荐：SQLite（WAL）+ 异步镜像 memory/*.md。
路径：StarTools.get_data_dir("astrbot_plugin_astral") / "memory.db"。

### 6.3 表结构

    CREATE TABLE IF NOT EXISTS facts (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      scope       TEXT NOT NULL,
      key         TEXT NOT NULL,
      content     TEXT NOT NULL,
      source_umo  TEXT,
      confidence  REAL NOT NULL DEFAULT 1.0,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL,
      expires_at  TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_facts_scope_key ON facts(scope, key);
    CREATE INDEX IF NOT EXISTS ix_facts_scope_updated ON facts(scope, updated_at DESC);

    CREATE TABLE IF NOT EXISTS episodes (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      scope      TEXT NOT NULL,
      summary    TEXT NOT NULL,
      detail     TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_episodes_scope ON episodes(scope, created_at DESC);

    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

### 6.4 读取与注入（on_llm_request）

不要每轮改 req.system_prompt——会破坏 prompt cache、显著增加成本。AstrBot 官方文档明确建议：动态内容用 req.extra_user_content_parts（TextPart），且可以 mark_as_temp() 不写入历史。

    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.provider import ProviderRequest
    from astrbot.core.agent.message import TextPart

    MAX_MEMORY_CHARS = 1200      # 约 300-600 tokens，可配置

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.config.get("memory_enabled", True):
            return
        block = await self.memory.render(
            scopes=self.scope_helper.scopes_for(event),
            query=req.prompt or "",
            budget_chars=MAX_MEMORY_CHARS,
        )
        if not block:
            return
        req.extra_user_content_parts.append(
            TextPart(text="<memory>\n" + block + "\n</memory>").mark_as_temp()
        )

渲染规则（v1）：

1. 取 group / user / global 三个 scope 的 facts（未过期）。
2. 相关性：query 关键词与 content+key 的子串/词重叠（中文按 2-gram、英文按空格切分）。
3. 排序：relevance * 0.6 + recency * 0.3 + confidence * 0.1。
4. 截断到 MAX_MEMORY_CHARS，每条一行（含来源 scope 与日期）。
5. 无命中则不注入（避免每轮都塞一堆无关记忆）。

### 6.5 写路径 A：显式工具（先做这个）

    @filter.llm_tool(name="memory_save")
    async def memory_save(self, event: AstrMessageEvent, content: str, key: str = "", ttl_days: int = 0):
        """保存一条长期记忆。

        Args:
            content(string): 要记住的内容，简洁、自包含
            key(string): 可选主题键，用于去重与更新
            ttl_days(number): 可选过期天数，0 表示不过期
        """
        scope = self.scope_helper.primary_scope(event)
        await self.memory.upsert_fact(
            scope=scope,
            key=key or content[:32],
            content=content,
            source_umo=event.unified_msg_origin,
            ttl_days=int(ttl_days or 0),
        )
        yield event.plain_result("已记住。")

    @filter.llm_tool(name="memory_search")
    async def memory_search(self, event: AstrMessageEvent, query: str):
        """搜索记忆。

        Args:
            query(string): 搜索关键词
        """
        hits = await self.memory.search(self.scope_helper.scopes_for(event), query, limit=5)
        yield event.plain_result("\n".join("- " + h for h in hits) or "没有找到相关记忆。")

注意：@filter.llm_tool 通过 docstring 的 Args: 段生成参数 schema（参数名(类型): 描述），不读类型注解；格式写错会导致参数被静默丢弃。

### 6.6 写路径 B：回合结束自动抽取（v2）

    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api.provider import LLMResponse
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext

    @filter.on_agent_done()
    async def on_agent_done(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
        resp: LLMResponse,
    ):
        if not self.config.get("memory_auto_extract", False):
            return
        text = (resp.completion_text or "").strip()
        if text:
            self._extract_queue.put_nowait(
                (event.unified_msg_origin, event.message_str or "", text)
            )

    async def _extract_worker(self):
        while True:
            umo, user_text, bot_text = await self._extract_queue.get()
            try:
                prov = await self.context.get_using_provider_async(umo=umo)
                if prov is None:
                    continue
                r = await self.context.llm_generate(
                    chat_provider_id=prov.meta().id,
                    system_prompt=EXTRACT_PROMPT,
                    prompt=json.dumps(
                        {"user": user_text, "assistant": bot_text},
                        ensure_ascii=False,
                    ),
                )
                for fact in parse_facts(r.completion_text):
                    await self.memory.upsert_fact(scope=..., key=..., content=...)
            except Exception:
                logger.exception("astral: memory extract failed")
            finally:
                self._extract_queue.task_done()

要点：

- 抽取是异步旁路，不能阻塞回复；用队列 + 单个 worker（并发 1）即可。
- 每轮都抽取会额外消耗 token：默认关闭，按群/全局开关，限制单轮抽取 max tokens。
- 去重键：scope + key（ON CONFLICT DO UPDATE）；抽取 prompt 要求"只输出新事实或需要更新的事实"。
- 隐私：群聊抽取要遵守 memory_enabled 与 memory_private 配置；提供 /astral memory off。

### 6.7 检索升级（v3）

- v1：关键词 + recency（够用）。
- v2：用 context.get_all_embedding_providers() 做 embedding，存 BLOB，余弦检索；只对超过 N 条的记忆启用。
- v3：接入 AstrBot 知识库（kb_helper）做向量检索——KB 是全局设施，写入前要评估对其他功能的影响。

### 6.8 记忆与共享工作区文件的关系

- SQLite 是权威源；memory/*.md 是可读镜像（每次写入后异步重写该 scope 的 md，或每小时全量导出）。
- 允许用户直接编辑 memory/*.md：启动时/定时反向导入（按 updated_at 较新者胜）。
- 好处：用户/Agent 都能用普通文件工具读写记忆，且换机器时随工作区一起搬。

### 6.9 遗忘与隐私

- /astral memory forget <scope|key> 指令；管理页按钮。
- TTL：expires_at 到期由定时任务清理。
- 群退群/删群：平台事件不一定可靠，提供"按 group_id 清理"接口 + 管理页。
- 配置项：memory_enabled（总开关）、memory_auto_extract、memory_scopes、memory_private（禁止读改写用户画像）。

### 6.10 记忆管理页（views/）

页面能力：按 scope 浏览、搜索、编辑、删除、导出；以及补丁状态、共享根、目录体积。

    const bridge = window.AstrBotPluginView;
    await bridge.ready();
    const list = await bridge.apiGet("memory/list", { scope: "group:telegram:123", q: "" });
    await bridge.apiPost("memory/save", { scope, key, content });

---

## 7. 施工计划（分阶段）

每个阶段都有独立验收，建议按顺序做；P0 完成即可日常使用。

### P0 · 共享工作区最小可用（0.5-1 天）

- [ ] ws_shared.py：4 个函数双写 + 幂等 install/uninstall + status
- [ ] main.py：配置解析、共享根校验（黑名单）、__init__ 安装、terminate 还原
- [ ] _conf_schema.json：shared_root、patch_webchat_projects、forbidden_roots
- [ ] /astral/state 接口
- [ ] /astral 指令：显示当前根 + 补丁状态

验收：Telegram 群、QQ 群、私聊各发一条让 agent 执行 pwd 的消息 -> 三者返回同一路径；在 A 群让 agent 写文件，B 群让 agent 读 -> 能读到。

### P1 · 稳定性与旁路（1-2 天）

- [ ] WebUI 项目面板一致（resolve_project_workspace_root 接管）
- [ ] 重载 10 次不叠加（/state 里 originals 不变、consumers 正常）
- [ ] 停用插件后恢复原版（每会话目录重新出现）
- [ ] 异常处理：函数缺失（新版本改名）时记录明显错误并降级为不 patch
- [ ] 日志：安装/卸载/根变更各一条 INFO

验收：连续重载/停用后，data/workspaces 不再产生新目录；/state 显示 consumers 正确。

### P2 · 工作区能力（2 天）

- [ ] 首次初始化目录与默认 AGENTS.md / EXTRA_PROMPT.md
- [ ] astral_task_dir 工具（创建 tasks/<id>/）
- [ ] 清理/归档（archive/ + 管理页按钮 + /astral clean）
- [ ] 审计日志 .astral/audit.jsonl（谁在哪个 umo 写了什么，只记工具调用摘要）
- [ ] 体积统计与配额提示

### P3 · 记忆 v1（2-3 天）

- [ ] MemoryStore（SQLite + WAL + asyncio + to_thread）
- [ ] scope 解析（global/group/user）
- [ ] on_llm_request 注入（extra_user_content_parts，默认 1200 字符预算）
- [ ] memory_save / memory_search / memory_forget 工具
- [ ] /astral memory 指令（list/search/forget）

验收：同群两轮：第二轮能召回第一轮写入的事实；换群问同样问题不能召回（除非是 global 记忆）。

### P4 · 记忆 v2 + 管理页（3-5 天）

- [ ] on_agent_done 自动抽取（默认关闭，队列 + 并发 1）
- [ ] 去重/TTL/置信度
- [ ] memory/*.md 镜像与反向导入
- [ ] views/memory-lab/ 管理页（列表/搜索/编辑/删除/导出）
- [ ] （可选）embedding 检索

### P5 · 发布（1 天）

- [ ] metadata.yaml：astrbot_version: ">=4.29,<5"、display_name、repo
- [ ] README：能力、限制（不能改内置 UI）、配置说明、升级注意事项
- [ ] 升级回归：跑附录 A 的坐标核对 + 一个最小 harness
- [ ] 版本号与 CHANGELOG

---

## 8. 接口与集成清单

### 8.1 Astral 对外 HTTP API（register_web_api）

| 方法 | 路由（注册值） | 说明 |
| --- | --- | --- |
| GET | /astrbot_plugin_astral/state | 补丁状态、共享根、consumers、记忆统计 |
| GET | /astrbot_plugin_astral/workspace/tree | 目录浏览（替代内置面板，支持分页/过滤） |
| POST | /astrbot_plugin_astral/workspace/mkdir | 创建目录（用于任务目录） |
| GET | /astrbot_plugin_astral/memory/list | 按 scope 列出记忆 |
| POST | /astrbot_plugin_astral/memory/save | 新增/更新记忆 |
| POST | /astrbot_plugin_astral/memory/forget | 删除记忆 |
| GET | /astrbot_plugin_astral/memory/export | 导出 markdown |

实际 URL：/api/v1/plugins/extensions/<route>；bridge 里 endpoint 写 state、memory/list 即可。
注意：bridge 只支持 GET/POST，所以不要设计 PUT/DELETE 接口（用 POST + action 字段）。

### 8.2 使用的事件钩子

| 钩子 | 用途 |
| --- | --- |
| @filter.on_llm_request | 注入记忆（extra_user_content_parts） |
| @filter.on_agent_done | 回合结束，入队抽取记忆 |
| @filter.llm_tool | 记忆读写工具、任务目录工具 |
| @filter.command | /astral 状态、/astral memory ... |
| @filter.on_astrbot_loaded | 补丁重扫兜底（不要只靠它） |
| @filter.on_decorating_result | （可选）给回复加记忆来源标注 |

### 8.3 存储

| 数据 | 位置 |
| --- | --- |
| 插件配置 | <data>/config/astrbot_plugin_astral_config.json（框架管理） |
| 记忆库 | data/plugin_data/astrbot_plugin_astral/memory.db |
| 审计/状态 | <shared_root>/.astral/* |
| 记忆镜像 | <shared_root>/memory/*.md |

### 8.4 被 Astral 替换的 AstrBot 函数

| 函数 | 语义 |
| --- | --- |
| astrbot.core.workspace.resolve_workspace_root_for_umo | 永远返回共享根（Agent 侧） |
| astrbot.core.workspace.resolve_project_workspace_root | 永远返回共享根（Dashboard 面板） |
| astrbot.core.workspace.default_workspace_root | 兜底：返回共享根 |
| astrbot.core.workspace.project_workspace_root | 兜底：返回共享根 |

---

## 9. 测试计划与验收标准

### 9.1 自动化（pytest，可在 CI 跑）

| 用例 | 断言 |
| --- | --- |
| test_install_patches_all_consumers | 用桩加载真实 workspace.py + 两个 from-import 调用方，install 后两者都返回共享根 |
| test_install_is_idempotent | 连续 install 3 次，originals 仍是原始函数，consumers 不重复 |
| test_uninstall_restores | uninstall 后所有模块恢复原函数，行为回到按 umo 分目录 |
| test_reload_without_terminate | 模拟"未卸载就再次 install"，不产生叠加，root 更新生效 |
| test_dashboard_consumer | Dashboard 风格调用方（from-import resolve_project_workspace_root）返回共享根 |
| test_forbidden_root_rejected | 指向源码/配置目录时 install 抛错 |
| test_memory_scope_isolation | 群 A 写入的记忆在群 B 查询不到 |

可直接复用 _investigation/poc/harness/harness.py 的桩环境（无第三方依赖）。

### 9.2 手工验收

1. Telegram 群 + QQ 群 + 私聊分别让 agent 执行 pwd（或文件工具写一个带群名的文件）-> 路径一致。
2. A 群写 shared/a.txt，B 群读 shared/a.txt -> 读到。
3. 两个群同时让 agent 写同名文件 -> 观察覆盖，确认提示词/工具是否有任务目录约定。
4. WebUI 工作区面板打开任一会话 -> 看到共享根的内容。
5. 停用 Astral -> agent 回到按会话目录；重新启用 -> 回到共享根。
6. 改动 shared_root 配置 -> 重启后生效，旧目录不再被写入。

### 9.3 升级回归（每次 AstrBot 大版本）

- 核对 astrbot/core/workspace.py 的 4 个函数是否仍存在、签名是否变化；
- 核对 astr_main_agent.py / computer_tools/util.py / chatui_project_service.py 是否仍用 from-import；
- 跑 harness（若失败，/state 的 consumers 会是空的）；
- 核对 Context.register_web_api、views/ 扫描、bridge 是否有变化。

---

## 10. 风险登记册与运维

| 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- |
| AstrBot 升级改名/换实现，补丁静默失效 | 中 | 高（工作区变回按会话） | /state 自检 + 启动日志 + harness 回归 |
| 重载叠加/未还原 | 中 | 中（行为混乱） | __astral_patch_state__ + __astral_name__ + terminate |
| 停用后 HTTP 端点仍可达 | 高（平台缺陷） | 低 | 端点检查 installed，未装返回 503 |
| 全群共享 -> 隐私泄露 | 高（设计） | 中 | 产品文案 + 可选只读/黑名单/私有子目录 |
| 并发写冲突 | 中 | 中 | 任务目录约定 + filelock |
| 共享根指到敏感目录 | 低 | 高 | 启动黑名单校验 |
| 沙箱 runtime 用户以为共享生效 | 中 | 低 | 文档说明 + /astral 显示 runtime |
| 备份遗漏共享工作区 | 中 | 高 | README 提醒：AstrBot 备份不含工作区文件（docs/zh/use/webui.md:141） |

运维建议：把共享根纳入备份（或至少纳入 archive/ 快照）；升级前先停用 Astral，升级后确认 /state。

---

## 11. 附录

### A. 源码坐标速查（升级时逐个核对）

    astrbot/core/workspace.py:22-118          纯函数（normalize/default/project/path_to_root）
    astrbot/core/workspace.py:121-165         resolve_project_workspace_root
    astrbot/core/workspace.py:168-217         parse_webchat_umo + resolve_workspace_root_for_umo
    astrbot/core/astr_main_agent.py:141-144   from-import（关键）
    astrbot/core/astr_main_agent.py:429-448   _get_workspace_path_for_umo
    astrbot/core/astr_main_agent.py:451-486   EXTRA_PROMPT.md
    astrbot/core/astr_main_agent.py:626-632   workspace skills
    astrbot/core/astr_main_agent.py:1970-1983 "Current workspace"
    astrbot/core/tools/computer_tools/util.py:11-14, 62-80
    astrbot/dashboard/services/chatui_project_service.py:8-17, 216, 374, 450
    astrbot/dashboard/api/chat_projects.py:160-212    工作区只读接口
    astrbot/core/db/po.py:469-529
    astrbot/core/db/sqlite.py:213-223                  api_key 启动清理
    astrbot/core/db/sqlite.py:2446-2628                项目 CRUD
    astrbot/core/star/context.py:774-796              register_web_api
    astrbot/dashboard/api/plugins.py:196-272, 398-440 插件 API 分发
    astrbot/dashboard/services/plugin_page_service.py:29-30, 474-504   views 扫描
    astrbot/core/star/star_manager.py:1076-1437        插件加载
    astrbot/core/star/star_manager.py:1905-1966        停用/终止
    astrbot/core/utils/session_lock.py                 按 umo 的会话锁

### B. 最小可用代码（可直接粘到 main.py 调试）

    import importlib
    import sys

    ws = importlib.import_module("astrbot.core.workspace")
    _orig = ws.resolve_workspace_root_for_umo
    _orig_project = ws.resolve_project_workspace_root

    SHARED_ROOT = ...  # Path(...)

    async def _resolve(umo: str, db=None):
        return SHARED_ROOT

    def _resolve_project(project, *, fallback_umo: str):
        return SHARED_ROOT

    def install():
        ws.resolve_workspace_root_for_umo = _resolve
        ws.resolve_project_workspace_root = _resolve_project
        for m in list(sys.modules.values()):
            if getattr(m, "resolve_workspace_root_for_umo", None) is _orig:
                m.resolve_workspace_root_for_umo = _resolve
            if getattr(m, "resolve_project_workspace_root", None) is _orig_project:
                m.resolve_project_workspace_root = _resolve_project

仅用于验证思路；生产请用 3.3 的完整版（含幂等、还原、状态自检、重载安全）。

### C. 参考产物（本仓库）

| 文件 | 内容 |
| --- | --- |
| _investigation/ws-backend.md | 工作区全链路地图（数据模型/解析链/API/前端/15 条坑/9 个扩展缝隙） |
| _investigation/plugin-surface.md | 插件扩展面（生命周期/官方扩展点/URL 与鉴权/硬限制/文档纠错） |
| _investigation/poc-report.md | PoC 验证报告（为什么必须双写、73 条断言、升级风险、扩展点） |
| _investigation/poc/astrbot_plugin_ws_lab/ | 可运行示例插件（补丁 + 5 个 Web API + views 页面） |
| _investigation/poc/harness/harness.py | 验证脚本（加载真实 workspace.py + 真实路由匹配） |

### D. 术语表

| 术语 | 含义 |
| --- | --- |
| UMO | unified_msg_origin，形如 telegram:GroupMessage:123 / webchat:FriendMessage:webchat!alice!cid |
| 工作区 | agent 本地执行时的 cwd 与文件读写根 |
| 共享根 | Astral 指定的唯一目录，所有会话共用 |
| 双写 | 同时修改源模块属性与所有已导入调用方模块的同名全局 |
| scope | 记忆的作用域键（global/group/user） |

---

本文档基于 AstrBot 4.29.4 源码与实测；若目标版本不同，请先按附录 A 核对坐标，再按第 9.3 节回归。









