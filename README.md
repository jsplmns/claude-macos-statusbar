# Claude Usage Tracker

A lightweight macOS menu bar app that shows your Claude.ai usage at a glance — live utilization, both the 5-hour and 7-day windows, and the time until your next reset.

```
[▓▓▓▓▓▓▓░░░]  68%  1h 42m
```

## Features

- **Live usage** — reads your actual utilization directly from Claude.ai (5h and 7d windows)
- **Auto-refresh** — syncs every 60 seconds in the background
- **Minimal UI** — lives quietly in the menu bar, out of the way
- **No dependencies on xbar or BitBar** — pure native macOS via `rumps`

## Requirements

- macOS 12 or later
- Python 3.10+
- The following Python packages:

```bash
pip install rumps pyobjc-framework-Cocoa
```

## Installation

### Quick install (recommended)

```bash
git clone https://github.com/jonathanskudlik/claude-macos-statusbar.git
cd claude-macos-statusbar
bash install_claude_tracker.sh
```

The install script sets up a LaunchAgent so the app starts automatically on login.

### Manual run

```bash
python3 claude_tracker.py
```

## Connecting to Claude.ai

The app reads usage data from the Claude.ai web app's internal API using your session cookie.

1. Open [claude.ai](https://claude.ai) in your browser
2. Press `Cmd+Option+I` to open DevTools
3. Go to **Application** → **Cookies** → `claude.ai`
4. Find `sessionKey` and copy its full value (starts with `sk-ant-sid01…`)
5. Click the menu bar icon → **Connect to Claude.ai…** and paste the key

Your session key is stored locally in `~/.claude_tracker.json` and is never transmitted anywhere except to `claude.ai` to fetch your usage.

## Menu

| Item | Description |
|---|---|
| Progress bar | Visual utilization for the current 5h window |
| `5h · X%  ·  7d · Y%` | Utilization for both rate-limit windows |
| `↺  Xh Ym` | Countdown to the next 5h window reset |
| `●  Live  —  Xm ago` | Connection status and last sync time |
| **Connect / Reconnect…** | Enter or update your session key |
| **Disconnect** | Remove the stored session key |
| **Open Claude.ai ↗** | Open Claude.ai in your default browser |

## Status indicators

| Status bar | Meaning |
|---|---|
| Pill bar (light fill) | Under 65% utilized |
| Pill bar (medium fill) | 65–89% utilized |
| Pill bar (full fill) | 90%+ utilized |
| `●  Live` | Connected and syncing |
| `○  Not connected` | No session key stored |
| `●  Error` | Connected but usage endpoint unavailable |

## Support

If you find this useful, consider buying me a coffee ☕

**[buymeacoffee.com/tinytoolkit](https://buymeacoffee.com/tinytoolkit)**

## Disclaimer

This is an **unofficial, community-built tool** and is not affiliated with or endorsed by Anthropic. It uses undocumented internal Claude.ai endpoints that may change at any time without notice. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
