# 001 · 共享工作区 技术方案（plan）

> 状态：草案 v0.1 · 最后更新：2026-11-01 · 基线：AstrBot 4.29.4 @ `831abae85`（`pyproject.toml` version 4.29.4）
> 需求与验收见 `spec.md`；任务拆分见 `tasks.md`；取舍理由见 `decisions.md`。
> §8 的坐标是本项目**唯一**的源码坐标来源，其它文档引用时必须写「见 001 plan §8」，不得自带行号。

## 1. 架构与模块

```text
astrbot_plugin_astral/
├─ main.py                # 插件入口：配置读取、安装/还原、指令、钩子装配（< 250 行）
├─ ws_shared.py           # 补丁核心：install / uninstall / status（纯函数，可单测）
├─ config.py              # 配置解析与校验（shared_root 解析、黑名单判定）
├─ _conf_schema.json      # WebUI 配置表单
├─ metadata.yaml          # 插件元数据（含 astrbot_version）
├─ requirements.txt       # 若为空则删除
├─ views/astral/          # 只读页（002 负责）
├─ tests/                 # pytest：补丁语义、配置、黑名单、接口
└─ README.md / CHANGELOG.md
```

职责边界：

| 模块 | 做 | 不做 |
| --- | --- | --- |
| `ws_shared.py` | 替换/还原 4 个函数、维护状态对象、扫描消费者 | 不读配置、不建目录、不写文件 |
| `config.py` | 把配置字典解析成确定的 `Path`、判定黑名单、给出错误原因 | 不安装补丁 |
| `main.py` | 装配：解析配置 → 建目录 → 播种 → 安装 → 注册路由/指令 | 不直接操作 `sys.modules` |
| `snapshot.py`（002） | 记忆快照 | 不参与工作区解析 |

## 2. 配置

### 2.1 `_conf_schema.json`

```json
{
  "shared_root": {
    "type": "string",
    "description": "共享工作区根目录",
    "hint": "留空 = <AstrBot data>/workspaces/astral；支持 ~ 与相对路径（相对 <data>/workspaces）。",
    "default": ""
  },
  "patch_webchat_projects": {
    "type": "bool",
    "description": "同时接管 webchat 项目工作区解析（Dashboard 文件面板）",
    "hint": "关闭后 Agent 仍在共享根，但面板会显示项目自己的目录，容易误解。建议保持开启。",
    "default": true
  },
  "forbidden_roots": {
    "type": "list",
    "description": "额外禁止的共享根前缀（安全护栏）",
    "hint": "默认已包含 AstrBot 源码、data/config、data/plugins、用户主目录敏感区与系统目录。",
    "default": []
  },
  "quota_gb": {
    "type": "float",
    "description": "共享根体积告警阈值（GB，0 = 不限制，只告警不删除）",
    "default": 0
  }
}
```

（记忆相关配置在 002 的 `_conf_schema.json` 片段中定义，命名前缀统一 `memory_`。）

### 2.2 解析规则（`config.py`）

```text
resolve_shared_root(raw: str) -> Path
  raw = (raw or "").strip()
  if raw == "":               base = data_path / "workspaces" / "astral"
  elif raw.startswith("~"):   base = Path(raw).expanduser()
  elif Path(raw).is_absolute(): base = Path(raw)
  else:                       base = data_path / "workspaces" / raw
  return base.resolve(strict=False)
```

`data_path` 用 `astrbot.core.utils.astrbot_path.get_astrbot_data_path()`（与核心一致，桌面打包时自动落到 `~/.astrbot`）。

对其它模块（含 002、聊天指令与 HTTP 端点）只暴露一个入口：`config.get_shared_root(cfg: dict) -> Path`——内部依次完成 `resolve_shared_root()` 与黑名单校验，失败时抛带原因的异常。禁止各处自行拼路径或重复推断（002 在 `initialize()` 里调用它取共享根）。

### 2.3 黑名单（`config.py`）

```text
check_forbidden(root: Path, extra: list[str]) -> str | None   # 返回命中的规则描述，None = 通过
  规则表（顺序固定、可读）：
    AstrBot 源码     get_astrbot_path()
    数据敏感区       data/config, data/plugins, data/plugin_data
    用户敏感区       Path.home(), Path.home()/".ssh", ".aws", ".config"
    系统目录         win32: C:/Windows, C:/Program Files
                     posix: /etc, /usr, /var, /System
    用户追加         forbidden_roots 中每一项
  判定：root == 规则路径 或 root.is_relative_to(规则路径)  → 命中
  规则路径本身先 resolve(strict=False)，比较用 normcase 以适配 Windows 大小写
```

