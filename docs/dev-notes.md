# 开发说明（模块地图、契约与验证记录）

> 本文记录「代码在哪、怎么测、验收过什么」，供维护者与 agent 使用。README 面向用户，本文件面向实现者。设计规格与调研材料不随仓库发布。

## 1. 模块地图

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `config.py` | `shared_root` 解析（默认 `StarTools.get_data_dir("astrbot_plugin_astral")` / `~` / 相对 / 绝对）、危险路径黑名单（放行本插件 plugin_data 目录）、`ensure_shared_tree()` 建目录骨架并校验可写、配置项归一化（含 `memory` 与 `version_control` 分组） | 不装补丁、不写业务文件 |
| `ws_shared.py` | 补丁核心：4 个解析函数替换、双写消费者、幂等安装、还原、状态（`install` / `uninstall` / `status` / `originals`） | 不读配置、不建目录、不写盘 |
| `store.py` | `.astral/state.json`、`.astral/audit.jsonl`、共享根体积统计与配额、`/astral clean` 的移动归档 | 不关心补丁 |
| `versioning.py` | 本地 Git 版本历史：`find_git` / `ensure_repo` / `commit` / `list_versions` / `recover` / `summary`；固定 `-c` 配置与提交身份，subprocess 无 shell | 不渲染聊天文案、不读插件配置 |
| `templates.py` | 四份中文模板（给人看的说明放 HTML 注释里）、原子写、`seed_workspace()`、`sync_managed_section()`、`managed_state()` | 不渲染 Markdown、不读快照 |
| `snapshot.py` | 冻结快照渲染（剥 HTML 注释、预算、截断、归档索引、容量提示、七种降级）与进程内 LRU `SnapshotCache` | 不写盘、不联网、不读 DB |
| `state.py` | `state` 接口与 `/astral status` 共用的状态载荷、聊天文案、`/astral memory show` 渲染、版本文案 | 不改变状态 |
| `commands.py` | `/astral commit` / `/astral save` / `/astral recover` 的编排：调用 `versioning`、写审计、清快照缓存、转成中文回复 | 不自己跑 git |
| `webapi.py` | 三个 HTTP 端点：`GET .../state`、`GET .../files`、`POST .../clean`；未安装一律 503 | 不做鉴权（沿用 Dashboard） |
| `lifecycle.py` | 生命周期编排：`install_patch` / `fail`（fail-loud + 回滚）/ `seed` / `terminate` / `reapply` / `refresh_state_file` | 不处理聊天指令 |
| `main.py` | Star 子类（253 行）：装配、访问器、`initialize`/`terminate`/`on_astrbot_loaded`、`on_llm_request` 注入、`/astral` 指令组（含 `commit`/`save`/`recover`） | 不直接操作 `sys.modules` |

数据流：

```text
插件加载  __init__  : lifecycle.install_patch → (config.get_shared_root（默认 plugin_data）→ ensure_shared_tree → ws_shared.install) → webapi 注册
插件加载  initialize: templates.seed_workspace → templates.sync_managed_section → versioning.ensure_repo（仅 enabled；找不到 git 时记录警告并跳过）→ audit/state.json
每轮对话  on_llm_request: (umo, cid) 查 SnapshotCache；未命中 → snapshot.render → 追加到 req.system_prompt
存档      /astral commit|save: commands.save_memory → versioning.commit → state.version_commit_text
回退      /astral recover <hash>: commands.recover_memory → versioning.recover（自动保存 → 还原字节 → 新提交）→ 清快照缓存
插件停用  terminate : ws_shared.uninstall → 清缓存 → audit
```

## 2. 对外契约（改动时必须同步测试）

