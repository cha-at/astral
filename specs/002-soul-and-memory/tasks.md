# 002 任务拆分：SOUL/MEMORY 文件记忆

> 状态：草案 v0.1 · 依赖：001-shared-workspace · 最后更新：2026-11-01
> 说明：任务只覆盖 002 包（`templates.py` / `snapshot.py` / `main.py` 的记忆相关部分 / `views/astral/`）。补丁、共享根、目录骨架、`state` 接口属 001，不在此列。
> 编号规则：T-n 连续；每个任务至少引用 1 条 AC（见 `spec.md`）；"并行"列里的任务可在不同分支/不同人同时做。

---

## 0 里程碑

| 里程碑 | 目标 | 退出条件 |
| --- | --- | --- |
| M0 模板与播种 | 四份文件能被正确播种、不被覆盖、托管区段可重写 | T-1～T-3 完成，AC-2.1/AC-2.2/AC-2.3/AC-4.1 通过 |
| M1 快照渲染与单测 | 渲染结果格式确定、截断与降级全覆盖 | T-4～T-7 完成，AC-1.1/AC-3.3/AC-5.1/AC-5.2 通过 |
| M2 注入钩子接入 | 每对话一份冻结快照注入 `system_prompt` | T-8～T-10 完成，AC-3.1/AC-3.2 通过 |
| M3 守则与容量/归档 | 守则随版本生效，容量与归档提示到位 | T-11～T-13 完成，AC-4.1/AC-4.2/AC-5.1/AC-5.2 通过 |
| M4 指令与只读页 | 用户在聊天与 WebUI 都能只读查看 | T-14～T-16 完成，AC-6.1/AC-6.2 通过 |
| M5 文档与发布 | 备份/迁移/隐私披露写进用户文档，全量回归 | T-17～T-18 完成，AC-1.2/AC-7.1/AC-7.2/AC-8.1/AC-8.2 通过 |

关键路径：`T-1 → T-2 → T-4 → T-8 → T-9 → T-18`。其余任务都可挂在这条路径外侧并行。

---

## 1 任务清单

### T-1 模板常量（`templates.py`）

- 目标：落地四份中文模板（`SOUL.md` / `MEMORY.md` / `AGENTS.md` / 托管守则），每份首行带 `<!-- astral:seeded:v1 -->`；内容与 `plan.md` §5 逐字一致。
- 依赖：—（不依赖 001 运行，可先写常量与单测）
- 验收：AC-1.1（四文件语义可辨）、AC-2.1（`test_seed_idempotent`）
- 并行：可与 T-8、T-14、T-16 同时进行

### T-2 幂等播种与原子写

- 目标：`seed_workspace(root)` 挂在 `Star.initialize()` 上，按 spec FR-2 的判定表执行（播种条件、不覆盖、目录缺失、空文件重播）；`atomic_write_text()` 用 `.tmp.{pid}` + `fsync` + `os.replace`；异常只记日志、不阻断加载。
- 依赖：T-1；目录存在性来自 `specs/001-shared-workspace/spec.md` 的 FR-8（开发期用临时目录，联调期用真实共享根）
- 验收：AC-2.1（删除后重播）、AC-2.2（不覆盖）、AC-2.3（`test_seed_atomic_on_failure`）
- 并行：可与其他 M1 任务并行

### T-3 托管区段同步

- 目标：`sync_managed_section(root)` 覆盖四种情形：完整标记对（幂等重写）、标记缺失（末尾追加）、标记异常（不写 + `WARNING` + 状态标记）、内容未变（不写盘）；区段外字节逐字保留。
- 依赖：T-2（共用原子写）
- 验收：AC-4.1（区段外保留、标记缺失追加）、`test_managed_markers_broken`
- 并行：与 T-4 并行

### T-4 快照渲染主体

- 目标：`render(root) -> str` 按 `plan.md` §4.1 输出头部、两条段标题（含字符数）、尾注；固定文案为模块常量；不读 DB、不联网。
- 依赖：T-1（段标题措辞与模板一致）
- 验收：AC-1.1、`test_render_basic`
- 并行：与 T-3 并行