命中即返回原因（第一条命中的规则名 + 路径），由 `main.py` 转成"安装失败"。

## 3. 补丁核心（`ws_shared.py`）

### 3.1 被替换的 4 个函数（签名必须逐字一致，来自基线核对表 §8）

| 名字 | 基线签名 | 位置 |
| --- | --- | --- |
| `resolve_workspace_root_for_umo` | `async def (umo: str, db: BaseDatabase \| None = None) -> Path` | workspace.py 191-217 |
| `resolve_project_workspace_root` | `def (project: Any, *, fallback_umo: str) -> Path` | workspace.py 121-165 |
| `default_workspace_root` | `def (umo: str) -> Path` | workspace.py 65-76 |
| `project_workspace_root` | `def (project_id: str) -> Path` | workspace.py 79-91 |

### 3.2 双写算法

```text
install(root, *, patch_project=True) -> Report
  1. 前置检查（config.py 已完成黑名单/可写性）
  2. ws = importlib.import_module("astrbot.core.workspace")
  3. 取 state = getattr(ws, "__astral_patch_state__", None)
     不存在则 originals = {n: getattr(ws, n) for n in ALL if hasattr(ws, n)}，
     缺任一函数 → 直接失败（FR-6），不安装
  4. wrappers = 按 §3.3 构造（每次重建；带 __astral_name__ 标记）
  5. 写入侧 ①：setattr(ws, name, wrapper)
  6. 写入侧 ②：遍历 sys.modules，对仍持有 original 或旧 wrapper 的模块 setattr(name, wrapper)
  7. state 更新 root / wrappers / installed_at；返回 Report(installed, rebound[])
```

- 扫描判定条件：`current is original` 或 `getattr(current, "__astral_name__", None) == name`，避免把第三方同名函数误改。
- 扫描跳过 `astrbot.core.workspace` 自身（已在步骤 5 处理）。
- 所有 `setattr` 包 try/except，失败计入 `skipped[]` 并在日志中列出（不中断整体安装）。
- 安装失败时不写入任何 wrapper，保证"要么全装，要么不装"（AC-2.4 / AC-6.1）。

### 3.3 包装函数（保持语义最小）

```python
async def _resolve_umo(umo: str, db=None) -> Path: return _ROOT
def _resolve_project(project, *, fallback_umo: str) -> Path: return _ROOT
def _default(umo: str) -> Path: return _ROOT
def _project_root(project_id: str) -> Path: return _ROOT
```

- 包装函数直接返回常量，**不读库、不做 IO**（NFR-1）。
- 不得修改 `workspace_path_to_root`：Dashboard 新建/修改项目时用它做路径校验，接管会让"校验"与"解析"分裂（ADR-4）。

### 3.4 状态对象

挂在 `astrbot.core.workspace` 模块上（插件模块会被 `sys.modules` 清理，核心模块不会）：

```python
__astral_patch_state__ = {
    "originals": {name: callable},
    "wrappers":  {name: callable},
    "root": "C:/.../astral",
    "installed_at": "2026-11-01T09:00:00+08:00",
    "baseline": {"astrbot": "4.29.4", "commit": "831abae85"},
}
```

### 3.5 卸载

```text
uninstall() -> Report
  state 不存在 → {"uninstalled": false, "reason": "not installed"}
  对每个 name：
    - 若 ws.name is wrapper → 还原为 original
    - 遍历 sys.modules，凡是 name 仍 is wrapper 的模块 → 还原为 original
  delattr(ws, "__astral_patch_state__")
```

限定：只还原"模块级全局仍指向 wrapper"的对象；若第三方把函数对象缓存进闭包/默认参数，无法回溯（测试断言只覆盖模块级，见 `spec.md` AC-5.2）。

### 3.6 `status()`

返回 `{installed, root, consumers: {name: [module...]}, baseline, installed_at}`；`installed` 以"state 存在且 4 个函数当前都是我们的 wrapper"为准，而不是只看 state 是否存在。

## 4. 生命周期与安装时序

