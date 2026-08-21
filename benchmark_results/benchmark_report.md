# Autonomous Coding Agent — Benchmark Report

## 📊 Summary Metrics

- **Total Runs:** 10
- **Passed:** 10
- **Failed:** 0
- **Pass Rate:** 100.0%
- **Average Duration:** 85.96s
- **Total Iterations:** 18

## 📋 Benchmark Results Table

| Repository | Status | Iterations | Duration (s) | Guarded Tool Calls | Error / Notes |
|:---|:---:|:---:|:---:|:---|:---|
| `01_inventory_manager` | ✅ PASSED | 2 | 72.17 | list_directory: 2 | - |
| `02_string_utils` | ✅ PASSED | 4 | 189.54 | list_directory: 3 | - |
| `03_bank_account` | ✅ PASSED | 2 | 71.63 | list_directory: 2 | - |
| `04_linked_list` | ✅ PASSED | 1 | 173.19 | list_directory: 2 | - |
| `05_todo_manager` | ✅ PASSED | 3 | 135.54 | list_directory: 3 | - |
| `06_calculator` | ✅ PASSED | 1 | 38.15 | list_directory: 1 | - |
| `07_matrix_ops` | ✅ PASSED | 1 | 58.39 | list_directory: 1 | - |
| `08_password_validator` | ✅ PASSED | 1 | 40.52 | list_directory: 1 | - |
| `09_event_scheduler` | ✅ PASSED | 2 | 52.23 | list_directory: 2 | - |
| `10_shopping_cart` | ✅ PASSED | 1 | 28.22 | list_directory: 1 | - |

> **Note on Tool Calls:** The *Guarded Tool Calls* metric only covers tools routed through the
> `PermissionHarness` confirmation gate (such as `execute_command`, `create_directory`, `move_file`,
> `delete_file`, `list_directory`, and MCP tools). Native operations like `read_file`, `write_file`,
> and `grep` operate directly in the agent backend and are excluded from this count.