### T-5 截断、归档索引与容量提示

- 目标：按 4000/8000 预算截断并追加"已截断"提示行与绝对路径；截断时最多列 10 条 `memory/archive/` 文件名与大小；`MEMORY.md` 超软上限时追加"容量提示"段。
- 依赖：T-4
- 验收：AC-5.1、AC-5.2、`test_render_truncate` / `test_render_archive_index` / `test_render_soft_limit`
- 并行：与 T-6 并行（不同函数分支）

### T-6 降级矩阵

- 目标：实现 `plan.md` §4.3 的七种降级（缺失/空/非 UTF-8/读失败/符号链接越界/双缺失/未预期异常），任何分支都不抛到钩子外。
- 依赖：T-4
- 验收：AC-3.3、`test_render_missing_and_empty` / `test_render_non_utf8` / `test_render_both_missing`
- 并行：与 T-5 并行

### T-7 渲染层单测补齐

- 目标：把 `plan.md` §9.1 中渲染相关用例写全（含 mock 计数断言"命中不读盘"）。
- 依赖：T-4、T-5、T-6
- 验收：AC-3.3、AC-5.1、AC-5.2；`pytest -k render` 全绿
- 并行：否（收口任务）

### T-8 注入钩子接入

- 目标：`@filter.on_llm_request()` 内解析键（`req.conversation.cid` → `get_curr_conversation_id` → `no-conv`）、取块、以 `old + "\n" + block` 追加 `req.system_prompt`；`memory.enabled=false` 直接返回。
- 依赖：T-4；共享根解析依赖 `specs/001-shared-workspace/spec.md` 的 FR-1/FR-2（联调前用 mock 根）
- 验收：AC-3.1（首个请求注入且不改写既有内容）、`test_hook_appends_system_prompt`、`test_hook_disabled`
- 并行：否（M2 关键路径）

### T-9 冻结缓存

- 目标：LRU(64) 存最终字符串；同键复用；`/new` 换 cid 后重新渲染；`/reset` 不换 cid 因此复用（行为需与 AC-3.1 的说明一致）。
- 依赖：T-8
- 验收：AC-3.2（sha 稳定 + 不重复读盘）、AC-3.1（`/new` 后可见新内容）
- 并行：否

### T-10 观测与开关

- 目标：`DEBUG` 日志 `conv=<cid[:8]> sha=<8 位> chars=<n> hit=<bool>`；`WARNING` 覆盖共享根缺失、读失败、托管区段异常；日志不含记忆正文；`memory.enabled` 与三个预算配置项接上。
- 依赖：T-8、U-1 结论（配置分组归属）
- 验收：AC-3.2（靠 sha 日志断言）、NFR-6
- 并行：与 T-11 并行

### T-11 守则与模板同步

- 目标：托管守则文案与 `spec.md` FR-4 的"守则必须覆盖"六项一一对应；版本号变更时整体重写区段；`show extra` 与只读页提示"区段会被覆盖"。
- 依赖：T-3、T-1
- 验收：AC-4.1、AC-4.2
- 并行：与 T-10 并行

### T-12 容量阈值与提示文案

- 目标：软上限（默认 20000，可配）接入渲染与 `/astral memory show` 输出；只读页容量条读同一阈值。
- 依赖：T-5
- 验收：AC-5.1
- 并行：与 T-13 并行

### T-13 归档约定

- 目标：`memory/archive/MEMORY-YYYYMM.md` 命名写进守则与 `AGENTS.md` 模板；渲染层只列索引不搬移；归档目录缺失/不可读时优雅省略。
- 依赖：T-5
- 验收：AC-5.2
- 并行：与 T-12 并行

### T-14 `/astral memory show` 指令

