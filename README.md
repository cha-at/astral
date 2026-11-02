# Astral 记忆 · 一个工作区，一份记忆

**装上之后，Bot 会记住群里的人和事：换个群不失忆，说话像同一个人。**

Astral 是 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的插件。它把所有平台、所有群、所有私聊的 Agent 工作区指向同一个共享目录，并在里面放一份长期记忆 `MEMORY.md` 和一份人设 `SOUL.md`——每段对话开始时，这两份文件会被渲染成一段上下文交给模型。

> **全局共享 = 所有群可见。** 这是它最大的好处，也是它唯一的风险：写进记忆的东西，别的群、别的平台也能看到。往下读[隐私披露](#隐私披露)再决定往里写什么。

## 效果预览

**装之前**：每个群各聊各的，昨天说的事今天就忘。

```text
群 A：记住，以后每周五晚上八点开黑
Bot：好的，我记住了！
（第二天，群 A）这周五有安排吗？
Bot：抱歉，我看不到之前的聊天记录。
```

**装之后**：一份记忆，所有群共用。

```text
群 A：记住，以后每周五晚上八点开黑
Bot：记下了，我写进 MEMORY.md 了。
（第二天，群 B）这周五有安排吗？
Bot：你们固定周五晚八点开黑，别撞车。
```

它记住的是**你明确让它记下的那几条**，不是全部聊天记录——别指望它复述昨天群里的每句话。

想确认它到底记了什么：群里发 `/astral memory show`，或打开 WebUI → 插件 → Astral 的只读页。

## 装完三步

### ① 安装并启用

- **插件市场**（推荐）：WebUI → 插件 → 插件市场，搜「Astral 记忆」或 `astrbot_plugin_astral`。
- **从仓库装**：同一页面选「从仓库安装」，填 `https://github.com/cha-at/astral`。
- **手动**：把本目录放进 `<AstrBot>/data/plugins/astrbot_plugin_astral/`，在**插件配置页**里启用或改写配置。

配置改完必须**重载插件**才生效：共享根解析、目录骨架、四份文件播种、补丁安装都在插件加载时执行。

### ② 让它能写记忆——这一步别跳过

打开 AstrBot → 配置 → Agent → **Computer Use 运行时**，选「本机环境」。

| 运行时 | Bot 有文件工具吗 | 结果 |
| --- | --- | --- |
| 本机环境（`local`） | 有 | 正常：能读也能写 `MEMORY.md` |
| 不允许任何环境（`none`，**默认**） | 没有 | 记忆只能读不能写：它嘴上答应"记下了"，文件里一个字都没有 |
| 第三方沙箱环境（`sandbox`） | 有，但在容器里 | 写进容器的 `/workspace`，不是你的共享根 |

想直接改配置文件：把 `provider_settings.computer_use_runtime` 设为 `local`，然后重载插件。插件检测到运行时不是本机环境时，`/astral status` 与插件页都会给出告警。

### ③ 验收

1. 群里发 `/astral status`，看到「Astral 已生效 ✅」和正确的记忆目录；
2. 对它说一句「记住：这个群叫我老王」，再发 `/astral memory show`，能在 `MEMORY.md` 里看到这条；
3. 想改性格、口吻或名字：编辑共享根里的 `SOUL.md`，改完 `/new` 开新对话生效。

## 它能干什么

| 能力 | 表现 |
| --- | --- |
| 一个工作区 | 任意平台、任意群、任意私聊里的 Agent，cwd 与文件读写基准都是同一个共享根；A 群写的文件，B 群直接能读 |
| 一份记忆 | `SOUL.md` 描述身份、语气与边界，`MEMORY.md` 保存长期记忆（谁是谁 / 群里的事 / 我自己的事 / 我学到的）；对话开始时把两者渲染成一段标着 `# 关于你自己` 的上下文，追加到系统提示 |
| 看得见它记了什么 | 聊天里 `/astral memory show`、插件只读页，都能看到"它到底记了什么"；插件页没有任何写入口 |
| 可观测、可回滚 | `/astral status` 与 `GET .../state` 给出生效状态、记忆目录与失败原因；停用插件即恢复原生"按会话分目录"的行为 |

## 它不能干什么

先看这张表，免得装完失望：

| 限制 | 说明 |
| --- | --- |
| 不能修改内置 WebUI | Astral 不能给 AstrBot 内置 WebUI 加按钮、加标签页，也不能替换"工作区文件"面板；Astral 只有自己的插件页（WebUI → 插件 → Astral） |
| 不改 AstrBot 源码 | 全部通过运行期 monkeypatch 包装工作区解析函数实现；插件目录之外的代码零改动，停用插件即还原 |
| 安全影响（monkeypatch） | 接管 `resolve_project_workspace_root` 会让 AstrBot 针对 API-key 项目与 custom workspace 的越界校验不再生效；只在受信任的实例上使用，并绑定 4.29.x |
| 只适配 AstrBot 4.29.x | 补丁绑定具体源码位置；其它版本在插件市场显示"不支持"，升级 AstrBot 后要按[升级与备份](#升级与备份)一节回归 |
| 不支持多进程 | 补丁只在各自进程内生效；多 worker 部署下各进程各打各的补丁，行为无法保证 |
| `computer_use_runtime=sandbox` 时工作区不参与 Agent 执行 | Agent 在容器内的 `/workspace` 里干活，与宿主机共享根无关；`/astral status` 会提示 |
| 没有文件上传与在线编辑 | 插件页只读；写文件请用聊天里的文件工具，或直接编辑共享根下的文件 |
| 全局共享 = 跨群可见 | 隐私边界靠披露与守则约束，不做访问控制，详见[隐私披露](#隐私披露) |

## 配置说明

配置在 AstrBot WebUI 的**插件配置页**修改；保存后**重载插件**生效（共享根解析、目录骨架、播种、托管区段同步与补丁安装都在插件加载时执行）。

| 配置项 | 含义 | 默认值 | 注意点 |
| --- | --- | --- | --- |
| `shared_root` | 共享根目录 | 空字符串：落在 AstrBot 数据目录的 `workspaces/astral` | 大多数情况不用改。支持 `~` 与相对路径（相对数据目录的 `workspaces`）；命中危险路径黑名单会拒绝安装，不会回落到默认目录 |
| `patch_webchat_projects` | 是否同时接管 webchat 项目的工作区解析（Dashboard"工作区文件"面板） | `true` | 设为 `false` 后面板显示项目自己的目录、Agent 仍在共享根，两者不一致，`/astral status` 会明示 |
| `forbidden_roots` | 额外禁止的共享根前缀 | 空列表 | 默认已禁止 AstrBot 源码、数据敏感区（`config`/`plugins`/`plugin_data`）、用户主目录敏感区与系统目录；位于 `<data>` 子树内的默认根对"AstrBot 源码"规则例外（否则默认根会拒绝自己），但 `data/config`、`data/plugins`、`data/plugin_data` 仍单独禁止 |
| `quota_gb` | 共享根体积告警阈值（GB） | `0`（不限制） | 超限只在 `/astral status` 与状态接口给 warn，**不自动删除**任何文件 |
| `memory.enabled` | 是否在对话开始时注入记忆快照 | `true` | 关闭后不注入，播种与只读页不受影响 |
| `memory.soul_budget_chars` | `SOUL.md` 的单次注入预算（字符） | `4000` | 超预算只截断快照，不改文件；完整内容用文件工具读取 |
| `memory.memory_budget_chars` | `MEMORY.md` 的单次注入预算（字符） | `8000` | 同上；截断时提示行会给原文总字符数与绝对路径 |
| `memory.soft_limit_chars` | `MEMORY.md` 的容量软上限（字符） | `20000` | 超过后快照尾部出现容量提示，插件页“运行与维护”里能看到这个上限；建议合并重复条目并归档到 `memory/archive/` |

记忆相关的四个配置项在配置页的 `memory` 分组下。它们只影响快照渲染与插件页的容量提示，不会修改磁盘上的文件。

## 指令

| 指令 | 作用 |
| --- | --- |
| `/astral status` | 查看是否生效、记忆目录在哪、记忆概况，以及运行时告警。典型输出见下 |
| `/astral clean [days]`（需要 AstrBot 管理员） | 把共享根**顶层**修改时间早于 `days` 天的普通文件**移动**到 `archive/<yyyyMMdd-HHmmss>/`。**只移动、不删除，可还原**；不递归，也不处理 `skills/`、`shared/`、`memory/`、`archive/`、`.astral/` 与四份内容文件。省略 `days` 时默认 **30** 天 |
| `/astral memory show` | 查看记忆文件：默认 `memory`；私聊返回全文（超过 3000 字符截断并给出路径）；群聊只返回文件名、字符数、更新时间与前 3 行，完整内容请私聊或到 WebUI 只读页 |

`/astral memory show [soul|memory|agents|extra]` 的四个参数分别对应 `SOUL.md`、`MEMORY.md`、`AGENTS.md`、`EXTRA_PROMPT.md`，大小写不敏感。

`/astral status` 的典型输出（群里只回可行动的信息，不再有内部计数器）：

```text
Astral 已生效 ✅
- 记忆目录：C:\AstrBot\data\workspaces\astral
- 记忆概况：SOUL 620 字 / MEMORY 12 条（1.8k 字），最近 11-01 20:26 更新
- 看它记得什么：/astral memory show
- 全局共享：所有平台、所有群共用这一份，别写密钥和别人的隐私。
```

未生效时长这样（原因若不是中文，会拆成「结论 / 原始信息」两行，方便你搜到对应报错）：

```text
Astral 未生效 ❌
- 结论：插件没装上。最常见的是 AstrBot 版本对不上，或共享根不可写。
- 原始信息：missing AstrBot workspace symbols: resolve_workspace_root_for_umo
- 下一步：重载插件后再发一次 /astral status；还是不行就看插件页的提示。
```

## HTTP 接口

接口统一挂在 AstrBot Dashboard 的插件前缀下，例如本机默认端口：

`http://127.0.0.1:6185/api/v1/plugins/extensions/astrbot_plugin_astral/…`

所有接口都沿用 AstrBot Dashboard 的登录鉴权；插件页通过 bridge 调用，不需要自己带 Cookie。

| 方法 | 路径 | 成功（200） | 失败语义 |
| --- | --- | --- | --- |
| GET | `/api/v1/plugins/extensions/astrbot_plugin_astral/state` | 返回 `installed`、`shared_root`、`baseline`、`runtime`、`consumers`、`disk`、`quota`、`memory`、`warnings`、`reason` | 503：插件已停用或补丁未安装，body 仍含 `installed=false` 与 `reason` |
| GET | `/api/v1/plugins/extensions/astrbot_plugin_astral/files` | 不带 `name` 返回清单（`shared_root`、`memory_soft_limit`、四个文件的字符数/更新时间/`managed_ok`、归档索引）；带 `?name=SOUL` 返回单文件正文与 `truncated` 标记（可选 `MEMORY`、`AGENTS`、`EXTRA`，大小写不敏感） | 400 `unknown_name`（名称不在白名单或含 `../` 等注入尝试）、400 `forbidden_path`（解析后越出共享根）；404 `not_found`；500 `read_failed`；503 插件未安装 |
| POST | `/api/v1/plugins/extensions/astrbot_plugin_astral/clean` | 归档完成，返回本次归档的时间戳目录与 `days` | 非法 `days` 按 30 处理并返回 200；503 插件未安装 |

约束：`files` 只注册 GET，不提供 PUT/POST/DELETE；单文件响应最多 512 KB，超出时截断并标 `truncated=true`；不返回目录树、不返回归档正文，错误响应不回传异常栈。

## 共享根目录结构

```text
<共享根>/
├─ SOUL.md             # 它是谁、怎么说话、底线（每对话注入）
├─ MEMORY.md           # 长期记忆（每对话注入）
├─ AGENTS.md           # 工作区说明（不注入对话）
├─ EXTRA_PROMPT.md     # 注入型守则；astral:managed 标记之间由 Astral 重写
├─ skills/             # 工作区技能，每个技能一个 SKILL.md
├─ shared/             # 跨会话共享的交付物
├─ memory/archive/     # 记忆归档，MEMORY-YYYYMM.md
├─ archive/            # 其它归档
└─ .astral/            # Astral 自用状态，不要手改
```

四份内容文件只在"不存在或为空白"时播种，**已有内容永不被覆盖**；想恢复默认模板，删除对应文件后重载插件即可。托管区段是唯一由 Astral 覆盖内容的位置，标记之外属于你。

## 隐私披露

**全局共享 = 所有群可见。** 工作区与记忆只有一份：所有平台、所有群、所有私聊共用它，写进工作区文件或 `MEMORY.md` 的内容，其他群、其他平台、其他私聊都可能看到。

- 以 Agent 身份从事管理操作的另一个人或 admin，同样能看到共享目录里的东西，也能读写它；不要把它当成只有自己能看的私人空间。
- **不要把密钥、口令、token、私钥、身份证/银行卡号、手机号/邮箱/住址，或第三方隐私写进工作区或记忆文件。** 需要记录"某人偏好"时请去标识化（写"群里有人偏好中文回复"，不要写群号 + 姓名 + 手机号）。
- 群聊里的 `/astral memory show` 只给元信息与前 3 行，这是隐私要求而不是权限系统；完整内容请在私聊或 WebUI 只读页查看。
- 需要删除记忆时，让 Agent 直接编辑文件删除对应条目并回报删了几行；没有独立回收站，备份就是回滚点。
- Astral 不自动扫描敏感信息，也不加密、不做访问控制；是否写入由你和 Agent 遵守的守则判断。

## 升级与备份

**需要单独备份共享根。** AstrBot 官方的"维护 → 备份"覆盖配置、插件与数据库等数据，但**不包含共享根里的工作区文件**；只做官方备份会丢掉全部工作区与记忆。

PowerShell 备份（把共享根整目录复制到备份盘）：

```powershell
# 共享根路径以 /astral status 显示的「记忆目录」为准
Copy-Item -Recurse -Force "C:\AstrBot\data\workspaces\astral" "D:\backup\astral-20261102"
```

PowerShell 恢复（先停用插件，避免恢复过程中被写入）：

```powershell
Copy-Item -Recurse -Force "D:\backup\astral-20261102\*" "C:\AstrBot\data\workspaces\astral"
```

迁移到新机器：先在新机器配置好 `shared_root`，重启让插件建好目录骨架，再把备份内容拷回共享根并重启。对话快照是进程内内存态：同一进程内冻结，改文件后要 `/new` 才刷新；重启或重载插件后缓存清空，已有对话下一轮会按当前文件重建一次。

升级 AstrBot 之后：

1. 重载插件，发 `/astral status`，确认「Astral 已生效」、记忆目录正确；
2. 跑 `pytest tests/`（命令见[开发与测试](#开发与测试)），确认补丁、配置、快照与页面契约用例全部通过；
3. 按 [docs/dev-notes.md](docs/dev-notes.md) 的"升级回归清单"核对源码坐标：4 个解析函数是否存在、3 个消费者的 import 形态、注入钩子的位置是否仍在 core 组装之后；
4. 行为不符时停用插件即回滚到原生行为；记忆内容用备份恢复。

## 常见问题（FAQ）

| 问题 | 回答 |
| --- | --- |
| 它说"记下了"，但 `MEMORY.md` 里没有？ | 九成是 Computer Use 运行时没设成本机环境（默认 `none`），Bot 没有文件工具。按[装完三步](#装完三步)第 ② 步改完并重载插件。 |
| 改了 `SOUL.md` / `MEMORY.md`，为什么这一轮对话没变化？ | 快照在对话开始时冻结，本轮不刷新。改完文件后要 `/new` 开新对话才会加载新内容。 |
| `/reset` 能刷新快照吗？ | 不能。`/reset` 只清空对话历史、不换对话 ID（cid），快照仍然复用；要换新快照请用 `/new`。 |
| 群聊里 `/astral memory show` 为什么只有几行？ | 群聊只给元信息与前 3 行（隐私要求），完整内容请私聊机器人或在 WebUI 只读页查看。 |
| 第三方 agent runner 能用吗？ | 未验证。快照追加在 `system_prompt` 末尾，依赖 core 采纳该字段；第三方 runner（`agent_runner.runner_type`）下是否生效没有测试，按"未知 / 未支持"对待。 |
| 插件停用后页面和指令还能用吗？ | 不能。端点自检返回 503、指令消失；共享根里的 `EXTRA_PROMPT.md` 仍在磁盘上，但补丁还原后 core 回到每会话工作区查找它，守则不再注入（v1.0.0 代码路径如此）。 |
| 可以跑多进程 / 多 worker 吗？ | 不支持。补丁只在各自进程内生效，多 worker 下行为不确定。 |
| 沙箱运行时下工作区在哪？ | `computer_use_runtime=sandbox` 时 Agent 在容器内的 `/workspace` 干活，不参与共享根；`/astral status` 会给出提示。 |
| 共享根磁盘只涨不跌怎么办？ | 定期用 `/astral clean 30` 归档临时产物（移动而非删除）；`quota_gb` 只告警不删除，需要你自己清理或扩容。 |
| 删掉共享根会怎样？ | 插件下次加载会重建目录骨架并重新播种四份文件；已有记忆若没有备份就丢了，所以需要单独备份共享根。 |
| 插件页为什么不提供编辑、保存按钮？ | 只读页刻意不提供写入口；修改请用聊天里的文件工具，或直接编辑共享根下的文件。 |

## 开发与测试

本仓库根 = 插件目录。测试命令（把路径换成你的实例路径）：

```powershell
$venv = "C:\AstrBot\venv\Scripts\python.exe"
$env:PYTHONPATH = "C:\AstrBot\core;$env:TEMP\astral-pytest"
$env:ASTRBOT_ROOT = "$env:TEMP\astral-testroot"
Set-Location "C:\AstrBot\data\plugins\astrbot_plugin_astral"
& $venv -m pytest tests -q
```

- `ASTRBOT_ROOT` 必须指向临时目录：测试绝不能写真实 `data/`。
- `pytest` 不在 AstrBot 自带 venv 里时，按 [tests/README.md](tests/README.md) 的说明装到 `%TEMP%\astral-pytest`。
- 只跑自己负责的用例：`python -m pytest tests/test_views_contract.py -q`。

| 用例文件 | 覆盖内容 |
| --- | --- |
| `tests/test_views_contract.py` | 只读页静态契约（无写入口、无注入、只调两个 GET、横幅披露）与 README 必含内容 |
| `tests/test_config.py`、`tests/test_config_regression.py` | 共享根解析规则、危险路径黑名单、默认根位于安装目录内时的放行回归 |
| `tests/test_ws_shared.py` | 4 个函数的补丁、双写（含负对照）、幂等安装与还原 |
| `tests/test_store.py` | `.astral/` 状态与审计写入、配额告警、移动归档 |
| `tests/test_templates.py` | 四份默认模板（与 plan 逐字比对）、托管区段重写与原子写 |
| `tests/test_snapshot.py` | 快照渲染、HTML 注释剥离、预算截断、降级与缓存命中 |
| `tests/test_coordinates.py` | 源码坐标回归（函数存在性、签名、消费者 import 形态） |
| `tests/test_main_hooks.py` | 插件装配、state 载荷、快照注入与冻结、files 接口、端点 503 自检 |
| `tests/test_plugin_load.py` | 以 `data.plugins.<插件名>.main` 包路径导入并检查指令/钩子注册 |
| `tests/test_smoke_env.py` | 测试环境自检（AstrBot 可导入、4 个解析函数存在） |

## 真实实例验收（v1.0.0）

以下结果在 AstrBot 4.29.4 实例上实测（2026-11-02）：

- 插件页返回 200，顶部有"全局共享"横幅，页面只调用 `state` 与 `files` 两个 GET。
- `GET .../state` → 200，`installed=true`；`GET .../files` → 200，`?name=SOUL` 含 `content`，`?name=../../etc/passwd` → 400 `unknown_name`。
- 停用插件后 `state` → 503 且 `installed=false`；重新启用 → 200 且 `installed=true`。
- 群里 `/astral status` 输出生效状态与记忆概况；`/astral memory show` 私聊给全文、群聊只给元信息与前 3 行。
- 全量 `pytest tests -q` = 157 passed / 2 skipped（Windows 无符号链接权限）。

完整验收记录见 [CHANGELOG.md](CHANGELOG.md)。

## 维护者信息

- 基线：AstrBot 4.29.4（源码提交 `831abae85`），兼容版本范围 `>=4.29.4,<4.30`；补丁绑定具体源码位置，不保证其它版本可用。
- 运行环境：随 AstrBot 4.29.4（Python 3.12+），Windows / macOS / Linux，单进程部署。
- 实现说明：[docs/dev-notes.md](docs/dev-notes.md)（模块地图、对外契约与升级回归清单）。
- `Astral` 仅作为本插件展示名；本项目与 Astral Software Inc.（Ruff / uv）无关，也未发布同名 Python 包。

## 许可

[GNU Affero General Public License v3.0](LICENSE)。Copyright (C) 2026 ChakerAt。
