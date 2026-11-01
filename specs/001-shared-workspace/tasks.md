# 001 · 共享工作区 任务拆分（tasks）

> 状态：草案 v0.1 · 最后更新：2026-11-01 · 基线：AstrBot 4.29.4 @ `831abae85`
> 需求见 `spec.md`（FR/AC），实现方案见 `plan.md`（§ 引用）。
> 每个任务都必须能回答："做完后哪几条 AC 会从红变绿"。

## 1. 里程碑总览

| 里程碑 | 目标 | 出口条件 | 预估 |
| --- | --- | --- | --- |
| M0 骨架 | 仓库从 helloworld 模板变成可装的 Astral 骨架；补丁核心有单测 | `pytest tests/` 绿；插件能在 4.29.4 加载 | 0.5 天 |
| M1 P0 可用 | 三个平台共享同一目录；`/astral status` 可自检 | AC-1.x、AC-3.3、AC-7.x 全绿 | 0.5–1 天 |
| M2 P1 稳定 | 幂等、还原、失败语义、面板一致 | AC-2.x、AC-4.x、AC-5.x、AC-6.x、AC-9.x、AC-10.x 全绿 | 1–2 天 |
| M3 P2 运维 | 审计、归档、配额、可选迁移 | AC-11.x 全绿 | 1 天 |
| M4 发布 | README/CHANGELOG/metadata/回归脚本 | AC-12.x、AC-13.x 全绿 | 0.5–1 天 |

## 2. 任务表

### T-0 非沙箱环境重跑完整 harness（阻塞发布，不阻塞开发）

- 目标：拿到 `../_investigation/poc/harness/harness.py` 的完整 73 条断言输出（本沙箱内只跑到 11/73，原因是 `%TEMP%` 嵌套目录被拒）。
- 依赖：无。
- 做法：在可写 `%TEMP%` 嵌套目录的环境执行 `python ../_investigation/poc/harness/harness.py`，把输出存为 `../_investigation/verify/harness-<date>.txt` 并在 CHANGELOG 里记录断言数。
- 验收：输出末行为断言汇总且 failed=0；文件入库。
- 并行：可与 M0/M1 同时进行。

### M0 · 骨架

#### T-1 模板清理与仓库骨架

- 目标：删掉 helloworld 残留，建立 `main.py` / `ws_shared.py` / `config.py` / `_conf_schema.json` / `tests/` 骨架。
- 依赖：无。
- 做法：替换 `main.py`；删除 `@register("helloworld", ...)`；`metadata.yaml` 补 `astrbot_version: ">=4.29,<5"`；保留 `LICENSE`。
- 验收依据：AC-12.1、AC-12.3。
- 并行：是（与 T-2、T-3 互不阻塞）。

#### T-2 配置解析与黑名单（`config.py`）

- 目标：实现 `plan.md` §2 的四种路径形态解析、`~` 展开、相对路径基准、黑名单判定与原因文案。
- 依赖：无。
- 验收依据：AC-1.2、AC-1.3、AC-1.4、AC-2.2、AC-2.3。
- 单测：路径解析 4 例 + 黑名单 5 例（含 Windows 大小写、`is_relative_to` 边界）。
- 并行：是。

#### T-3 补丁核心 `ws_shared.py` + 单测

- 目标：实现 `plan.md` §3 的 install/uninstall/status 与双写算法。
- 依赖：无（用桩加载真实 `workspace.py`，见测试策略）。
- 验收依据：AC-3.1、AC-3.2、AC-3.4、AC-3.5、AC-3.6、AC-4.1、AC-4.3、AC-5.2、AC-5.3、AC-6.1。
- 测试文件：`tests/test_ws_shared.py`（负对照/正对照必须都在）。
- 并行：是。

### M1 · P0 可用

#### T-4 目录骨架与首启播种

- 目标：创建共享根目录树；按 `plan.md` §5 的"只补不覆盖 + 原子落盘"播种 `AGENTS.md`、`EXTRA_PROMPT.md`、`SOUL.md`、`MEMORY.md`。
- 依赖：T-2（需要解析后的根）。
- 验收依据：AC-8.1、AC-8.2、AC-8.3、AC-8.4。
- 备注：四个模板文案由 002 的 `templates.py` 提供；本任务只做落盘逻辑与幂等。

#### T-5 安装接入生命周期

- 目标：`__init__` 里完成"解析 → 校验 → 建目录 → 播种 → install"；`on_astrbot_loaded` 再 install 一次作保险。
- 依赖：T-2、T-3、T-4。
- 验收依据：AC-3.3、AC-3.5、AC-4.2。
- 备注：安装失败不得抛出未捕获异常导致插件加载失败，但必须让状态可见（FR-6）。

#### T-6 还原

- 目标：`terminate()` 调用 `uninstall()`；重复调用安全。
- 依赖：T-3、T-5。
- 验收依据：AC-5.1、AC-5.3。

#### T-7 失败语义（FR-6）

- 目标：缺函数、黑名单命中、目录不可写三种失败统一为 `installed=false + reason`，且"要么全装要么不装"。
- 依赖：T-2、T-3、T-5。
- 验收依据：AC-1.5、AC-2.1、AC-2.4、AC-6.1、AC-6.2。

