# AGENTS.md — astrbot_plugin_astral

本目录是 AstrBot 插件 **`astrbot_plugin_astral`**（展示名 Astral）的仓库根。

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

- v0.1.0 已实现：共享工作区补丁 + SOUL/MEMORY 文件式记忆。
- 模块地图：见 docs/dev-notes.md（config / ws_shared / store / templates / snapshot / state / webapi / lifecycle / main）。
- 测试：`pytest tests -q` → 152 passed / 2 skipped；运行方式见 tests/README.md，必须把 `ASTRBOT_ROOT` 指向临时目录。
- 未决：三平台 cwd 一致、WebUI 面板路径、备份恢复等需要真实多平台会话的人工验收。

## 提交约定

- 不要提交 `specs/`（.gitignore 已忽略）、运行期产物或本机绝对路径。
- 改补丁相关代码时同步更新 `tests/test_ws_shared.py` 与 `tests/test_coordinates.py`。
