# AGENTS.md — astrbot_plugin_astral

本目录是 AstrBot 插件 **`astrbot_plugin_astral`**（展示名 Astral）的仓库根，也是写代码时本工作区的根。

## 先读什么

- 规格（唯一权威）：`specs/README.md` → `specs/CONSTITUTION.md` → `specs/001-shared-workspace/` → `specs/002-soul-and-memory/`
- 原设计稿（已归档；第 6 章记忆方案作废）：`docs/archive/astral-workspace-design.md`
- 实现已完成（v0.1.0）：模块地图、契约与验收记录见 `docs/dev-notes.md`，验收证据见 `CHANGELOG.md`

## 路径约定（重要）

本工作区根 = 插件仓库根。兄弟目录要靠 `../` 才能到达：

| 引用 | 相对本工作区 | 绝对路径 |
| --- | --- | --- |
| AstrBot 源码（4.29.4 @ `831abae85`） | `../AstrBot-code/` | `C:\Users\Chake\projects\AstrBot\AstrBot-code\` |
| 调研与 PoC | `../_investigation/` | `C:\Users\Chake\projects\AstrBot\_investigation\` |
| 坐标核对报告 | `../_investigation/verify/coordinate-verification.md` | 同上 |
| 验证 harness | `../_investigation/poc/harness/harness.py` | 同上 |

四条规则：

1. **源码坐标里的简写**（`astrbot/…`、`dashboard/…`、`docs/zh/…`、`core/…`、`fs.py:…`）一律指 `../AstrBot-code/` 下的同名文件；本仓库自己的 `tests/`、`views/`、`specs/`、`docs/` 才指本地目录。
2. 文档里的 `<data>`、`<shared_root>` 是 AstrBot 运行期目录，不是仓库路径。
3. `../` 只在"工作区 = `C:\Users\Chake\projects\AstrBot\astrbot_plugin_astral`（符号链接入口）且本地同时检出 AstrBot-code"时成立；若 DSH 按实例真实路径（`C:\Users\Chake\.astrbot_launcher\instances\...\core\data\plugins\astrbot_plugin_astral`）打开工作区，`../` 会指向 `plugins` 目录，此时请改用上表的绝对路径。插件单独分发时这些路径同样不存在，规格内容以正文为准。
4. 完整约定见 `specs/README.md` §0。

## 当前状态

- 规格：已定稿（草案 v0.1）；实现：v0.1.0 已落地（001 M0–M4 + 002 M0–M5）。
- 模块地图（`docs/dev-notes.md` 有完整契约）：`config.py`（共享根解析 + 黑名单）、`ws_shared.py`（补丁核心：双写/还原/状态）、`store.py`（`.astral/` 元数据、审计、配额、归档）、`templates.py`（四份模板 + 播种 + 托管区段）、`snapshot.py`（冻结快照渲染 + LRU）、`state.py`（状态载荷与聊天文案）、`webapi.py`（`state`/`files`/`clean` 三个端点）、`lifecycle.py`（安装/失败/播种/还原编排）、`main.py`（Star 子类：指令与钩子，241 行）。
- 测试：`pytest tests -q`（151 passed / 2 skipped，Windows 无 symlink 权限）。运行方式见 `tests/README.md`；必须设置 `ASTRBOT_ROOT` 到临时目录。
- 已完成：harness 73 条断言在非沙箱环境跑通（73 passed / 0 failed，输出见 `../_investigation/verify/harness-2026-11-01.txt`）。
- 未决：三平台 `pwd` 一致（AC-3.3）、Dashboard 面板路径（AC-9.x）等需要真实多平台会话的手工冒烟。