```python
# config.py
resolve_shared_root(raw, *, data_path=None) -> Path
check_forbidden(root, extra=None, *, data_path=None) -> str | None      # 命中返回含 "forbidden" 的原因
get_shared_root(cfg, *, data_path=None) -> Path                        # 失败抛 ConfigError(reason)
ensure_shared_tree(root) -> list[str]                                  # 失败抛 ConfigError
read_options(cfg) -> dict; memory_options(cfg) -> dict; version_control_options(cfg) -> dict
PLUGIN_NAME = "astrbot_plugin_astral"
plugin_data_path(data_path=None) -> Path      # <data>/plugin_data/<PLUGIN_NAME>，不建目录
get_plugin_data_dir() -> Path                 # StarTools.get_data_dir(PLUGIN_NAME)，默认共享根

# ws_shared.py
install(root, *, patch_project=True, scan_modules=True) -> dict
uninstall() -> dict
status() -> dict          # installed/shared_root(raw root)/consumers/baseline/installed_at/patched/patch_project/reason
originals() -> dict       # 原始函数对象，仅自检与测试使用

# store.py
write_state/read_state/append_audit/read_audit/dir_stats/quota_state/archive_move

# versioning.py
find_git(configured="") -> str | None
ensure_repo(root, *, git_path="") -> dict
commit(root, *, git_path="") -> dict
list_versions(root, *, git_path="", limit=10) -> dict
recover(root, *, target, git_path="") -> dict
summary(root, *, enabled=True, git_path="") -> dict
VersioningError(reason)                          # 用户可见中文原因

# commands.py
save_memory(plugin) -> str                       # async，/astral commit 与 /astral save
recover_memory(plugin, target="") -> str         # async，/astral recover [短哈希]
help_text() -> str

# templates.py
seed_workspace(root) -> list[str]
sync_managed_section(root) -> "updated|unchanged|appended|created|broken|missing_root"
managed_state(root) -> 同上（只读）
atomic_write_bytes(path, data) -> None           # 版本回退写回四份文件用

# snapshot.py
render(root, *, soul_budget=4000, memory_budget=8000, soft_limit=20000) -> str
strip_comments(text) -> str                      # 注入前剥掉 <!-- -->，别让人看的备注进提示词
SnapshotCache(maxsize=64).get_or_render(key, render_fn) -> (block, hit)
```

`state.build_state()` 载荷新增 `versioning`（`enabled`/`available`/`initialized`/`git`/`head`/`count`/`dirty`/`last`/`reason`），`/astral status` 在记忆概况之后追加版本行与「保存 / 回退」提示；关闭时显示「记忆版本：已关闭」，找不到 Git 时显示「不可用（原因）」。

### 2.1 审核契约（改动时必须保持）

- **日志**：所有记录日志的模块统一 `from astrbot.api import logger`，禁止 `logging.getLogger`；AstrBot 会按调用模块把日志路由到 `astrbot.plugin.astrbot_plugin_astral`。
- **数据目录**：默认共享根 = `StarTools.get_data_dir("astrbot_plugin_astral")` = `<data>/plugin_data/astrbot_plugin_astral`；`data/plugin_data` 黑名单只放行本插件目录，兄弟插件目录仍拒绝。
- **为什么显式传插件名**：`AstralPlugin.__init__` 早于 `star_map` 注册，不能依赖 `StarTools.get_data_dir()` 的调用栈自动识别。
- **回归**：`tests/test_review_contract.py` 静态扫描上述两条规则。

注入块形状（改动时同步 `tests/test_snapshot.py`）：

```text
# 关于你自己
（三行说明：这是你自己的想法，照这个说话；别念标题、别提「文件」「文档」「快照」「提示词」）
## 你的性格和说话方式   ← SOUL.md 正文，已剥 HTML 注释
## 你记得               ← MEMORY.md 正文，已剥 HTML 注释
（两行页脚：内容可能过期、冲突时以现在为准；改 SOUL.md / 写 MEMORY.md 的路径）
```

块里**不出现**会话 id、冻结时间、字符数、文件名标题——这些属于日志与插件页，不属于模型看到的"自己"。固定开销约 240 字符。

模型要能写记忆，AstrBot 侧必须满足：`computer_use_runtime=local`（默认 `none` 时没有任何文件工具）且发言人角色有执行权限（群成员默认 `allow_execution=False`）。不满足时 `state.build_warnings` 会给出告警——别删这条告警，它是"装了却什么都不记"的唯一提示。