| 时点 | 动作 | 依据 |
| --- | --- | --- |
| 插件 `__init__` | 读配置 → 解析根 → 黑名单 → 建目录（FR-8）→ `ws_shared.install()` → 注册路由/指令 | `__init__` 执行时 DB/Config/Context 已就绪，但 provider/平台/EventBus 尚未初始化 |
| `Star.initialize()` | 由 002 播种四个内容文件并同步 `EXTRA_PROMPT.md` 托管区段 | 核心在每次 `load()` 之后 await（`star_manager.py:1422-1424`），重载也会执行；001 不写这些文件的内容 |
| `on_astrbot_loaded` | 再次 `install()`（幂等，作为"重扫一遍"的保险） | 该事件只在启动时派发一次；WebUI 重载插件不会再触发 |
| `terminate()` | `ws_shared.uninstall()` + 关闭 002 的快照缓存 | 停用/重载时核心会调用 `terminate` |

要点：

- 补丁必须装在 `__init__`，**不能只依赖** `on_astrbot_loaded`（重载不触发）。
- 插件模块被重载时会从 `sys.modules` 清理；状态挂在核心模块上，因此重载后仍能拿到 originals。
- 若某次重载没走 `terminate`（开发期热替换），下次 `install()` 依靠 `__astral_name__` 识别并替换旧 wrapper。

## 5. 共享根目录骨架与首启播种

```text
<shared_root>/
├─ AGENTS.md            # 由 002 首启播种（不存在才写）
├─ EXTRA_PROMPT.md      # 由 002 首启播种，含 <!-- astral:managed:begin/end --> 托管区段
├─ SOUL.md              # 由 002 首启播种（模板见 002 plan §5）
├─ MEMORY.md            # 由 002 首启播种（模板见 002 plan §5）
├─ skills/              # mkdir，不写内容
├─ shared/              # mkdir
├─ memory/archive/      # mkdir（002 使用）
├─ archive/             # mkdir（FR-11 使用）
└─ .astral/
   ├─ state.json        # 每次成功安装后覆盖写入（installed/root/baseline/时间）
   └─ audit.jsonl       # 追加式
```

归属与播种规则（硬性）：

1. **001 只建目录**；四个内容文件（`AGENTS.md` / `EXTRA_PROMPT.md` / `SOUL.md` / `MEMORY.md`）由 002 的 `Star.initialize()` 播种（`002 FR-2`），001 不得写它们的内容；
2. 002 的播种规则：只写"不存在或为空白"的文件，绝不覆盖（AC-8.2）；临时文件 + `os.replace` 原子落盘；UTF-8（无 BOM）、LF 换行；
3. 默认文案放 `templates.py`（002 提供），播种入口 `seed_workspace(root)`；
4. 001 只写自用文件：`.astral/state.json`（每次成功安装后覆盖）与 `.astral/audit.jsonl`（追加）；
5. `.astral/` 下禁止出现任何密钥/token。

迁移（可选，P2）：若 `<data>/workspaces/` 下已存在历史会话目录，提供 `POST /astrbot_plugin_astral/migrate`（**只复制不移动**，按 mtime 冲突保留较新，原目录保留一个版本周期）。默认不自动执行。

## 6. HTTP 接口契约

所有路由必须以插件目录名为首段（`/astrbot_plugin_astral/...`），否则路由匹配不到；实际 URL 前缀由 Dashboard 拼成 `/api/v1/plugins/extensions/`。插件页 bridge 只支持 GET/POST，因此**不使用 PUT/DELETE**。

### 6.1 `GET /astrbot_plugin_astral/state`

响应 200：

```json
{
  "installed": true,
  "shared_root": "C:/Users/x/.astrbot/data/workspaces/astral",
  "baseline": {"astrbot": "4.29.4", "commit": "831abae85"},
  "runtime": "local",
  "consumers": {
    "resolve_workspace_root_for_umo": ["astrbot.core.astr_main_agent", "astrbot.core.tools.computer_tools.util"],
    "resolve_project_workspace_root": ["astrbot.dashboard.services.chatui_project_service"],
    "default_workspace_root": ["astrbot.core.workspace"],
    "project_workspace_root": ["astrbot.core.workspace"]
  },
  "disk": {"exists": true, "files": 12, "size_mb": 3.4},
  "quota": {"limit_gb": 0, "warn": false},
  "reason": null
}
```

响应 503（停用/失败）：

```json
{"installed": false, "reason": "patch not installed: missing resolve_project_workspace_root", "shared_root": "..."}
```

约定：`consumers` 为空数组表示"该函数没有任何模块在消费"——正常情况不应出现，出现即说明补丁失效（`/astral status` 需告警）。

### 6.2 其它接口

