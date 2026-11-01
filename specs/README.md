# Astral 规格集（specs）

> 状态：草案 v0.1 · 最后更新：2026-11-01 · 维护者：Lead（实现由另开的默认模式会话负责）
> 本目录是 Astral 插件的**唯一权威规格**。`docs/archive/astral-workspace-design.md`（原设计稿）与 `../_investigation/`（调研与 PoC）只作为来源材料；与规格冲突时**以本目录为准**。

## 0. 路径约定（先读）

**本仓库根 = 插件目录 `astrbot_plugin_astral/`**，也是将来写代码时 DSH 工作区的根；项目根是它的上一级（`../`）。文档中的路径一律按下面这张表写：

| 引用对象 | 写法 | 示例 |
| --- | --- | --- |
| 同目录文件 | 裸文件名 | `plan.md` §8、`spec.md` FR-3 |
| 插件仓库内其它文件 | 仓库根相对，以 `specs/` 开头 | `specs/001-shared-workspace/plan.md` §8 |
| AstrBot 源码 | `../AstrBot-code/<子路径>` | `../AstrBot-code/astrbot/core/workspace.py:191` |
| 调研与 PoC 材料 | `../_investigation/<子路径>` | `../_investigation/verify/coordinate-verification.md` |
| AstrBot 运行期目录 | 尖括号占位，**不是仓库路径** | `<data>/workspaces/astral`、`<shared_root>/MEMORY.md` |
| Markdown 超链接 | 文件相对（保证可点击） | `[plan.md](001-shared-workspace/plan.md)` |

两条补充规则：

