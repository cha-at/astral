# AGENTS.md — astrbot_plugin_astral

本目录是 AstrBot 插件 **`astrbot_plugin_astral`**（展示名「Astral 记忆」）的仓库根。

## 先读什么

- 用户文档：README.md
- 实现说明（模块地图、对外契约、测试与升级回归）：docs/dev-notes.md
- 变更记录：CHANGELOG.md

设计规格与调研材料是维护者本地资料，不随仓库发布。

## 路径约定

- 本仓库只包含插件代码、测试、页面与文档；AstrBot 源码不随仓库发布。
- 源码坐标里的 `astrbot/…`、`dashboard/…`、`core/…`、`fs.py:…` 等简写，指 AstrBot 4.29.4（提交 `831abae85`）源码树中的同名文件。
- 坐标用例需要本地检出 AstrBot 源码时，用环境变量 `ASTRBOT_SOURCE` 指向检出目录；没有就跳过。
- 文档里的 `<data>`、`<shared_root>` 是 AstrBot 运行期目录，不是仓库路径。

## 当前状态

- v1.0.0 已实现：共享工作区补丁 + SOUL/MEMORY 文件式记忆；v1.0.0 重点是首启文案、注入块与插件页（记忆窗口），记忆写入仍依赖 AstrBot 的 Computer Use 运行时。
- 审核修复：默认共享根 = `StarTools.get_data_dir("astrbot_plugin_astral")`（`<data>/plugin_data/astrbot_plugin_astral`）；根目录模块日志统一 `from astrbot.api import logger`，禁止 `logging.getLogger`。旧默认 `<data>/workspaces/astral` 不自动迁移。
- 未发布：记忆版本控制（`versioning.py` + `commands.py`；`/astral commit`、`/astral save`、`/astral recover`）。插件加载时在共享根初始化 `.git`、只跟踪四份内容文件、回退为追加式提交；配置键 `version_control.enabled`（默认 true）与 `version_control.git_path`。插件版本仍为 1.0.0，变更记在 CHANGELOG「未发布」。
- 模块地图：见 docs/dev-notes.md（config / ws_shared / store / versioning / templates / snapshot / state / commands / webapi / lifecycle / main）。
- 测试：`pytest tests -q` → 212 passed / 3 skipped；运行方式见 tests/README.md，必须把 `ASTRBOT_ROOT` 指向临时目录。
- 注入契约：`snapshot.py` 渲染 `# 关于你自己`，剥掉 HTML 注释，不带会话 id / 字符数 / 文件名标题；改它要同步 `tests/test_snapshot.py` 与 docs/dev-notes.md。
- 未决：三平台 cwd 一致、WebUI 面板路径、备份恢复、Git 历史删除、多平台 Git 路径/权限，以及默认数据目录改为 `plugin_data` 后的真实实例重载复核等需要人工验收。

## 提交约定

- 不要提交 `specs/`（.gitignore 已忽略）、运行期产物或本机绝对路径。
- 改补丁相关代码时同步更新 `tests/test_ws_shared.py` 与 `tests/test_coordinates.py`。
