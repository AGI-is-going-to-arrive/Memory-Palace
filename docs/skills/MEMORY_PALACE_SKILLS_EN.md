# Memory Palace Skills Design and Maintenance Specification

This document is no longer just a "strategy description for humans"; it is the maintenance baseline for the `memory-palace` skill system.

The Single Source of Truth (SSoT) is located at:

```text
docs/skills/memory-palace/
├── SKILL.md
├── references/
│   ├── mcp-workflow.md
│   └── trigger-samples.md
├── agents/
│   └── openai.yaml
└── variants/
    ├── antigravity/
    │   └── global_workflows/
    │       └── memory-palace.md
    └── gemini/
        └── SKILL.md
```

The distribution script is located at:

```text
scripts/sync_memory_palace_skill.py
```

The installation script is located at:

```text
scripts/install_skill.py
```

## 0. Alignment with Claude Skills Specification (2026-03-07)

This round of alignment was conducted against the official `Claude Code` skills documentation, the `Improving skill-creator: Test, measure, and refine Agent Skills` released by Anthropic on 2026-03-03, and the `skill-creator` in the `anthropics/skills` repository.

The current conclusions are as follows:

- **Structure Aligned**: Adopted the standard `skill-name/SKILL.md` bundle structure; the directory name and `name` are both `memory-palace`.
- **Trigger Contract Aligned**: The `description` clearly states both "what it does" and "when to use it," while retaining explicit trigger hints.
- **Progressive Loading Aligned**: The main `SKILL.md` is kept concise, with tool details offloaded to `references/`.
- **Cross-client Distribution Aligned**: Claude / Codex / OpenCode use mirrors; Gemini retains a variant. The current repository can directly use the project-level entry, though `user-scope install` is still prioritized for cross-repo reuse.

However, compared to the full `skill-creator` workflow, there is one clear boundary:

- **The validation layer is not yet a full eval / benchmark suite.**

The following validation entries have been organized in the repository:

- `docs/skills/memory-palace/` canonical bundle
- `scripts/sync_memory_palace_skill.py`
- `scripts/install_skill.py`
- trigger smoke
- mirror drift check
- live MCP e2e
- cross-client MCP binding check

Hidden mirrors and workspace configs such as `.claude/.codex/.opencode/.cursor/.agent/.gemini/.mcp.json` are generated only after you run sync/install locally. They are not part of the public GitHub repository by default.

But it hasn't fully implemented the `skill-creator` style of:

- `evals.json`
- blind comparator
- benchmark viewer
- automatic description optimization loop

Therefore, a more accurate statement is:

- **The current skill design complies with the structural and trigger specifications of Claude Skills.**
- **The current validation method leans toward engineering smoke / e2e and has not yet completed the full skill-creator evaluation workflow.**

## 1. Why Converge Like This

The core issue with the old design was not "too little information," but fragmented structure:

- There were strategy documents, but no canonical skill bundle distributable within the repo.
- Multi-CLI directories relied on manual maintenance, making them prone to drift.
- The loop of "when to trigger," "how to execute," and "how to verify" was not closed.

The current design goals are:

- **Distributable**: The canonical bundle is fixed at `docs/skills/memory-palace/`.
- **Cross-CLI Compatible**: Mirrors for `.claude`, `.codex`, `.opencode`, `.cursor`, and `.agent` are generated locally via the sync script. Gemini can use the project-level entry after running workspace installation in the current workspace, but `user-scope install` is still preferred for cross-repo use.
- **Verifiable**: Continuous validation via `sync_memory_palace_skill.py --check` and repository gates.
- **Iterative**: Optimize the trigger quality of the `description` first, then optimize the `SKILL.md` body and references.

For Gemini, there is a known boundary:

- Workspace-local `.gemini/skills/...` can be discovered.
- However, during actual triggering, Gemini might attempt to read the hidden skill directory directly.
- Under certain local policies, this might be blocked by ignore patterns.

Therefore, the current recommendation is two-layered:

- **More reliable by default**: First run `python scripts/install_skill.py --targets gemini --scope user --with-mcp --force`.
- **If you also want a project-level entry in the current workspace**: Then add workspace installation to generate `.gemini/skills/...` + `.gemini/settings.json`.
- **For cross-repo reuse / copying to other workspaces**: Still prioritize `user-scope install`.