1. 源码坐标里的简写 —— `workspace.py:191`、`dashboard/api/plugins.py:250`、`docs/zh/use/webui.md:141`、`core/star/base.py:141` —— **一律指 `../AstrBot-code/` 下的同名文件**，不是本仓库文件。
2. `../` 只在"本地同时检出 AstrBot-code 与插件、且工作区入口是项目根下的 `astrbot_plugin_astral` 符号链接"时成立；对应绝对路径为 `C:\Users\Chake\projects\AstrBot\AstrBot-code\` 与 `C:\Users\Chake\projects\AstrBot\_investigation\`。若按实例真实路径打开工作区（`...\.astrbot_launcher\instances\...\plugins\astrbot_plugin_astral`），`../` 会指向 `plugins` 目录，此时请直接使用绝对路径；插件单独分发时这些路径不存在，规格内容仍以正文为准。

---

## 1. 规格索引

| 编号 | 名称 | 状态 | 一句话目标 | 依赖 |
| --- | --- | --- | --- | --- |
| — | [CONSTITUTION.md](CONSTITUTION.md) | 生效 | 项目原则与质量门禁，长期有效 | — |
| 001 | [001-shared-workspace/spec.md](001-shared-workspace/spec.md) | 草案 v0.1 | 所有平台/所有群/所有私聊共享同一个工作区根目录，且可观测、可回滚 | — |
| 001 | [001-shared-workspace/plan.md](001-shared-workspace/plan.md) | 草案 v0.1 | 补丁实现、配置、目录骨架、接口契约、源码坐标与升级回归 | — |
| 001 | [001-shared-workspace/tasks.md](001-shared-workspace/tasks.md) | 草案 v0.1 | M0–M4 里程碑与任务拆分 | — |
| 001 | [001-shared-workspace/decisions.md](001-shared-workspace/decisions.md) | 草案 v0.1 | 10 条 ADR 与未决问题 | — |
| 002 | [002-soul-and-memory/spec.md](002-soul-and-memory/spec.md) | 草案 v0.1 | SOUL.md / MEMORY.md 文件式记忆，对话级冻结快照注入 | 001 |
| 002 | [002-soul-and-memory/plan.md](002-soul-and-memory/plan.md) | 草案 v0.1 | 快照渲染与注入、默认模板、只读页、测试策略 | 001 |
| 002 | [002-soul-and-memory/tasks.md](002-soul-and-memory/tasks.md) | 草案 v0.1 | M0–M5 里程碑与任务拆分 | 001 |
| 002 | [002-soul-and-memory/decisions.md](002-soul-and-memory/decisions.md) | 草案 v0.1 | 否决 SQLite/embedding 等 7 条 ADR 与未决问题 | 001 |

## 2. 阅读顺序

1. **CONSTITUTION.md** —— 先看原则与门禁，后面所有取舍都据此判断。
2. **001-shared-workspace/spec.md → plan.md → tasks.md** —— 共享工作区是全部能力的地基。
3. **002-soul-and-memory/spec.md → plan.md → tasks.md** —— 记忆与人格文件建立在地基之上。
4. 需要查"为什么这么定"时看各包的 `decisions.md`；需要查实现细节时看 `plan.md`。

## 3. 文档地图（避免重复定义）

| 主题 | 唯一权威位置 | 其它文档只能说 |
| --- | --- | --- |
| 共享根的解析、校验、黑名单 | `specs/001-shared-workspace/spec.md` FR-1、FR-2 | "引用 001" |
| 补丁的 4 个被替换函数与双写算法 | `specs/001-shared-workspace/plan.md` §3 | 函数名 + 指向 001 |
| 目录骨架（001 建目录；四份文件由 002 播种） | `specs/001-shared-workspace/spec.md` FR-8 | 002 引用，不重列 |
| `GET /astrbot_plugin_astral/state` | `specs/001-shared-workspace/plan.md` §6 | 002 只调用 |
| SOUL/MEMORY/AGENTS 的语义与守则文案 | `specs/002-soul-and-memory/spec.md`、`specs/002-soul-and-memory/plan.md` §5 | 001 只保证目录存在 |
| 快照格式、预算、截断、缓存 | `specs/002-soul-and-memory/plan.md` §4 | — |
| 只读页 `views/astral/` | `specs/002-soul-and-memory/plan.md` §7 | — |
| 源码坐标与升级回归清单 | `specs/001-shared-workspace/plan.md` §8、§9 | 其它文档不得自带行号 |
| 编号规则、质量门禁 | `CONSTITUTION.md` | — |

## 4. 编号规范

| 前缀 | 含义 | 规则 |
| --- | --- | --- |
| `FR-n` | 功能需求 | 每个规格包内从 1 连续编号；跨包引用写成 `001 FR-7` |
| `AC-f.n` | 验收标准 | `f` = 所属 FR 号、`n` = 该 FR 内序号（如 `AC-3.4`）；必须可执行（命令/观察点/断言） |
| `NFR-n` | 非功能要求 | 性能、安全、可用性、可维护性 |
| `T-n` | 任务 | 每个任务至少引用 1 条 AC |
| `ADR-n` | 架构决策记录 | 决策 / 理由 / 备选 / 影响 四段式 |
| `U-n` | 未决问题 | 必须写清负责人与默认处理 |

## 5. 与既有材料的关系

| 材料 | 关系 |
| --- | --- |
| `docs/archive/astral-workspace-design.md` | 原设计稿（1115 行），已被本目录取代；**第 6 章记忆方案（SQLite + embedding + 自动抽取）已作废**，改由 002 定义；其附录 A 的源码坐标有 4 处漂移，以 `specs/001-shared-workspace/plan.md` §8 为准 |
| `../_investigation/ws-backend.md` | 工作区全链路地图（数据模型/解析链/API/前端），作为背景阅读 |
| `../_investigation/plugin-surface.md` | 插件扩展面（能做什么、不能做什么） |
| `../_investigation/poc-report.md` | PoC 验证报告（为什么必须双写） |
| `../_investigation/verify/coordinate-verification.md` | 2026-11-01 对 4.29.4 @831abae85 的坐标核对（63 条断言），spec 的事实来源 |
| `../_investigation/poc/harness/harness.py` | 可运行的验证 harness（本沙箱内只跑到 11/73，见 001 `/tasks.md` T-0） |

## 6. 变更记录

| 日期 | 变更 | 影响 |
| --- | --- | --- |
| 2026-11-01 | 建立规格集：立项拆包（001 共享工作区 / 002 文件记忆） | 首个版本 |
| 2026-11-01 | 记忆方案由"SQLite + 作用域 + 自动抽取 + embedding"改为"SOUL.md + MEMORY.md 纯文件 + 对话级冻结快照" | 002 全新；原设计稿第 6 章作废并归档 |
