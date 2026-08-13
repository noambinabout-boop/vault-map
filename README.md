# vault-map

**A navigable map of your Obsidian vault for coding agents (MCP server).** Skeletons of
your notes — headings with line ranges, queryable frontmatter, outgoing links — so the
agent stops loading whole notes just to find its way around.

It is the markdown counterpart of [repo-map](https://github.com/noambinabout-boop/repo-map)
(which does the same for source code).

## Why

An agent that reads a 2 500-line note to answer one question burns ~49 000 tokens. With
`outline()` + `get_section()` it reads the table of contents and the one chapter it needs:
**~5 200 tokens — an 89 % cut**, measured on a real vault (`python _smoke.py <vault>`
reproduces the measurement on yours).

The point isn't only cost: an agent that can afford to look around actually looks around,
instead of answering from memory.

## Tools

| Tool | What it gives you |
|---|---|
| `vault_map()` | whole-vault map: folder, key frontmatter, H2 titles, outgoing links |
| `outline(note)` | one note's heading tree with line numbers — call this *before* reading |
| `get_section(note, title)` | the body of a single section, nothing else |
| `query(filter)` | filter notes by frontmatter without opening them (`status:draft, score>5`) |
| `grep_notes(pattern)` | regex search through note *text*, each hit located as `note › section (Ln)` |
| `index(path)` | (re)target the server at another vault / folder of `.md` files |

`query` also understands two pseudo-fields for finding a note by name: `title:<word>` and
`path:<folder>`.

Freshness is automatic: the map is rebuilt whenever a note changes (signature = aggregated
mtime + size), so an outline never lies. A stale outline is worse than an honest read.

## Requirements

Python 3.10+. One dependency (`mcp`) — the engine itself is pure stdlib.

## Install

```bash
git clone https://github.com/noambinabout-boop/vault-map.git
cd vault-map
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # macOS / Linux
```

Then register it with your agent. For **Claude Code**, from anywhere:

```bash
# Windows
claude mcp add vault-map --scope user -- C:\path\to\vault-map\.venv\Scripts\python.exe C:\path\to\vault-map\server.py

# macOS / Linux
claude mcp add vault-map --scope user -- /path/to/vault-map/.venv/bin/python /path/to/vault-map/server.py
```

Check with `/mcp` inside Claude Code. Any MCP-capable client works the same way — it is a
plain stdio server: `<python> server.py`.

### Point it at your vault

By default the server targets the **current working directory**. Two ways to change that:

- **Per call**: `index("/path/to/vault")` — retargets on the fly.
- **Always the same vault**, whatever project you launched the agent in: set
  `VAULT_MAP_TARGET`. In `~/.claude.json`, on the server entry:

```json
"vault-map": {
  "type": "stdio",
  "command": "C:\\path\\to\\vault-map\\.venv\\Scripts\\python.exe",
  "args": ["C:\\path\\to\\vault-map\\server.py"],
  "env": {
    "VAULT_MAP_TARGET": "C:\\path\\to\\your\\vault",
    "PYTHONIOENCODING": "utf-8"
  }
}
```

On Windows, `PYTHONIOENCODING: utf-8` avoids a crash when a note contains an emoji.

### Make the agent actually use it

Tools nobody calls save nothing. Put the reflex in your `CLAUDE.md` (or equivalent):

```markdown
To find your way around the vault, use vault-map, not Read:
1. vault_map() for the big picture, query(filter) to filter by frontmatter
2. outline(note) before opening any note
3. get_section(note, title) to read only the part that matters
4. grep_notes(pattern) to search by content
Raw Grep/Read: last resort only.
```

If your client defers tool schemas, mark the server `"alwaysLoad": true` so the tools are
there from the first message.

## Verify your install

```bash
python _handshake.py            # full MCP stdio round-trip
python _smoke.py <your-vault>   # builds the map and prints the token saving
```

## License

MIT
