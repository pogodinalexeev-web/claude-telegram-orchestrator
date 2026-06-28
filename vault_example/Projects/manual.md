# Projects/ — active work

One folder per project. Each project has at least a `manual.md` (what it is + how its
pieces connect), and as it grows: `tasks.md`, `ideas.md`, `log.md`, `журнал/`
(a journal of dated documents).

Example project folder:

```
Projects/<project>/
  manual.md      # structure: what's in it + links + topic. No state, no chronicle.
  tasks.md       # roadmap, loops, queues. Closed items don't pile up.
  ideas.md       # take-or-not, reviewed weekly.
  log.md         # dated chronicle, append-only, newest on top.
  журнал/        # dated documents (research, forwards): YYYY-MM-DD <title>.md
  Resources/     # topic materials, live configs
```

Discipline (kept by the rules file and the hooks):
- `manual.md` holds **no state and no chronicle** — state lives in the root
  `status.md`, the chronicle in the project's `log.md`.
- One source of truth: a task that moves into a project is deleted from the root
  `Tasks/tasks.md`.
- Structural filenames in Latin (`manual.md`, `tasks.md`); content filenames can be in
  the owner's language.