Public communication suggestion:

- You can say "the workspace entry is in place."
- It is not recommended to state "Gemini is fully ready out-of-the-box."

## 2. Directory Responsibilities

### `docs/skills/memory-palace/SKILL.md`

Responsible for:

- Defining when to trigger.
- Providing the shortest yet safest default process.
- Specifying which cases must be checked first and cannot be written blindly.

### `docs/skills/memory-palace/variants/gemini/SKILL.md`

Responsible for:

- Providing Gemini with a shorter, stronger-triggering skill body.
- Hardcoding the first move, `NOOP` handling, and trigger sample paths as anchors.
- Reducing under-triggering and irrelevant responses from Gemini on skill introspection.

### `docs/skills/memory-palace/references/mcp-workflow.md`

Responsible for:

- Maintaining the minimum safe workflow for the 9 MCP tools.
- Recording the safe sequence for recall / write / compact / rebuild.
- Providing examples of "should trigger" vs "should not trigger."

### `docs/skills/memory-palace/references/trigger-samples.md`

Responsible for:

- Providing a stable set of should-trigger / should-not-trigger / borderline prompts.
- Ensuring `description` optimization has a fixed control group rather than relying on intuition.
- Leaving a unified input set for subsequent trigger regression / human review.

### `scripts/sync_memory_palace_skill.py`

Responsible for:

- Distributing the canonical bundle to various CLI directories.
- Checking for drift in mirrors.
- Current workspace mirrors include `.claude`, `.codex`, `.opencode`, `.cursor`, `.agent`, and `.gemini`.
- If `--check` reports drift, run a sync first, then re-run `evaluate_memory_palace_skill.py`.
- If only `claude(user)` binding fails, prioritize supplementing the project-scoped `mcpServers.memory-palace` in `~/.claude.json` for the current project, rather than modifying project blocks of sibling repos.

### `scripts/install_skill.py`

Responsible for:

- Installing the canonical bundle to other workspaces or user directories.
- Supporting `copy` / `symlink`.
- Populating `--with-mcp` CLI configs when needed, while still binding MCP to the **current checkout** via `scripts/run_memory_palace_mcp_stdio.sh`.
- For Gemini, this is currently the more reliable recommended installation path.
- When the target is Gemini, it automatically replaces the content with `variants/gemini/SKILL.md`.

## 3. Design Principles

1. The `description` is the **trigger contract**.
2. The `SKILL.md` body retains only the **execution steps, hard constraints, and failure handling**.
3. Tool details are offloaded to `references/`.
4. Distribution and validation are handled by repository scripts, removing the need for users to manually copy skills.
5. Runtime references prioritize **repo-visible canonical docs/skills paths**; do not depend on the readability of hidden mirror directories.
6. Don't just check "if the skill can be found"; also check "if the corresponding MCP is actually bound to the current project."

## 4. Default Workflow

### Boot

Before the first real operation:

```python
read_memory("system://boot")
```

### Recall

When the URI is uncertain:

```python
search_memory(query="...", include_session=True)
```

### Read before write

Read the target or candidate target before the following operations:

- `create_memory`
- `update_memory`
- `delete_memory`
- `add_alias`

Default recommendation:

- Prioritize providing an explicit `title` for `create_memory` when creating.
- Use the `update_memory` patch for standard updates.
- Use `append` only when truly appending new content to the end.

### Guard-aware write

Do not ignore these fields:

- `guard_action`
- `guard_reason`
- `guard_method`
- `guard_target_uri`
- `guard_target_id`

Recommended rules:

- `NOOP` → Stop writing; inspect `guard_target_uri` / `guard_target_id`, and read the suggested target before deciding whether anything should change.
- `UPDATE` → Prioritize changing to `update_memory`.
- `DELETE` → Confirm the old memory should indeed be replaced.

### Compact / Recover

- Long sessions, high noise → `compact_context(force=false)`.
- Retrieval degradation → `index_status()`, and if necessary, `rebuild_index(wait=true)`.

## 5. Trigger Design Requirements

The `description` of the new `memory-palace` skill must cover these trigger signals:

- User explicitly mentions memory / remember / recall / long-term memory.
- User uses Chinese terms like “记住” (remember), “回忆” (recall), “长期记忆” (long-term memory), “跨会话” (cross-session), “压缩上下文” (compact context), “重建索引” (rebuild index).
- User mentions `system://boot`.
- User mentions `search_memory` / `compact_context` / `rebuild_index`.
- User asks whether to use `create` or `update`.
- User performs maintenance, rollback, or index recovery actions.

Also, define boundaries:

- Not for general README / UI / benchmark / general code implementation tasks.
- Not for generalized "skill design" tasks unrelated to the Memory Palace MCP.

## 6. Test / Measure / Refine

This time, it's not just about making the skill longer, but about completing the maintenance loop:

1. Adjust the `description` first.
2. Then adjust the `SKILL.md` body.
3. Run `python scripts/sync_memory_palace_skill.py --check`.
4. Then run `python scripts/evaluate_memory_palace_skill.py`.
5. Then run `cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py`.
6. Then run `bash scripts/pre_publish_check.sh`.
7. Expand `references/` only when absolutely necessary.

### Should trigger

- "Help me write this user preference into Memory Palace."
- "Read from `system://boot` first, then search for recent memories of this type."
- "This memory might be duplicated; help me decide whether to update or create."
- "Search performance has dropped recently; help me see if I should `rebuild_index`."
- "I want to clean up a long session and compact it into notes."

### Should not trigger

- "Rewrite the README for me."
- "Fix the frontend button style."
- "Help me analyze the benchmark results."
- "Update docs/skills text that is unrelated to Memory Palace."

### Specific Role of Sample Sets

The role of `references/trigger-samples.md` is not "just another document," but to enable you to stably answer four questions:

1. Does this skill **fail to trigger when it should**?
2. Does this skill **trigger randomly when it shouldn't**?
3. After triggering, is its **first action correct**?
4. After changing the `description`, is the effect actually better or worse?

Without this set of samples, every subsequent `description` adjustment can only rely on temporary intuition, easily leading to:

- Sacrificing legitimate hits to reduce false triggers.
- Pulling in general docs / coding tasks to expand triggering.
- Triggering correctly but failing the first step (e.g., not performing `boot` / `search before write`).

`evaluate_memory_palace_skill.py` solidifies these samples and actual CLI smoke tests into a repeatable regression entry to answer:

- Are mirrors still consistent?
- Is YAML/frontmatter still valid?
- Are Claude / Codex / OpenCode / Gemini passing, partially passing, or failing?
- Is the current regression result better than the last one?

`evaluate_memory_palace_mcp_e2e.py` further answers another critical layer:

- Beyond skill rules, can the real MCP stdio call chain succeed?
- Do the 9 tools return as designed on an isolated database?
- Do key behaviors like `write_guard NOOP`, `add_alias`, and `rebuild_index(wait=true)` align with the project design?
- Does the `runtime-index-worker` still have latent cross-event-loop bugs?

## 7. Gemini Compatibility Notes

A real compatibility boundary has appeared in recent validations:

- The CLI can discover and load the `memory-palace` skill.
- However, the runtime file reading strategy might ignore hidden mirror directories (e.g., `.gemini/skills/...`).

Therefore, the canonical `SKILL.md` now uniformly requires:

- Prioritize opening `docs/skills/memory-palace/...` when referencing files.
- Do not treat hidden mirror paths as default reference paths.

The benefits of doing this are:

- Claude / Codex / OpenCode remain usable.
- Clients like Gemini / Cursor, which are more conservative about hidden directories, are more likely to get consistent results.

If a more reliable smoke test for Gemini CLI is needed, the current more dependable call method is:

```bash
gemini -m gemini-3-flash-preview \
  -p '<your prompt>' \
  --output-format text \
  --allowed-tools activate_skill,read_file
```

Note: This is a **more stable empirical path from recent validations**, not a universal official guarantee for all Gemini versions.

## 8. Maintenance Boundaries

When continuing to optimize, maintain this sequence:

1. Adjust trigger description first.
2. Then adjust execution body.
3. Then adjust reference.
4. Finally, adjust sync scripts and gates.

Do not return to the old mode of "writing a long document first, then asking the user to manually copy it into a skill."