| 方法 | 路由 | 归属 | 说明 |
| --- | --- | --- | --- |
| GET | `.../state` | 001 | 见上 |
| POST | `.../migrate` | 001（P2） | 历史目录复制迁移 |
| GET | `.../files` | 002 | 只读预览记忆文件 |
| POST | `.../clean` | 001（P2） | 归档清理（等价 `/astral clean`） |

## 7. 日志与可观测

- 安装成功：`astral: shared workspace installed root=%s rebound=%d`（INFO）
- 根变更：`astral: shared root changed %s -> %s`（INFO）
- 卸载：`astral: patch uninstalled restored=%d`（INFO）
- 安装失败：`astral: install failed reason=%s`（ERROR）
- 黑名单命中：`astral: shared_root rejected (forbidden): %s`（ERROR）
- 每条失败日志必须包含**可操作原因**（缺哪个函数/命中哪条规则/哪个路径不可写）。

## 8. 源码坐标表（基线 `831abae85` / 4.29.4，2026-11-01 核对）

> 来源：`../_investigation/verify/coordinate-verification.md`。升级时必须重跑 §9 并更新本表。
> 路径约定：本节及引用本表的文档中，`astrbot/…`、`dashboard/…`、`docs/zh/…`、`core/…`、`tests/…`、`fs.py:…` 等源码路径**一律指 `../AstrBot-code/` 下的同名文件**（不是本仓库文件）；本仓库自身路径见 `specs/README.md` §0。

### 8.1 解析链（`astrbot/core/workspace.py`，共 217 行）

| 主题 | 位置 |
| --- | --- |
| `normalize_umo_for_workspace` | 22-32 |
| `normalize_project_workspace_type` | 35-47 |
| `normalize_workspace_path` | 50-62 |
| `default_workspace_root` | 65-76 |
| `project_workspace_root` | 79-91 |
| `workspace_path_to_root` | 94-118 |
| `resolve_project_workspace_root` | 121-165（`fallback_umo` 为仅关键字参数） |
| `parse_webchat_umo` | 168-188（非 webchat 返回 None → 走入会话目录分支） |
| `resolve_workspace_root_for_umo` | 191-217（多行签名；`db is None` 时回退 `db_helper`，202-212） |

### 8.2 消费者（全部为顶层 `from ... import`）

| 文件 | 位置 | 导入内容 |
| --- | --- | --- |
| `astrbot/core/astr_main_agent.py` | 141-144 | `normalize_umo_for_workspace`、`resolve_workspace_root_for_umo` |
| `astrbot/core/tools/computer_tools/util.py` | 11-14 | `resolve_workspace_root_for_umo` 等 |
| `astrbot/dashboard/services/chatui_project_service.py` | 8-17 | 8 个名字（含 `resolve_project_workspace_root`、`workspace_path_to_root`） |
| `astrbot/dashboard/api/auth.py` | 9 | 仅常量 `API_KEY_USERNAME_PREFIX`（与补丁无关） |

### 8.3 Agent 侧消费

| 主题 | 位置 | 说明 |
| --- | --- | --- |
| `_get_workspace_path_for_umo` | `astr_main_agent.py:429-448` | 439-441 有**本地重算**兜底（不受叶子补丁影响） |
| `_apply_workspace_extra_prompt` | 451-486 | 读 `<ws>/EXTRA_PROMPT.md` 拼到 `req.system_prompt` |
| 调用点 | 1085 | 无条件下调用（每请求） |
| 工作区 skills | 626-632 | 仅 `runtime == local` |
| `Current workspace` 提示 | 1970-1983 | 告知模型 cwd |

### 8.4 工具侧消费

| 主题 | 位置 |
| --- | --- |
| `workspace_root_for_context` | `computer_tools/util.py:62-80`（76/80 回落本地重算 `workspace_root`，定义在 48-59） |
| 权限矩阵本体 | `computer_tools/util.py:92-199`（`LocalPermissionPolicy` 24-45；`check_local_file_permission` 202-223） |
| shell cwd | `computer_tools/shell.py:117-122` |
| python cwd | `computer_tools/python.py:160-177`（166 `readable_roots`，177 `cwd`） |
| 文件工具相对路径基准 | `computer_tools/fs.py:377,480,596,867`（四行均为 `workspace_root_for_context(...) if local else None`） |
| fs 的会话目录兜底 | `computer_tools/fs.py:112-120`（`_workspace_root`，仅 `current_workspace_root is None` 时使用） |
| 发文件路径解析 | `computer_tools/message_tools.py:144-186` |

### 8.5 Dashboard 侧