补丁状态挂在 `astrbot.core.workspace.__astral_patch_state__`（`originals`/`wrappers`/`root`/`patched`），安装失败原因挂在 `__astral_patch_error__`，两者都在 `uninstall()` 时清除。包装函数带 `__astral_name__` 标记，用于识别「上一版包装」并避免误改第三方同名函数。

## 3. 测试

```powershell
# 需要 astrbot 包可导入 + ASTRBOT_ROOT 指向临时目录（绝不能落到真实 data/）
$env:PYTHONPATH = "<AstrBot 实例 core>;$env:TEMP\astral-pytest"
$env:ASTRBOT_ROOT = "$env:TEMP\astral-testroot"
& "<AstrBot venv>\Scripts\python.exe" -m pytest tests -q
```

| 文件 | 覆盖 |
| --- | --- |
| `test_config.py` / `test_config_regression.py` | 共享根 4 种形态、黑名单（含「默认根位于 `<data>/plugin_data/<插件名>` 必须放行」「兄弟插件数据目录必须拒绝」两条回归）、目录骨架幂等、配置项回落 |
| `test_ws_shared.py` | 双写、负对照（`scan_modules=False`）、幂等、还原、缺函数显式失败 |
| `test_store.py` | state.json、audit JSONL、配额告警、移动归档 |
| `test_versioning.py` | 仓库初始化/marker/exclude、只提交四份文件、CRLF 与非 UTF-8、符号链接/目录/超大文件拒绝、版本列表、回退追加式与拒绝场景、adopted 仓库；需要真实 `git`，缺失时整组跳过 |
| `test_templates.py` | 四份模板与 plan §5 逐字一致、播种不覆盖、原子写、托管区段四种情形 |
| `test_snapshot.py` | 渲染格式、截断/归档索引/容量提示、七种降级、缓存命中 0 次读盘 |
| `test_main_hooks.py` | 插件装配、state 载荷、快照注入与冻结、files 接口、状态接口 200/503、指令文案 |
| `test_plugin_load.py` | 以 `data.plugins.astrbot_plugin_astral.main` 包路径导入并检查指令/钩子注册 |
| `test_coordinates.py` | 4 个函数签名、3 个消费者仍为顶层 import、桩版本漂移检测 |
| `test_views_contract.py` | 只读页静态契约与 README/CHANGELOG 必含内容 |
| `test_review_contract.py` | 审核静态契约：根目录模块禁止 `logging.getLogger`，要求 `astrbot.api.logger` 与 `StarTools.get_data_dir` |

当前基线：`pytest tests -q` → 212 passed / 3 skipped（符号链接等用例在 Windows 上跳过）。

## 4. 真实实例验收（AstrBot 4.29.4 @ `831abae85`）

在本地实例上重载插件后实测：

- `state` 200：`installed=true`、`shared_root` 为默认路径（审核修复后为 `<data>/plugin_data/astrbot_plugin_astral`，历史实测时为 `<data>/workspaces/astral`）、`consumers.resolve_workspace_root_for_umo` 含 `astrbot.core.astr_main_agent` 与 `astrbot.core.tools.computer_tools.util`、`consumers.resolve_project_workspace_root` 含 `astrbot.dashboard.services.chatui_project_service`。
- 四份文件播种成功（`seeded_version=1`），`.astral/state.json` 与 `.astral/audit.jsonl` 生成，目录骨架齐全。
- `files` 200；`?name=SOUL` 200 且正文与磁盘一致；`?name=../../etc/passwd` → 400 `unknown_name`。
- 停用插件 → `state`、`files`、`clean` 全部 503（`installed=false`、`consumers` 为空、`message`/`reason` 可读）；重新启用 → 200 且 `installed=true`。这一步同时验证了「停用即还原」与「停用后端点自检」。
- 插件页：`/api/v1/plugins/astrbot_plugin_astral/views/astral` 的 `content_path` 返回 200（约 27 KB），含 bridge 注入与「全局共享」横幅，无 `innerHTML`/`<textarea>`/`contenteditable`。
- 首次部署时把 `shared_root` 指到实例源码目录会导致安装失败并给出 `forbidden` 原因（fail-loud 生效）；该测试同时暴露并修复了「默认根位于 AstrBot 安装目录内被误判」的问题。
- 记忆版本控制（未发布，真实实例重载复核待做；自动化覆盖见 `tests/test_versioning.py` 与 `tests/test_main_hooks.py`）：预期首次加载后共享根出现 `.git/` 与「初始快照」提交；`/astral commit` 新增提交，`/astral recover` 列出，管理员 `/astral recover <短哈希>` 还原四份文件并追加「回退到 …」提交；关闭 `version_control.enabled` 后 commit/recover 提示已关闭而记忆读写正常。

