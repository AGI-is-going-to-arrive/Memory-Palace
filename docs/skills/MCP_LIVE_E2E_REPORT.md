# Memory Palace Live MCP E2E Report

## Summary

| Check | Status | Summary |
|---|---|---|
| `tool_inventory` | `PASS` | stdio MCP 暴露 9 个工具 |
| `boot_empty` | `PASS` | 首次 boot 在空库中按设计返回空核心记忆 |
| `create_memory` | `PASS` | create_memory 成功并返回 guard_action=ADD |
| `write_guard_block` | `PASS` | 重复写入被 UPDATE 正确拦截 |
| `search_memory` | `PASS` | search_memory 返回预期记忆且未降级 |
| `read_memory` | `PASS` | read_memory 能读取刚创建的记忆 |
| `update_memory` | `PASS` | update_memory patch 模式成功 |
| `add_alias` | `PASS` | add_alias 成功，alias 可读 |
| `delete_alias_via_delete_memory` | `PASS` | 通过 `delete_memory` 删除 alias 后原始 core 路径仍保留 |
| `compact_context` | `PASS` | compact_context 可正常返回 |
| `index_status` | `PASS` | index_status 返回 runtime 状态 |
| `rebuild_index` | `PASS` | rebuild_index(wait=true) 任务成功 |
| `boot_after_write` | `PASS` | boot 在写入后能加载 core memory |
| `runtime_worker` | `PASS` | 未发现跨 event loop worker 异常 |

## Details

### tool_inventory

- Status: `PASS`
- Summary: stdio MCP 暴露 9 个工具

### boot_empty

- Status: `PASS`
- Summary: 首次 boot 在空库中按设计返回空核心记忆

### create_memory

- Status: `PASS`
- Summary: create_memory 成功并返回 guard_action=ADD

### write_guard_block

- Status: `PASS`
- Summary: 重复写入被 UPDATE 正确拦截

### search_memory

- Status: `PASS`
- Summary: search_memory 返回预期记忆且未降级

### read_memory

- Status: `PASS`
- Summary: read_memory 能读取刚创建的记忆

### update_memory

- Status: `PASS`
- Summary: update_memory patch 模式成功

### add_alias

- Status: `PASS`
- Summary: add_alias 成功，alias 可读

### delete_alias_via_delete_memory

- Status: `PASS`
- Summary: 通过 `delete_memory` 删除 alias 后原始 core 路径仍保留

### compact_context

- Status: `PASS`
- Summary: compact_context 可正常返回

### index_status

- Status: `PASS`
- Summary: index_status 返回 runtime 状态

### rebuild_index

- Status: `PASS`
- Summary: rebuild_index(wait=true) 任务成功

### boot_after_write

- Status: `PASS`
- Summary: boot 在写入后能加载 core memory

### runtime_worker

- Status: `PASS`
- Summary: 未发现跨 event loop worker 异常

## MCP Server stderr

```text
Processing request of type ListToolsRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
Processing request of type CallToolRequest
```
