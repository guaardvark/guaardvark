---
name: code
description: >-
  Code intelligence over repositories Guaardvark has indexed: semantic code search,
  exact-text search, file listing, PageRank repository map, import dependency graph,
  reading one class or function, analysis and generated rewrites, plus the System Mapper
  and self-improvement status. Use when the user asks where something is implemented, how
  modules depend on each other, or wants an architecture map of an indexed repo.
---

# Code intelligence with Guaardvark (MCP tools)

| tool | use |
|---|---|
| `list_code_repositories` | which folders are marked as repositories (gives the folder id) |
| `search_codebase` | semantic search over the indexed project: "where is thinking enabled per model" |
| `search_code` | case-insensitive regex across files |
| `list_code_files` | directory listing to orient |
| `read_code` | a whole file with line numbers |
| `read_ast_node` | one class or function by name from a Python file |
| `get_repository_map` | PageRank-ranked architectural map for a folder id |
| `get_dependency_graph` | file-level import graph for a folder id |
| `analyze_code` | structure, patterns, improvement notes for a file |
| `codegen` | a complete modified version of a file from instructions (writes a new file) |
| `verify_change` | confirm text now exists in a file after an edit |
| `map_codebase` | run the System Mapper: stats plus ranked findings |
| `self_improvement_status` | can the self-improvement engine run (lock, flag, already running) |
| `swarm_status` | the coding swarm (see the swarm skill) |

## Pattern

1. `list_code_repositories` → folder id.
2. `get_repository_map` for the shape, `search_codebase` for the question.
3. Read the exact code (`read_code` / `read_ast_node`) before answering; quote file and line.
4. Edits: make them with your own editor tools in the user's checkout; `verify_change` afterwards.

## Rules

- These tools read Guaardvark's index, which can lag the working tree; when it matters, read the
  file from disk too.
- `map_codebase` and `self_improvement_status` are read-only here. Dispatching a fix to the
  self-improvement engine is done in the Studio, on purpose.