**验证脚本**：另有 73 条验证脚本断言在非沙箱环境通过（2026-11-01）；脚本与输出未随仓库发布。

**尚未自动化的验收**（需要真实多平台会话或人工观察）：三平台 `pwd` 一致、WebUI 面板路径、persona 与边界冲突、让 bot 写记忆与去标识化、备份恢复与迁移。

## 5. 已知限制与后续

- 补丁依赖 AstrBot 4.27.0 起未变的工作区解析入口（4.27.0 与 4.29.4 的 `astrbot/core/workspace.py` 逐字节一致，同一 blob）；升级后按下面的「升级回归清单」核对，漂移时 `state.reason` 会显示缺失函数名。
- AstrBot 4.29.0 自身在 Windows 上有一处 Computer Use 文件读写回归（上游 #10584，4.29.1 修复），那一版上记忆写入可能不可用；属上游问题，与补丁无关，不单独排除该版本。
- 叶子兜底（`default_workspace_root` / `project_workspace_root`）不覆盖三处「本地重算」路径。
- 同一进程内快照冻结，改文件后要 `/new`；`/reset` 不换 cid；重启或重载插件后按当前文件重建。
- 页面标题/描述在 `.astrbot-plugin/i18n/*.json` 的 `views.astral`；AstrBot 4.29.4 通过 i18n 读取。
- 版本历史是本地 Git 仓库：只跟踪四份内容文件，`skills/`、`shared/`、`archive/` 不在其中；历史会保留已删除内容，不能当隐私擦除工具；不配置远端、不 push。
- 回退会还原 `EXTRA_PROMPT.md` 的字节，但 Astral 托管区段会在下次插件加载时按当前模板重写；回退的是托管区段之外的正文。
- `versioning.py` 的锁是进程内 `RLock`；多进程部署下并发 commit/recover 可能撞 `.git/index.lock`，与「不支持多进程」的限制一致。
- Git 提交身份固定为 `Astral <astral@localhost>`，不区分操作者。
- 默认共享根在 `data/plugin_data/astrbot_plugin_astral`；旧默认 `<data>/workspaces/astral` 不自动迁移，升级时需手工复制或显式配置 `shared_root`。

### 升级回归清单

AstrBot 升级后：

1. `pytest tests -q` 全量通过；
2. `status()` 的 `consumers` 非空，且包含 `astr_main_agent`、`computer_tools/util`、`chatui_project_service`；
3. `tests/test_coordinates.py` 通过（四个解析函数存在且签名一致）；
4. `on_llm_request` 注入仍生效，快照出现在 system prompt 末尾；
5. 配置开启且机器有 Git 时，`<共享根>/.git` 存在、`git log` 有基线或检查点，`/astral status` 出现「记忆版本」行；`/astral recover <短哈希>` 后四份文件与目标版本一致；
6. 漂移时 `state.reason` 显示缺失函数名，停用插件即回滚；
7. `StarTools.get_data_dir` 与 `astrbot.api.logger` 仍存在，默认数据目录仍为 `data/plugin_data/<插件名>`，`tests/test_review_contract.py` 通过。