#### T-8 状态接口 `GET /state`

- 目标：按 `plan.md` §6.1 的契约实现；未安装返回 503；路由必须带插件名前缀。
- 依赖：T-3、T-7。
- 验收依据：AC-7.1、AC-7.2、AC-10.1、AC-10.2。
- 可并行：与 T-9 并行。

#### T-9 `/astral status` 指令

- 目标：聊天指令输出共享根、安装状态、consumers 数、runtime、失败原因与 sandbox 提示。
- 依赖：T-3、T-7。
- 验收依据：AC-7.3、AC-7.4。
- 可并行：与 T-8 并行。

#### T-10 三平台冒烟

- 目标：Telegram 群 / QQ 群 / 私聊各发一条让 Agent 执行 `pwd`；A 群写文件、B 群读。
- 依赖：T-5、T-6。
- 验收依据：AC-3.3 与 `spec.md` §1.2 成功判据。
- 产出：一段可复制的操作记录（群名、指令、路径输出），贴进 CHANGELOG。

### M2 · P1 稳定

#### T-11 Dashboard 面板一致（`patch_webchat_projects`）

- 目标：接管 `resolve_project_workspace_root`；开关关闭时状态接口明示不一致。
- 依赖：T-3、T-8。
- 验收依据：AC-9.1、AC-9.2、AC-9.3。

#### T-12 重载/停用压测

- 目标：WebUI 重载 10 次、停用/启用各 5 次，验证不叠加、可还原、端点 200/503 正确。
- 依赖：T-5、T-6、T-8。
- 验收依据：AC-4.2、AC-5.1、AC-10.1、AC-10.2、AC-10.3。

#### T-13 日志与告警

- 目标：按 `plan.md` §7 补齐 INFO/ERROR 日志；`/astral status` 在 `consumers` 为空时给出显式告警。
- 依赖：T-5、T-9。
- 验收依据：AC-2.1（ERROR 可见）、AC-6.1、AC-7.3。

### M3 · P2 运维

#### T-14 审计日志

- 目标：`.astral/audit.jsonl` 追加式写入，字段 `ts/action/detail`。
- 依赖：T-4。
- 验收依据：AC-11.2。

#### T-15 归档与 `/astral clean`

- 目标：按天数把产物移动到 `archive/<yyyyMMdd-HHmmss>/`（移动非删除）；提供指令与 POST 端点。
- 依赖：T-14。
- 验收依据：AC-11.1。

#### T-16 配额告警

- 目标：统计共享根体积；超过 `quota_gb` 时状态接口与指令给出 warn，不删除。
- 依赖：T-8、T-14。
- 验收依据：AC-11.3。

#### T-17 历史目录迁移（可选）

- 目标：`POST /astrbot_plugin_astral/migrate`，把 `<data>/workspaces/*` 复制（不移动）到共享根的 `shared/` 下，按 mtime 保留较新。
- 依赖：T-4、T-8。
- 验收依据：新增 AC（实现时补 AC-11.4），并在 `decisions.md` 记录"默认不自动执行"的理由。
- 可并行：是（不阻塞发布）。

### M4 · 发布

#### T-18 README 与 CHANGELOG

- 目标：README 必含四段（能力与限制、配置、隐私披露、升级与备份）；CHANGELOG 每版记录回归结论与 harness 断言数。
- 依赖：M1、M2。
- 验收依据：AC-12.2、AC-13.2。

#### T-19 坐标核对脚本

- 目标：把 `plan.md` §9 的第 2–4 步做成可执行用例（脚本或 pytest），在基线通过、在人为改名桩上失败。
- 依赖：T-3。
- 验收依据：AC-13.1。

#### T-20 平台矩阵冒烟

- 目标：Windows / macOS / Linux（至少 x64 + arm64 各一）验证 `pwd` 一致与路径分隔符处理。
- 依赖：T-10。
- 验收依据：NFR-4。

## 3. 依赖与并行图

```text
T-0 ────────────────────────────────┐（发布前必须完成）
T-1 ─┬─ T-2 ─┬─ T-4 ─┬─ T-5 ─┬─ T-6 ─┬─ T-10 ── T-20
     └─ T-3 ─┘        │       ├─ T-7 ── T-8 ─┬─ T-11
                      │       │             └─ T-16
                      │       └─ T-9 ── T-13
                      ├─ T-14 ─┬─ T-15
                      │        └─ T-16
                      └─ T-17
T-3 ── T-8 ── T-12
T-3 ── T-19
M1+M2 ── T-18
```

可并行的三组：{T-1, T-2, T-3}、{T-8, T-9}、{T-11, T-13, T-14}。

## 4. 完成定义（DoD）

一个任务算完成，必须同时满足：

1. 对应 AC 有可执行证据（测试输出、curl 响应、聊天截图或路径输出）；
2. 新增代码有单测或手工验证步骤记录；
3. 涉及的文档（本包 `spec.md`/`plan.md`）如有行为变化已同步更新；
4. 失败路径有日志，且不出现静默降级。
