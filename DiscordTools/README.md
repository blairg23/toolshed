# DiscordTools

A CLI tool for managing and cleaning up your Discord servers.

## Setup

### 1. Install dependencies

Requires [Poetry](https://python-poetry.org/docs/#installation).

```bash
poetry install
```

### 2. Get your Discord token

> ⚠️ Keep your token private — it grants full access to your Discord account. Never share it or commit it to git.

#### Option A: Browser

1. Open Discord in your **browser** (discord.com)
2. Press `F12` (or `Ctrl+Shift+I`) to open DevTools
3. Go to the **Network** tab
4. Click any channel or server in Discord to trigger a request
5. Click any request in the list, then look at the **Request Headers**
6. Find the `Authorization` header — the value is your token

#### Option B: Desktop app (Windows)

1. Fully close the Discord app (system tray → Quit)
2. Press `Win+R` and type `%appdata%/discord`, hit Enter
3. Open `settings.json` in a text editor
4. Add this line inside the JSON object (before the last `}`):
   ```json
   "DANGEROUS_ENABLE_DEVTOOLS_ONLY_ENABLE_IF_YOU_KNOW_WHAT_YOURE_DOING": true
   ```
5. Save the file and relaunch Discord
6. Press `Ctrl+Shift+I` to open DevTools
7. Follow the same Network tab steps as Option A above

### 3. Create a `.env` file

In `DiscordTools/` (the directory you run the command from -- `load_dotenv()`
searches the current working directory, not the script's own location),
create a file called `.env`:

```
DISCORD_TOKEN=your_token_here
```

> Make sure `.env` is in your `.gitignore` if you ever put this in a repo.

---

## Usage

```bash
poetry run python src/discord_tools.py
```

### Commands

| Command | Description |
|---|---|
| `fetch` | Load servers from cache (hits API if no cache exists) |
| `fetch --reload` | Force fresh API call and update cache |
| `counts` | Load member counts from cache (hits API if no cache exists) |
| `counts --reload` | Re-fetch all member counts and update cache |
| `list` | Show current view |
| `list 1-50` | Show a range of servers |
| `stats` | Summary stats (total, owned, oldest, newest, avg age, member counts) |
| `sort age` | Sort by age, oldest first (default) |
| `sort members` | Sort by member count, smallest first |
| `sort name` | Sort alphabetically |
| `filter owned` | Show only servers you own |
| `filter small` | Show servers with <50 members |
| `filter small 100` | Show servers with <100 members |
| `filter old` | Show servers older than 3 years |
| `filter old 5` | Show servers older than 5 years |
| `filter ffxiv` | Text search by name |
| `filter clear` | Reset filter, show all servers |
| `leave` | Interactively select servers to leave |
| `help` | Show command reference |
| `quit` | Exit |

### Typical workflow

```
discord> fetch
discord> counts
discord> stats
discord> sort age
discord> filter small 30
discord> list
discord> leave
```

### Leaving servers

When you run `leave`, you'll be prompted to enter server numbers (from the current view) as a comma-separated list. Ranges are supported:

```
> 2, 5, 7-10, 14
```

Servers you **own** are automatically skipped — you'll need to either transfer ownership or delete those manually in Discord.

Type `yes` to confirm before any servers are left.

### Caching

Member counts take a while to fetch (~0.3s per server). Results are cached in `discord_tools_cache.json` in the same directory. On subsequent runs, `fetch` and `counts` load from cache instantly.

Use `--reload` to force a fresh API call when you want up-to-date data.

---

## Notes

- This tool uses your **user token**, not a bot token. Discord's ToS restricts user token automation, so use this for personal cleanup only.
- The Discord API caps guild list requests at 200 per page — pagination is handled automatically.
- Rate limits are handled automatically with retries.