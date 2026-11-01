# 开发说明（模块地图、契约与验证记录）

> 规格是唯一权威：`specs/`。本文只记录「代码在哪、怎么测、验收过什么」，供后续维护者与 agent 使用。README 面向用户，本文件面向实现者。

## 1. 模块地图

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `config.py` | `shared_root` 解析（默认 / `~` / 相对 / 绝对）、危险路径黑名单、`ensure_shared_tree()` 建目录骨架并校验可写、配置项归一化 | 不装补丁、不写业务文件 |
| `ws_shared.py` | 补丁核心：4 个解析函数替换、双写消费者、幂等安装、还原、状态（`install` / `uninstall` / `status` / `originals`） | 不读配置、不建目录、不写盘 |
| `store.py` | `.astral/state.json`、`.astral/audit.jsonl`、共享根体积统计与配额、`/astral clean` 的移动归档 | 不关心补丁 |
| `templates.py` | 四份中文模板、原子写、`seed_workspace()`、`sync_managed_section()`、`managed_state()` | 不渲染 Markdown、不读快照 |
| `snapshot.py` | 冻结快照渲染（预算、截断、归档索引、容量提示、七种降级）与进程内 LRU `SnapshotCache` | 不写盘、不联网、不读 DB |
| `state.py` | `state` 接口与 `/astral status` 共用的状态载荷、聊天文案、`/astral memory show` 渲染 | 不改变状态 |
| `webapi.py` | 三个 HTTP 端点：`GET .../state`、`GET .../files`、`POST .../clean`；未安装一律 503 | 不做鉴权（沿用 Dashboard） |
| `lifecycle.py` | 生命周期编排：`install_patch` / `fail`（fail-loud + 回滚）/ `seed` / `terminate` / `reapply` / `refresh_state_file` | 不处理聊天指令 |
| `main.py` | Star 子类（241 行）：装配、访问器、`initialize`/`terminate`/`on_astrbot_loaded`、`on_llm_request` 注入、`/astral` 指令组 | 不直接操作 `sys.modules` |

数据流：

```text
插件加载  __init__  : lifecycle.install_patch → (config.get_shared_root → ensure_shared_tree → ws_shared.install) → webapi 注册
插件加载  initialize: templates.seed_workspace → templates.sync_managed_section → audit/state.json
每轮对话  on_llm_request: (umo, cid) 查 SnapshotCache；未命中 → snapshot.render → 追加到 req.system_prompt
插件停用  terminate : ws_shared.uninstall → 清缓存 → audit
```

## 2. 对外契约（改动时必须同步测试）

```python
# config.py
resolve_shared_root(raw, *, data_path=None) -> Path
check_forbidden(root, extra=None, *, data_path=None) -> str | None      # 命中返回含 "forbidden" 的原因
get_shared_root(cfg, *, data_path=None) -> Path                        # 失败抛 ConfigError(reason)
ensure_shared_tree(root) -> list[str]                                  # 失败抛 ConfigError
read_options(cfg) -> dict; memory_options(cfg) -> dict

# ws_shared.py
install(root, *, patch_project=True, scan_modules=True) -> dict
uninstall() -> dict
status() -> dict          # installed/shared_root(raw root)/consumers/baseline/installed_at/patched/patch_project/reason
originals() -> dict       # 原始函数对象，仅自检与测试使用

# store.py
write_state/read_state/append_audit/read_audit/dir_stats/quota_state/archive_move

# templates.py
seed_workspace(root) -> list[str]
sync_managed_section(root) -> "updated|unchanged|appended|created|broken|missing_root"
managed_state(root) -> 同上（只读）

# snapshot.py
render(root, *, soul_budget=4000, memory_budget=8000, soft_limit=20000, cid=None, now=None) -> str
SnapshotCache(maxsize=64).get_or_render(key, render_fn) -> (block, hit)
```

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
| `test_config.py` / `test_config_regression.py` | 共享根 4 种形态、黑名单（含「AstrBot 源码目录内、但位于 `<data>` 下的默认根必须放行」这条回归）、目录骨架幂等、配置项回落 |
| `test_ws_shared.py` | 双写、负对照（`scan_modules=False`）、幂等、还原、缺函数显式失败 |
| `test_store.py` | state.json、audit JSONL、配额告警、移动归档 |
| `test_templates.py` | 四份模板与 plan §5 逐字一致、播种不覆盖、原子写、托管区段四种情形 |
| `test_snapshot.py` | 渲染格式、截断/归档索引/容量提示、七种降级、缓存命中 0 次读盘 |
| `test_main_hooks.py` | 插件装配、state 载荷、快照注入与冻结、files 接口、状态接口 200/503、指令文案 |
| `test_plugin_load.py` | 以 `data.plugins.astrbot_plugin_astral.main` 包路径导入并检查指令/钩子注册 |
| `test_coordinates.py` | 4 个函数签名、3 个消费者仍为顶层 import、桩版本漂移检测 |
| `test_views_contract.py` | 只读页静态契约与 README/CHANGELOG 必含内容 |