- 目标：`show [soul|memory|agents|extra]`；私聊给全文（>3000 字符截断 + 路径），群聊只给元信息 + "私聊或 WebUI"提示 + "全局共享"披露句（与只读页横幅同一句）；尾注说明"换新快照用 `/new`"。
- 依赖：T-4；指令组 `astral` 由 001 的 `/astral status` 建立（若 001 未建则本任务建组并保留 `status` 子指令名）
- 验收：AC-6.2、AC-7.1（群聊披露句）、U-5 结论
- 并行：与 T-15、T-16 并行

### T-15 `GET /astrbot_plugin_astral/files`

- 目标：按 `plan.md` §6 实现清单与单文件两种响应、白名单校验、512 KB 截断、400/404/500 错误码；只注册 GET。
- 依赖：T-4（复用字符数与状态计算）
- 验收：AC-6.2、`test_files_api_errors`（400 / 404 / 512 KB 截断）
- 并行：与 T-14 并行

### T-16 只读页 `views/astral/index.html`

- 目标：按 `plan.md` §7 六个区块实现；只调 001 的 `state` 与本包的 `files`；无编辑/刷新/上传下载；`textContent` 渲染；错误态红条。
- 依赖：T-15、`specs/001-shared-workspace/plan.md` §6 的 `state` 字段
- 验收：AC-6.1（与 `state` 一致、无 `textarea`/`contenteditable`）、AC-7.1（顶部横幅）
- 并行：与 T-14 并行

### T-17 用户文档与披露

- 目标：在插件 `README.md` / 用户文档补三节：文件式记忆怎么用（改 SOUL、让它记住、`/new` 生效）、备份与迁移命令（PowerShell `Copy-Item -Recurse`，并注明官方备份不含工作区文件）、隐私披露（全局共享 + 禁止写入清单）。
- 依赖：T-14、T-16
- 验收：AC-7.1、AC-8.1、AC-8.2
- 并行：否（需功能定稿）

### T-18 发布自检与 001 联调

- 目标：在真实 AstrBot 实例上按 AC-1.1～AC-8.2 全量回归；与 001 联调三项：共享根路径、目录骨架存在、`state` 字段；检查注入位置仍在 `system_prompt` 末尾（`plan.md` §4.5）。
- 依赖：全部任务；001 的 M0～M2
- 验收：AC-1.1～AC-8.2 全绿；升级回归清单补一条"核对 `internal.py:307` 之前是否新增 system_prompt 追加"（R-5）
- 并行：否（收口）

---

## 2 依赖图

```text
T-1 ─► T-2 ─► T-3 ─► T-11 ─┐
  └──► T-4 ─┬► T-5 ─┬► T-7 ─┐
            │        ├► T-12 │
            │        └► T-13 │
            ├► T-6 ──────────┤
            ├► T-8 ─► T-9 ──►├──► T-18
            │        └► T-10 ┘
            ├► T-14 ─► T-17 ─┘
            └► T-15 ─► T-16 ─┘
```

## 3 可并行分组

| 批次 | 任务 | 前置 |
| --- | --- | --- |
| 批次 A | T-1、T-14、T-16（页面骨架可用假数据先写） | 无 |
| 批次 B | T-2、T-4 | T-1 |
| 批次 C | T-3、T-5、T-6、T-15 | T-2 / T-4 |
| 批次 D | T-8、T-11、T-12、T-13 | 批次 C |
| 批次 E | T-9、T-10、T-7 | T-8 / T-5 / T-6 |
| 批次 F | T-17、T-18 | 全部 |

## 4 完成定义（DoD）

- 每个任务的 AC 都能在真实实例上复现，命令与观察点写进 PR 描述。
- `pytest` 全绿；`ruff format .` 与 `ruff check .` 无改动。
- 日志中无记忆正文（抽查 `DEBUG` 输出）。
- `spec.md` / `plan.md` 与新实现不一致处已同步，或已登记为未决问题（U-n）。
- 不新增磁盘状态：代码中不存在除共享根四文件与 `memory/archive/` 之外的写入点。
