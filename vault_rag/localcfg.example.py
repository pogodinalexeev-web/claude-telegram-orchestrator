"""Machine-specific config for vault_rag — copy to localcfg.py and edit.
localcfg.py is gitignored: each machine has its own (paths differ between
a laptop and a server). Keeping it out of git lets the rest of the code use
relative imports and stay identical everywhere.
"""

# Absolute path to the knowledge base (vault) root to index.
VAULT = "/path/to/your/vault"

# Where the agent's chat-session .jsonl files live (used by chatlog.py to
# rebuild the daily conversation log that also gets indexed).
SESSIONS_PROJ = "/path/to/claude/projects/<project-slug>"

# Suffix for the daily chat-log file, to tell two machines apart
# (e.g. "-mac" on one, "" on the other) so they don't overwrite each other.
CHATLOG_SUFFIX = ""

# Label for the assistant's replies in the rebuilt log.
CHATLOG_WHO = "Assistant"

# Label for the owner's messages in the rebuilt log.
OWNER_LABEL = "Owner"
