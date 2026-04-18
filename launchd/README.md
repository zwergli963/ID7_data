# launchd scheduling

Two **launch agents** run the collector every 5 min and the derivation every 15 min. "Launch agent" is macOS-speak for "background job tied to your user account" — it runs when you're logged in and stops when you log out. Perfect for a laptop-only hobby setup.

Both plists use **absolute paths** — launchd strips your shell's `$PATH` and other environment variables, so `python` or `./collect.py` wouldn't resolve. If you ever move the repo, update the paths in the plist files (they currently hard-code `/Users/tomhausemer/Coding/ID7_data`).

Logs go to `logs/collect.log` and `logs/derive.log` at the repo root — tail them when debugging.

## Install

```bash
# Copy the plists into the directory launchd watches for user agents.
cp launchd/com.id7data.*.plist ~/Library/LaunchAgents/

# Register them with launchd. `launchctl load` both enables the job and
# starts it (because we set RunAtLoad=true in the plists).
launchctl load ~/Library/LaunchAgents/com.id7data.collect.plist
launchctl load ~/Library/LaunchAgents/com.id7data.derive.plist
```

## Verify

```bash
# Both jobs should appear. PID column is '-' between runs, a number while running.
launchctl list | grep id7

# Tail the logs — you should see 'inserted' or 'skipped' lines every 5 min.
tail -f logs/collect.log

# Count rows over time.
sqlite3 data/id7.db "SELECT COUNT(*) FROM readings"
```

## Trigger a run immediately (handy for testing)

```bash
launchctl kickstart -k gui/$(id -u)/com.id7data.collect
launchctl kickstart -k gui/$(id -u)/com.id7data.derive
```

The `-k` flag kills any currently-running instance first, then starts a fresh one. `gui/$(id -u)/…` is launchd's modern syntax for "the job with this label, running under my user session".

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.id7data.collect.plist
launchctl unload ~/Library/LaunchAgents/com.id7data.derive.plist
rm ~/Library/LaunchAgents/com.id7data.collect.plist
rm ~/Library/LaunchAgents/com.id7data.derive.plist
```

## Troubleshooting

- **`Load failed: 5: Input/output error`** — usually a syntax error in the plist. Check with `plutil -lint ~/Library/LaunchAgents/com.id7data.collect.plist`.
- **Jobs appear but nothing gets written.** Tail `logs/collect.log`. If it's empty, launchd may not have permission to write; check `ls -la logs/`. If it has a Python traceback, fix the underlying error — `python collect.py` from a terminal is the fastest way to reproduce.
- **VW 2FA prompt on first run as an agent.** The tokenstore file (`.tokenstore`) is created on first interactive login. If you see auth errors in `collect.log`, run `.venv/bin/python collect.py` once from a terminal to complete 2FA, then let launchd take over.
