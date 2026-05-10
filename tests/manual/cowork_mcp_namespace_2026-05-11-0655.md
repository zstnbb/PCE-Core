# Cowork MCP namespace enumeration — 2026-05-11 06:55 UTC+08:00

> **Captured**: from Claude Desktop MSIX, Cowork composer, single
> utterance prompt (see `Docs/research/2026-05-XX-cowork-recon-
> findings.md` Q0 § for the prompt and the architectural framing).
>
> **Why this file exists**: Q0 of the cowork RECON asked whether
> `mcp__mcp-registry__` exposes a `register` / `install` / `add_server`
> verb (which would have made L3f-Cowork capture achievable via
> official channel). This file is the canonical empirical answer —
> **Outcome B** in the Q0 decision tree (read-only registry; no
> registration verb).
>
> **Verbatim** from Cowork's response. Light formatting normalization
> only.

---

# MCP Namespace Tools

## `mcp__mcp-registry__`

1. **search_mcp_registry**
   - Input: `keywords` (array of strings, required)
   - Output: Registry search results with connected status
   - Description: Search for available connectors in the MCP registry by keywords

2. **list_connectors**
   - Input: `keywords` (array of strings, optional)
   - Output: Installed connectors as an interactive card
   - Description: List the user's installed connectors, optionally filtered by keywords

3. **suggest_connectors**
   - Input: `uuids` (array of strings, required), `keywords` (array of strings, required)
   - Output: Connector suggestion card with Connect buttons
   - Description: Display connector suggestions to the user with Connect/re-authenticate buttons

---

## `mcp__skills__`

1. **list_skills**
   - Input: `skill_names` (array, optional), `keywords` (array, optional), `context_label` (string, optional)
   - Output: Interactive widget showing installed skills with "Try it" buttons
   - Description: Render the user's installed slash-menu skills as an interactive widget

2. **suggest_skills**
   - Input: `keywords` (array, optional), `context_label` (string, optional)
   - Output: Interactive widget showing available skills to add
   - Description: Suggest standalone skills the user can add (org/shared/Anthropic skills not yet enabled)

---

## `mcp__scheduled-tasks__`

1. **create_scheduled_task**
   - Input: `taskId` (string, required), `prompt` (string, required), `description` (string, required), `cronExpression` (string, optional), `fireAt` (string, optional), `notifyOnCompletion` (boolean, optional)
   - Output: Scheduled task created with storage location and session tracking
   - Description: Create a new scheduled task that runs on a recurring schedule, runs once at a specific time, or is started manually

2. **list_scheduled_tasks**
   - Input: None
   - Output: List of all scheduled tasks with their state, schedule, and `nextRunAt`/`lastRunAt` timestamps
   - Description: List all scheduled tasks with their current state

3. **update_scheduled_task**
   - Input: `taskId` (string, required), `prompt` (string, optional), `description` (string, optional), `cronExpression` (string, optional), `fireAt` (string, optional), `enabled` (boolean, optional), `notifyOnCompletion` (boolean, optional)
   - Output: Updated task state
   - Description: Update an existing scheduled task (partial updates supported)

---

## `mcp__visualize__`

1. **read_me**
   - Input: `modules` (array of strings, optional), `platform` (string, optional: `"mobile"` / `"desktop"` / `"unknown"`)
   - Output: CSS variables, colors, typography, layout rules, and examples for visualization
   - Description: Return required context for `show_widget` (internal setup step for styling/theming)

2. **show_widget**
   - Input: `title` (string, required), `widget_code` (string: SVG or HTML, required), `loading_messages` (array of strings, required)
   - Output: Rendered visual content inline in chat
   - Description: Show visual content (SVG graphics, diagrams, charts, or interactive HTML widgets) inline

---

## `mcp__cowork__`

1. **create_artifact**
   - Input: `id` (string, required), `html_path` (string, required), `description` (string, optional), `mcp_tools` (array of tool names, optional)
   - Output: Persisted HTML artifact accessible across sessions
   - Description: Create a persisted HTML artifact that opens in the Cowork sidebar and survives across sessions

2. **update_artifact**
   - Input: `id` (string, required), `html_path` (string, required), `update_summary` (string, required), `description` (string, optional), `mcp_tools` (array of tool names, optional)
   - Output: Updated artifact
   - Description: Update an existing artifact with new HTML content

3. **list_artifacts**
   - Input: None
   - Output: List of all Cowork artifacts with `id`, `name`, `path`, `createdAt`, `updatedAt`
   - Description: List all Cowork artifacts in the manifest

4. **request_cowork_directory**
   - Input: `path` (string, optional)
   - Output: Folder mounted and accessible for file operations
   - Description: Request access to a directory on the user's computer (with optional native folder picker)

5. **read_widget_context**
   - Input: `tool_name` (string, required)
   - Output: Widget state from an embedded interactive widget
   - Description: Read context from an embedded interactive widget rendered in chat

6. **present_files**
   - Input: `files` (array of objects with `file_path`, required)
   - Output: Files displayed as interactive cards in chat
   - Description: Present files to the user with interactive cards in the chat

7. **allow_cowork_file_delete**
   - Input: `file_path` (string, required)
   - Output: Permission granted for file deletion
   - Description: Request permission to delete files in a directory (called when delete operation fails with "Operation not permitted")

---

## `mcp__plugins__`

1. **list_plugins**
   - Input: `keywords` (array, optional), `context_label` (string, optional)
   - Output: Interactive card showing installed plugins with drill-in detail
   - Description: Render the user's installed plugins as an interactive card with detail per plugin

2. **suggest_plugin_install**
   - Input: `contextLabel` (string, required), `plugins` (array of plugin objects, required)
   - Output: Inline plugin card with Add/Manage buttons
   - Description: Render an inline plugin card suggesting plugins for the user to install

3. **search_plugins**
   - Input: `userIntent` (string, required), `keywords` (array, optional), `includeInstalled` (boolean, optional)
   - Output: Matching plugins with matched capabilities and skills list
   - Description: Search for installable plugins matching the user's request and org-specific workflows

---

## Answer to the "register / install / add_server" question

**No**, the `mcp__mcp-registry__` namespace **does not expose any
'register', 'install', or 'add_server' verb** for attaching custom
MCP servers to Cowork at runtime.

The available tools are read-only discovery and suggestion functions:

- `search_mcp_registry` — searches existing registered servers
- `list_connectors` — lists already-installed connectors
- `suggest_connectors` — displays suggestions for connecting to existing servers

There is no programmatic mechanism to register a new custom MCP server
or attach it to Cowork through these tools. Custom MCP integration
appears to require a different setup mechanism outside of runtime tool
invocation.