| 主题 | 位置 |
| --- | --- |
| 项目解析三处 | `dashboard/services/chatui_project_service.py:216,374,450` |
| 面板 `fallback_umo` 合成 | 同文件 212-214（恒为 `webchat:FriendMessage:webchat!<creator>!<session>`） |
| 新建/修改项目的路径校验 | 同文件 518（`workspace_path_to_root`）→ 不接管 |
| 工作区只读 3 个 GET | `dashboard/api/chat_projects.py:160-212` |
| 预览限制 | 512 KB / UTF-8 / 拒绝符号链接（同服务内常量与校验） |

### 8.6 数据层

| 主题 | 位置 |
| --- | --- |
| `chatui_projects` 表 | `core/db/po.py:469-506`（`workspace_type` 列默认 `session`） |
| `session_project_relations` 表 | `core/db/po.py:509-529` |
| 项目 CRUD | `core/db/sqlite.py:2446-2628`；抽象声明 `core/db/__init__.py:937-1016` |
| api_key 项目启动清理 | `core/db/sqlite.py:213-223` |
| `workspace_type` 默认值三层 | DB 列 `session`；WebUI 新建对话框 `project`（`ProjectDialog.vue:196,241`）；API 仅 `api_key:*` 默认 `project` |

### 8.7 插件面与生命周期

| 主题 | 位置 |
| --- | --- |
| 插件加载 | `core/star/star_manager.py:1076-1483`（1423-1424 调 `initialize()`） |
| 重载 | 1018-1074（1044/1062 调 `_terminate_plugin`） |
| 停用 | 1905 起；1917 调 `_terminate_plugin`；1965-1966 调 `terminate()` |
| 插件模块清理 | 742-774（`_purge_modules`） |
| `metadata.yaml` 优先级 | 1181-1196（yaml 覆盖 `@register`） |
| 启动时序 | `core/initial_loader.py:26-52`；`core/core_lifecycle.py:161-301`（264 建 PluginManager、267 `reload()`） |
| `OnAstrBotLoadedEvent` | 唯一派发点 `core_lifecycle.py:374`（只在启动） |
| `register_web_api` | `core/star/context.py:774-796`；`registered_web_apis` 类属性在 135 |
| 插件 API 分发 | `dashboard/api/plugins.py:196-272,398-440,1485-1491`（250：路由首段 = 插件名） |
| 插件页扫描 | `dashboard/services/plugin_page_service.py:29-30,474-504` |
| bridge 能力 | `plugin_page_bridge.js:207-285`（无 PUT/DELETE） |
| 事件并发 | `core/event_bus.py:73` |
| 会话锁 | `core/utils/session_lock.py:17,49,55`；使用点 `agent_sub_stages/internal.py:239` |

### 8.8 其它

| 主题 | 位置 |
| --- | --- |
| 工作区 skills 发现 | `core/skills/skill_manager.py:394-469` |
| `converted_files` 命名 | `core/computer/file_read_utils.py:577-595`（`hexdigest()[-6:]`，**后** 6 位） |
| `<data>` 路径 | `core/utils/astrbot_path.py:30-41,86-88` |
| 权限默认值 | `core/config/default.py:22-33`（member: 不执行/不联网/workspace；admin: host） |
| 备份不含工作区 | `../AstrBot-code/docs/zh/use/webui.md:141` |
| 定时任务走同一入口 | `core/cron/manager.py:482-486` |
| `StarTools.get_data_dir` | `core/star/star_tools.py:244` |
| Dashboard `dist` | 源码树**不存在**（`.gitignore:41`，pip 构建时生成）——升级核对时不要误判缺失 |

## 9. 升级回归清单（每次 AstrBot 版本变更必须执行）

1. `git -C ../AstrBot-code log -1` 与 `pyproject.toml` 的 version 是否与 `state.baseline` 一致；不一致则更新基线。
2. 核对 §8.1 的 9 个函数是否存在、签名是否变化（尤其 `resolve_project_workspace_root` 的仅关键字参数）。
3. 核对 §8.2 的 3 个消费者是否**仍为顶层 from-import**；若出现函数内延迟 import，需要新的覆盖手段（E-2）。
4. 核对 §8.3/§8.4 的消费点是否新增；新增点若绕开 `resolve_*`，评估是否落进兜底路径。
5. 检查 `registered_web_apis` 是否已支持清理（若已修复，FR-10 的 503 自检可简化但保留）。
6. 检查 `metadata.yaml` 字段要求（`astrbot_version`）与插件页扫描规则是否变化。
7. 在仓库根（= 插件目录）执行 `pytest tests/`，并在**非沙箱环境**执行 `python ../_investigation/poc/harness/harness.py`（见 tasks T-0），记录断言数。
8. 更新本文 §8 与 `CHANGELOG.md` 的回归结论。

