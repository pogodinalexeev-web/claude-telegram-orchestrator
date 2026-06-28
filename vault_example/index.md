# index — map of the vault

The assistant works on top of an Obsidian-style vault: a tree of Markdown files that
holds projects, a knowledge library, and a capture funnel. The bot reads and writes
these files; `vault_rag/` indexes them for semantic search; `git` syncs them between
machines.

This folder is a **de-personalized skeleton** — structure and rules, no private
content.

## Top-level layout

| Path | What lives here |
| --- | --- |
| `index.md` | This map. |
| `status.md` | What's active right now — the single source of truth for state (open loops, current focus). |
| `inbox.md` | Single capture point. Everything dropped to the bot lands here first, in a canonical format, sorted later. |
| `Tasks/` | The funnel: `tasks.md` (concrete actions), `ideas.md` (take-or-not), plus the rules file. |
| `Projects/` | Active work — one folder per project, each with a `manual.md` (+ optional `tasks.md` / `log.md` / journal). |
| `Resources/` | The library: `atoms/` (zettelkasten notes), `attachments/`, `chat-logs/`, `glossaries/`, `_templates/`. |
| `Journal/` | Personal cycles: daily / weekly / monthly notes, and `log.md` (append-only session history). |
| `.claude/` | The assistant's own config: `skills/`, `hooks/`, `commands/`, `CLAUDE.md`. |
| `memory/` | Long-term memory the agent reads and writes across sessions. |

## How a capture flows

```
message to bot ──► inbox.md ──► (sorted) ──► Tasks / Projects / Resources / atoms
                     ▲                              │
                     └────── /process-inbox ────────┘
```

## The Telegram menu

The bot exposes an **inline navigation menu** in Telegram (buttons, not typed
commands): a daily brief, a project list, and per-project sub-menus that open the
right files. Tapping a project shows its status and recent log; the menu is driven by
callback handlers in `tg_bot.py` (`handle_brief_nav_callback`, `setup_bot_menu`). It's
how the owner navigates the vault from the phone without typing paths.