当前基线：`pytest tests -q` → 151 passed / 2 skipped（跳过项是 Windows 无 symlink 权限的两个符号链接用例）。

## 4. 真实实例验收（AstrBot 4.29.4 @ `831abae85`）

在本地实例上重载插件后实测：

- `state` 200：`installed=true`、`shared_root` 为默认 `<data>/workspaces/astral`、`consumers.resolve_workspace_root_for_umo` 含 `astrbot.core.astr_main_agent` 与 `astrbot.core.tools.computer_tools.util`、`consumers.resolve_project_workspace_root` 含 `astrbot.dashboard.services.chatui_project_service`。
- 四份文件播种成功（`seeded_version=1`），`.astral/state.json` 与 `.astral/audit.jsonl` 生成，目录骨架齐全。
- `files` 200；`?name=SOUL` 200 且正文与磁盘一致；`?name=../../etc/passwd` → 400 `unknown_name`。
- 停用插件 → `state`、`files`、`clean` 全部 503（`installed=false`、`consumers` 为空、`message`/`reason` 可读）；重新启用 → 200 且 `installed=true`。这一步同时验证了「停用即还原」与「停用后端点自检」。
- 插件页：`/api/v1/plugins/astrbot_plugin_astral/views/astral` 的 `content_path` 返回 200（约 27 KB），含 bridge 注入与「全局共享」横幅，无 `innerHTML`/`<textarea>`/`contenteditable`。
- 首次部署时把 `shared_root` 指到实例源码目录会导致安装失败并给出 `forbidden` 原因（fail-loud 生效）；该测试同时暴露并修复了「默认根位于 AstrBot 安装目录内被误判」的问题。

**harness（T-0 / G-8）**：在非沙箱环境完整运行 `../_investigation/poc/harness/harness.py` → **73 passed / 0 failed**（2026-11-01），输出存于 `../_investigation/verify/harness-2026-11-01.txt`。

**尚未自动化的验收**（需要真实多平台会话或人工观察）：三平台 `pwd` 一致（AC-3.3）、Dashboard 面板路径（AC-9.1/9.2）、persona 与边界冲突（AC-1.2）、让 bot 写记忆与去标识化（AC-4.2/AC-7.2）、备份恢复与迁移（AC-8.1/8.2）。

## 5. 已知限制与后续

- 补丁依赖 AstrBot 4.29.x 的解析入口；升级后按 `specs/001-shared-workspace/plan.md` §9 回归，漂移时 `state.reason` 会显示缺失函数名。
- 叶子兜底（`default_workspace_root` / `project_workspace_root`）不覆盖三处「本地重算」路径（001 ADR-3 / E-1）。
- 快照只在 `/new` 后刷新；`/reset` 不换 cid。
- `views/astral/_page.json` 在 4.29.4 不被页面服务消费（服务只扫 `index.html`），保留为元数据。