## 10. 安全设计

1. **插件 = 管理员权限**：插件能读写整个数据目录、配置与 DB，README 必须写明。
2. **黑名单 fail-loud**：FR-2；不自动回落（ADR-6）。
3. **只读接口约束**（002 的 `files`）：路径必须 `is_relative_to(shared_root)`、拒绝 `is_symlink()`、单文件 ≤ 512 KB、非 UTF-8 直接 400。
4. **不接管 `workspace_path_to_root`**：保留 Dashboard 自身的路径校验（ADR-4）。
5. **审计**：`.astral/audit.jsonl` 只记录动作摘要，不记录文件内容；不写密钥。
6. **最小权限建议**：README 建议把 `computer_use_local_permissions.filesystem_scope` 保持默认（member=workspace），并提示"共享即跨群可见"。

## 11. 测试策略

| 层级 | 内容 | 环境 |
| --- | --- | --- |
| 单元（pytest） | 桩加载真实 `workspace.py`：负对照（只改模块属性）、正对照（双写）、后导入消费者、install 幂等、uninstall 还原、函数缺失失败、黑名单判定、根解析 4 种形态 | 无需 AstrBot 运行实例 |
| 接口 | `state` 200/503、路由前缀、只读 GET | TestClient 或直接调 handler |
| 手工冒烟 | 三平台 `pwd` 一致、A 群写 B 群读、面板一致、停用/启用、改根重启 | 真实实例 |
| harness | 完整 73 条断言 | 非沙箱环境（T-0） |

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- |
| AstrBot 改名/换实现，补丁静默失效 | 中 | 高 | FR-6 显式失败 + `status().consumers` 为空即告警 + §9 回归 |
| 重载叠加/未还原 | 中 | 中 | 状态挂核心模块 + `__astral_name__` + `terminate` + AC-4.x |
| 停用后端点残留 | 高（平台缺陷） | 低 | FR-10 的 503 自检 |
| 全群共享导致隐私泄露 | 高（设计使然） | 中 | README 披露 + CONSTITUTION P5 + 可选的只读/黑名单 |
| 共享根指向敏感目录 | 低 | 高 | FR-2 黑名单 fail-loud |
| 用户多进程部署 | 低 | 低 | README 声明不支持 |
| 升级后 harness 无法在沙箱跑 | 中 | 中 | T-0 在非沙箱重跑并归档输出 |
| 备份遗漏共享工作区 | 中 | 高 | README 明确"工作区需单独备份" |

## 13. 附录：最小安装骨架（供实现参考，不是最终代码）

```python
# ws_shared.py（骨架）
from __future__ import annotations
import importlib, sys
from pathlib import Path
from typing import Any, Callable

_WS = "astrbot.core.workspace"
_STATE = "__astral_patch_state__"
TOP = ("resolve_workspace_root_for_umo", "resolve_project_workspace_root")
LEAF = ("default_workspace_root", "project_workspace_root")
ALL = (*TOP, *LEAF)

def _ws(): return importlib.import_module(_WS)

def install(root: Path, *, patch_project: bool = True) -> dict:
    ws = _ws()
    state = getattr(ws, _STATE, None)
    if state is None:
        missing = [n for n in ALL if not hasattr(ws, n)]
        if missing:
            raise RuntimeError(f"missing AstrBot workspace symbols: {missing}")
        state = {"originals": {n: getattr(ws, n) for n in ALL}, "wrappers": {}}
        setattr(ws, _STATE, state)
    wrappers = _build_wrappers(root, patch_project)
    state.update(wrappers=wrappers, root=str(root))
    rebound = []
    for name in ALL:
        if name not in wrappers:
            continue
        setattr(ws, name, wrappers[name])                      # ① 源模块
        orig = state["originals"][name]
        for mod in list(sys.modules.values()):                  # ② 已导入调用方
            if mod is ws or mod is None:
                continue
            cur = getattr(mod, name, None)
            if cur is orig or getattr(cur, "__astral_name__", None) == name:
                setattr(mod, name, wrappers[name])
                rebound.append(f"{getattr(mod, '__name__', '?')}.{name}")
    return {"installed": True, "root": str(root), "rebound": sorted(set(rebound))}

def uninstall() -> dict: ...   # 反向操作，见 §3.5
def status() -> dict: ...      # 见 §3.6
```
