# Memory Palace Dashboard User Guide (English)

> This guide mirrors the **English interface** of the Memory Palace Dashboard.
>
> If your interface is currently in Chinese, click the language toggle in the top-right corner to switch to English. Your preference is saved automatically.

---

## Table of Contents

- [🌐 General Operations](#-general-operations)
- [📂 Memory Page](#-memory-page)
- [📋 Review Page](#-review-page)
- [🔧 Maintenance Page](#-maintenance-page)
- [📊 Observability Page](#-observability-page)
- [❓ FAQ](#-faq)

---

## 🌐 General Operations

### 🧭 Top Navigation Bar

The top of the page has four tabs. Click any tab to switch pages:

| Tab | Page | What It Does |
|-----|------|-------------|
| **Memory** | Memory Page | Browse, create, and edit memories |
| **Review** | Review Page | View change history and decide whether to keep or rollback modifications |
| **Maintenance** | Maintenance Page | Clean up old or unused memories |
| **Observability** | Observability Page | Monitor system health and search performance |

### 🔑 Setting an API Key

In the top-right corner, you may see one of these states:

- **Set API key**: no stored key is configured yet
- **Update API key / Clear key**: a local key is already stored in the browser
- **Runtime key active**: the page received a runtime-injected key, so manual entry is usually not needed and the manual key buttons are hidden

If the Dashboard shell opens but protected data does not load, the usual fix is:

**Set API key** → enter the `MCP_API_KEY` value from your `.env` file → confirm.

> If you set `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` for local development, protected data can load automatically without entering a key, as long as the request is a direct loopback request.

### 🌍 Language Toggle

A language toggle button is in the top-right corner. Click it to switch between English and Chinese. Your preference is remembered by the browser.

---

## 📂 Memory Page

> **What is this page for?** Browse and manage all your memory content. You can view existing memories, create new ones, edit content and metadata, or delete memories you no longer need.

### 🗂️ Page Layout

The page is divided into two areas:

- **Left side — Conversation Vault**: Create new memories from dialogue
- **Right side — Current Node Content + Child Memories list**: View and edit existing memories

### 🧭 Breadcrumb Navigation (top of page)

The breadcrumb bar at the top shows your current location, for example: `root > core > agent > preferences`.

- Click any level in the path to jump directly to that node
- **root** is the top-level node — it does not store memory content itself

### ✍️ Left Side: Conversation Vault

This is where you create new memories.

#### 📝 Field Descriptions

| Field | Placeholder Text | Description |
|-------|-----------------|-------------|
| **Memory title** | `Memory title (optional)` | A short name for this memory. Optional but recommended — makes it easier to find later. |
| **Conversation** | Large text area (with grey sample text) | Paste the LLM / Agent dialogue you want to save. **This field is required.** |
| **Priority** | `Priority` | Enter a number. Higher numbers = more important. Higher-priority memories rank higher in search results. Leave blank for the default value. |
| **Disclosure** | `Disclosure` | A text description controlling when this memory should be surfaced to an Agent. For example: `"Only show when the user explicitly asks"`. Optional — leave blank for no restriction. |

> **What is "Disclosure"?** Think of it as a visibility rule you write in plain language. It tells the system under what conditions this memory should be shown to the AI. For example, if you store a sensitive preference, you might write "only surface when the user asks about preferences." If you're unsure, just leave it blank.

#### ✅ Step-by-Step: Creating a Memory

1. Paste your dialogue into the **Conversation** text area
2. (Optional) Fill in **Memory title**, **Priority**, and **Disclosure**
3. Click **Store Memory**
4. The system runs a Write Guard check first — it decides whether the content is worth saving
5. On success, a green notification appears at the bottom: `Memory created: core://xxx`

> **What is the Write Guard?** A safety mechanism that evaluates each write request before it is accepted. From the Dashboard side, the guaranteed visible result is whether the write proceeds or returns `Skipped: write_guard blocked ...`. The more specific backend reason still depends on the server-side decision result.

### 📄 Right Side, Upper: Current Node Content

Displays the full detail of the memory you're currently viewing.

- **Content editor**: Directly edit the memory body text
- **Priority**: Modify the priority number
- **Disclosure**: Modify the disclosure text
- **Save** button: Save your changes
- **Delete Path** button: Remove this memory's access path

> Clicking **Delete Path** brings up a confirmation dialog showing which path will be deleted. The deletion only happens after you confirm.

#### 📌 Gist vs. Original View

Each memory may have a system-generated summary (Gist). Use the **Gist** / **Original** toggle to switch between views:

- **Gist**: A compressed summary auto-generated by the system — useful for quick scanning
- **Original**: The full, unmodified memory content

### 📑 Right Side, Lower: Child Memories

Lists all child memories under the current node as cards.

#### 🔍 Child Filters

A filter toolbar sits above the child list:

| Filter | Placeholder Text | Description |
|--------|-----------------|-------------|
| **Search box** | `Search path / snippet` | Type keywords to quickly filter matching child memories by path and preview snippet |
| **Max priority** | `Max priority (optional)` | Enter a number — only shows memories with priority ≤ this value |

- Each child card shows the current priority, path/title, and snippet or gist preview
- Click any card to navigate into that memory node
- The child list is expanded in frontend batches — click **Load N more** at the bottom to reveal more of the children already loaded for the current node

> If you are editing the current node and then click a breadcrumb or a child card, the page asks whether you want to discard the unsaved edits first.

---

## 📋 Review Page

> **What is this page for?** Every time a memory is created, modified, or deleted, the system takes a "before" snapshot. This page shows you those changes so you can decide: keep the change, or roll it back.

### 🗂️ Page Layout

- **Left side — Review Ledger**: List of sessions and their snapshots
- **Right side — Diff View**: Detailed before/after comparison of a selected snapshot

### 📖 Left Side: Review Ledger

#### 🎯 Target Session

A dropdown at the top labeled **Target Session**. Each batch of AI-driven modifications is grouped into a session.

- If it shows **No active sessions**, there are no pending changes to review
- Select a session to see all modification snapshots under it

#### 📸 Snapshot List

Each snapshot represents a single modification. The card shows:
- **Operation type**: Create / Content / Meta / Delete / Alias
- **Resource path**: The memory path that was modified (e.g., `core://agent/preferences`)

Click a snapshot to load its diff view on the right.

### 🔀 Right Side: Diff View

After selecting a snapshot, this area shows the before/after content comparison:

- **Red / strikethrough text**: Content that was removed
- **Green / highlighted text**: Content that was added
- **Metadata Shifts**: Shows changes to priority, disclosure, or other metadata fields

#### 🎛️ Action Buttons

| Button | Label | What It Does |
|--------|-------|-------------|
| **Integrate** | `Integrate` | Accept this change — clears the snapshot from the review queue |
| **Reject** | `Reject` | Roll back the memory to its state before this change was made |
| **Integrate All** | `Integrate All` | Accept all pending changes in the current session at once |

> **What does "Integrate" mean?** It's essentially "approve." You're confirming that you've reviewed this change and it's fine. The snapshot record is then cleaned up.

> **What happens when I "Reject"?** The memory is restored to its pre-modification state. Deleted content comes back; new content is removed. It's like undoing the change.

---

## 🔧 Maintenance Page

> **What is this page for?** Helps you clean up "garbage" memories. Over time, some memories become outdated, unreachable (no paths point to them), or decay in vitality. This page finds them and lets you safely delete them.

The subtitle reads: *Manage orphan memories and low-vitality cleanup candidates with human confirmation.*

### 📊 Summary Cards at the Top

| Card | Meaning |
|------|---------|
| **Deprecated** | Number of old versions left behind when memories were updated (like file revision history) |
| **Orphaned** | Number of memories that no path can reach (they exist in the database but have no "entrance") |
| **Low Vitality** | Number of memories whose vitality score has decayed to the point of being deletable |

### 🧹 Upper Section: Orphan Cleanup

> **What is an "orphan memory"?** In Memory Palace, each memory is accessed via a path (e.g., `core://agent/preferences`). If that path is deleted but the memory record still exists in the database, it becomes an "orphan" — it's there but unreachable.

This section lists all orphan memories in two categories:

- **Deprecated Versions**: Old history copies left behind by update operations
- **Orphaned Memories**: Records with zero paths pointing to them

Each card can be expanded to view full content. Diff details only appear when the item is a deprecated version with a `migration_target`.

#### ✅ Step-by-Step: Cleaning Up Orphans

1. Click **Refresh** to scan for the latest orphan memories
2. Select the memories you want to delete (or use the select-all control in the section header)
3. Click **Delete N orphans**
4. Confirm the deletion in the popup dialog

> ⚠️ Deletion is permanent and cannot be undone. Review each item carefully before confirming.

### 💚 Lower Section: Vitality Cleanup Candidates

> **What is "vitality"?** Every memory has a vitality score representing how "active" it is. Newly created memories start with high vitality. Over time, the score naturally decays. If a memory hasn't been accessed for a long time, its vitality drops very low and the system marks it as a cleanup candidate.

#### 🔍 Filter Fields

| Field | Label | Description |
|-------|-------|-------------|
| **Threshold** | `Threshold` | The vitality score cutoff. The system finds memories with vitality below this number. Lower = stricter filter. |
| **Inactive days** | `Inactive days` | How many days a memory must have been unaccessed to qualify. E.g., `14` means only memories untouched for 14+ days. |
| **Limit** | `Limit` | Maximum number of candidates to display. Range: 1–500. |
| **Domain** | `Domain` | Only show candidates from a specific domain (e.g., `core`, `notes`). Leave blank for all. |
| **Path prefix** | `Path prefix` | Only show candidates whose path starts with this prefix. E.g., `agent/`. |
| **Reviewer** | `Reviewer` | Records who initiated this cleanup review. Optional. The default value is `maintenance_dashboard`. |

#### ✅ Step-by-Step: Vitality Cleanup

1. Fill in filter conditions and click **Apply Filters**
2. Review the candidate list below. Each card shows:
   - `vitality N.NN`: Current vitality score
   - `inactive Nd`: How many days since last access
   - `deletable` or `active paths`: Whether it can be safely deleted
3. Select the memories you want to act on
4. Choose an action:
   - **Prepare Delete (N)**: Mark selected memories for deletion
   - **Prepare Keep (N)**: Mark selected memories to be kept (not deleted)
5. Click **Confirm delete** or **Confirm keep** — the system will ask you to type a confirmation phrase (a safety measure to prevent accidental deletion)
6. Type the exact confirmation phrase to execute the action

> **What does "Run Decay + Refresh" do?** Manually triggers a vitality decay recalculation. The system does this automatically, but click here if you want to see the latest decay results immediately.

---

## 📊 Observability Page

> **What is this page for?** This is the system's "health report." It shows search engine performance, runtime status, and background job states. **Regular users don't need this page day-to-day** — it's mainly useful for troubleshooting.

The subtitle reads: *Track search latency, degrade reasons, cache hits, and index worker health.*

### 📊 Summary Cards at the Top

| Card | Meaning |
|------|---------|
| **Queries** | Total number of search requests the system has handled |
| **Latency** | Average search response time in milliseconds — lower is better |
| **Cache Hit Ratio** | Percentage of searches served from cache — higher is better (means repeated queries don't need recalculation) |
| **Index Latency** | Average time for index operations |
| **Cleanup p95** | 95th percentile cleanup time (the "worst-case" speed for cleanup operations) |
| **Cleanup Index Hit** | Hit rate for index lookups during cleanup operations |

### 🎛️ Top Action Buttons

| Button | Label | Description |
|--------|-------|-------------|
| **Refresh** | `Refresh` | Reload all statistics on the page |
| **Rebuild Index** | `Rebuild Index` | Submit a full index rebuild job request. Use when search quality has noticeably degraded. |
| **Sleep Consolidation** | `Sleep Consolidation` | Submit a memory consolidation job request. |

> **What is "Rebuild Index"?** It submits a rebuild request to the backend. Use it when the index may be out of date or search quality looks wrong. Think of it as asking the backend to rebuild the table of contents.

> **What is "Sleep Consolidation"?** It submits a consolidation job request to the backend. Its goal is to help the system reorganize fragmented memory records, but the exact effect still depends on backend policy and current runtime state.

### 🔎 Search Console

A diagnostic tool that lets you run a test search to evaluate retrieval quality.

#### 📝 Field Descriptions

| Field | Label | Description |
|-------|-------|-------------|
| **Query** | `Query` | The keywords or question you want to search for |
| **Mode** | `Mode` | Search mode. Options: hybrid (recommended — combines keyword + semantic), semantic (meaning-based), keyword (exact match) |
| **Session Id** | `Session Id` | (Optional) If filled, results from this session are prioritized |
| **Max Results** | `Max Results` | Maximum number of results to return |
| **Candidate x** | `Candidate x` | Internal candidate multiplier. Higher = broader search but slower. Default is usually fine. |
| **session-first** | `session-first` | Check this to prioritize memories from the current session in results |
| **Domain filter** | `Domain filter` | (Optional) Restrict search to a specific domain, e.g., `core` |
| **Path prefix filter** | `Path prefix filter` | (Optional) Only search under a path prefix |
| **Scope hint** | `Scope hint` | (Optional) A hint to guide the search engine's scope. E.g., `core://agent` |
| **Max priority filter** | `Max priority filter` | (Optional) Only return memories with priority ≤ this value |

Click **Run Diagnostic Search** to execute. Results and diagnostics appear below.

### 🩺 Search Diagnostics

After running a search, this section shows:

- **latency**: How many milliseconds the search took
- **mode**: The actual retrieval mode used
- **intent**: The query intent or applied intent label returned by the backend
- **strategy**: Which search strategy was selected
- **degraded**: Whether the backend marked this search as degraded; if so, the specific reasons are shown here
- **Result list**: Each result shows its match score, content snippet, source path, and update metadata

### ⚙️ Runtime Snapshot

Displays the system's current operational status:
- Whether the index is healthy or degraded
- Queue depth (how many tasks are waiting)
- Last worker error message
- Sleep consolidation status

### 📋 Index Task Queue

Lists running or recently completed index tasks. Each task can be:
- **Inspect**: View task details
- **Cancel**: Stop a running task
- **Retry**: Re-execute a failed task

---

## ❓ FAQ

### Q: The page loads but shows no data?

This usually means **the API key hasn't been configured**. Click **Set API key** in the top-right corner and enter the `MCP_API_KEY` value from your `.env` file.

### Q: I clicked "Store Memory" and it says "Skipped"?

The Write Guard blocked this write request. From the Dashboard side, the exact backend reason is not always visible; modify the content and try again, or inspect the backend response if you need the detailed cause.

### Q: What number should I use for Priority?

There's no fixed range. A simple rule of thumb: use `0` for normal memories, `5` for important ones, and `10` for critical core memories. Higher number = more important.

### Q: Can I undo "Integrate" or "Reject"?

- **Integrate** clears the snapshot record, but the memory content itself is unchanged
- **Reject** rolls back the memory to its pre-change state; if you later decide the change was needed, you'll need to re-create it

### Q: Does vitality automatically recover?

No. Vitality only decays over time. However, each time a memory is accessed (through reads or search hits), the vitality receives a "refresh" that slows the decay rate.

### Q: When do I need to manually "Rebuild Index"?

When you notice that memories you know exist aren't showing up in search results. Under normal use, the index is maintained automatically.
