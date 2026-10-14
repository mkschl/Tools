# migrate-layout-cli

One-off command that brings an agent-memory storage root to the current
layout: every project is one directory below `projects/`, holding `board.md`,
`notes/` and `decisions/`, and `notes/` at the top is for notes without a
project. See "Layout migration" in the `CLAUDE.md` of the project.

It handles two older layouts, one at a time:

- **type-first:** `kanban/`, `notes/` and `decisions/` at the root. Projects
  are moved into `projects/<new path>` as the mapping says.
- **projects at the root:** one directory per project (or folder of projects)
  directly at the top. They are moved into `projects/` unchanged; the mapping
  is not used.

It only moves files. Note and decision contents are never read or printed;
only boards are rewritten in the type-first case (their note links and, when
it still equals the old project name, their title). It can be deleted after
the migration.

## Usage

```bash
# dry run: prints the plan and every problem, changes nothing
uv run --project tools/migrate-layout-cli migrate-layout \
  --root "$AGENT_MEMORY_DIR" --mapping tools/migrate-layout-cli/mapping.toml

# real run: copies the root to <root>-backup-<timestamp> first, then migrates
uv run --project tools/migrate-layout-cli migrate-layout \
  --root "$AGENT_MEMORY_DIR" --mapping tools/migrate-layout-cli/mapping.toml --apply
```

The mapping is a TOML file with a `[projects]` table, old name on the left and
new path on the right. Projects missing from it keep their path.

## Refusals

The command stops before changing anything when:

- two projects would end up at the same path,
- a target path is also a folder for another target (a path must be a folder
  or a project, never both), so a name that is both in the old layout needs
  a mapping entry for its own files,
- a target already exists in the root,
- a target path is invalid or uses a reserved name (`board`, `notes`,
  `decisions`).

For projects at the root it also stops when `projects/` already holds
something, or when both older layouts are present at once.

After `--apply` it checks that the number of files is unchanged and reports
anything left behind in the old directories.
