# Astral 测试

## 运行环境

插件测试需要同时满足两个条件：`astrbot` 包可导入、数据目录可写（不能写到真实的 AstrBot 数据目录）。

本机（Windows，AstrBot 实例自带 venv）：

```powershell
$venv = "C:\Users\Chake\.astrbot_launcher\instances\101d7e36-5893-4d9c-bd47-0a4d60ce6bdf\venv\Scripts\python.exe"
$core = "C:\Users\Chake\.astrbot_launcher\instances\101d7e36-5893-4d9c-bd47-0a4d60ce6bdf\core"
$env:PYTHONPATH = "$core;$env:TEMP\astral-pytest"
$env:ASTRBOT_ROOT = "$env:TEMP\astral-testroot"
Set-Location <插件目录>
& $venv -m pytest tests -q
```

`pytest` 不在 AstrBot venv 里，测试用一份安装到 `%TEMP%\astral-pytest` 的独立副本：

```powershell
& $venv -m pip install --target "$env:TEMP\astral-pytest" pytest
```

## 分层

| 层 | 用例 | 依赖 |
| --- | --- | --- |
| 纯函数 | `test_config.py`、`test_store.py`、`test_templates.py`、`test_snapshot.py`、`test_views_contract.py` | 仅标准库 |
| 补丁 | `test_ws_shared.py` | 真实 `astrbot.core.workspace` |
| 坐标回归 | `test_coordinates.py` | 真实 AstrBot 源码树 |
| 装配 | `test_main_hooks.py` | 真实 `astrbot` + 假 Context |
