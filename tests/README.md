# Astral 测试

## 运行环境

测试需要两个条件：`astrbot` 包可导入、数据目录可写（不能写到真实 AstrBot 数据目录）。

```powershell
# 把 <AstrBot 实例> 换成实际安装目录，<插件目录> 换成仓库根
$env:PYTHONPATH = "<AstrBot 实例>\core;$env:TEMP\astral-pytest"
$env:ASTRBOT_ROOT = "$env:TEMP\astral-testroot"
$env:ASTRBOT_SOURCE = "<AstrBot 源码检出目录>"   # 可选：坐标用例需要
Set-Location <插件目录>
& "<AstrBot 实例>\venv\Scripts\python.exe" -m pytest tests -q
```

`pytest` 不在 AstrBot venv 里时，装一份独立副本：

```powershell
& $venv -m pip install --target "$env:TEMP\astral-pytest" pytest
```

## 分层

| 层 | 用例 | 依赖 |
| --- | --- | --- |
| 审核契约 | `test_review_contract.py` | 仅标准库；静态扫描仓库根 `.py` |
| 纯函数 | `test_config.py`、`test_store.py`、`test_templates.py`、`test_snapshot.py`、`test_views_contract.py`、`test_versioning.py` | 标准库 + 可导入的 `astrbot.api.logger`；`test_versioning.py` 额外调用真实 `git`（缺失时整组跳过） |
| 补丁 | `test_ws_shared.py` | 真实 `astrbot.core.workspace` |
| 坐标回归 | `test_coordinates.py` | 真实 AstrBot 源码树（`ASTRBOT_SOURCE`） |
| 装配 | `test_main_hooks.py` | 真实 `astrbot` + 假 Context |

`test_templates.py` 的模板逐字比对依赖维护者本地的 `specs/`；该目录不随仓库发布，缺失时相关用例自动跳过。

`test_versioning.py` 会真实调用系统 Git（经 `subprocess`，无 shell），机器上没有 Git 时整个模块跳过，其余用例不受影响。
