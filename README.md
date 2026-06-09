# Claude Usage Tracker

A lightweight macOS menu bar app that shows your Claude.ai usage at a glance — a live, colour-coded bar with your 5-hour and 7-day utilization, per-model limits, your plan, and the time until your next reset.

```
[▓▓▓▓▓▓▓░░░]  68%  ↺ 1h 42m
```

## What it shows

- **Live usage** — your real utilization, read straight from Claude.ai (5-hour and 7-day windows)
- **A slick colour-coded bar** — green → amber → red as you approach your limit
- **Per-model limits** — separate 7-day Opus and Sonnet usage (handy on Max/Team)
- **Your plan** — Free, Pro, Max (5×/20×), Team, or Team Premium, detected automatically
- **Dashboard** — your account, organisation, role, reset times, and a 21-day history chart
- **Auto-refresh** — quietly syncs every 60 seconds in the background
- **Private & secure** — your key is stored in the macOS Keychain and only ever sent to Claude.ai

---

## Quick install (one command)

Paste this into the **Terminal** app (`⌘ + Space`, type *Terminal*, press Return),
hit Return, and follow any prompts:

```bash
curl -fsSL https://raw.githubusercontent.com/jsplmns/claude-macos-statusbar/main/install_claude_tracker.sh | bash
```

It does everything for you — it even **installs Python automatically** if you don't
have it (macOS will ask for your login password for that one step), then downloads the
app and starts it. A gauge icon appears in your menu bar.

Then jump to **[Step 4 — Connect your Claude account](#step-4--connect-your-claude-account)**.

> Prefer to do it by hand, or curious what each step does? The step-by-step guide below
> walks through the same thing manually.

---

## Installation (step by step)

No coding needed — just follow these four steps. It takes about 5 minutes.

### Step 1 — Install Python (one-time)

The app needs Python 3.10 or newer. To check if you already have it, open the **Terminal**
app (press `⌘ + Space`, type *Terminal*, hit Return) and paste:

```bash
python3 --version
```

If it prints `Python 3.10` or higher, skip to Step 2. Otherwise:

1. Go to **[python.org/downloads](https://www.python.org/downloads/)**
2. Click the big yellow **Download Python** button
3. Open the downloaded `.pkg` file and click through the installer (Continue → Agree → Install)

### Step 2 — Download the app

1. At the top of this GitHub page, click the green **`< > Code`** button → **Download ZIP**
2. Open your **Downloads** folder and **double-click the ZIP** to unzip it
3. You'll get a folder called `claude-macos-statusbar`

### Step 3 — Run the installer

1. Open the **Terminal** app again (`⌘ + Space`, type *Terminal*, Return)
2. Type `bash` followed by a single space (don't forget the space):

   ```
   bash 
   ```
3. **Drag the file `install_claude_tracker.sh`** from the unzipped folder onto the Terminal
   window. Its location fills in automatically.
4. Press **Return**.
5. When it asks *"Launch automatically at every login?"*, type **`y`** and press Return.

That's it — a small **gauge icon appears in your menu bar** (top-right of your screen).

### Step 4 — Connect your Claude account

The app needs a one-time "session key" so it can read your usage. This part is a little fiddly
— take it slowly:

1. Open **[claude.ai](https://claude.ai)** in your browser and make sure you're logged in.
2. Open the browser's developer tools: press **`⌘ + ⌥ (Option) + I`**.
3. In the panel that opens, find the **Application** tab (in Chrome) or **Storage** tab (in Safari/Firefox).
   - In Safari you first need to enable the Develop menu: **Safari → Settings → Advanced → "Show features for web developers"**.
4. In the left sidebar, open **Cookies** → click **`https://claude.ai`**.
5. Find the row named **`sessionKey`** and copy its **Value** (a long string starting with `sk-ant-sid01…`).
6. Click the **gauge icon** in your menu bar → **Connect to Claude.ai…**, paste the key, and click **Connect**.
7. Confirm with **Touch ID** (or your Mac login password) when prompted.

Done! Your usage now updates automatically every minute.

---

## Using it

Click the menu bar icon to open the menu:

| Item | What it shows |
|---|---|
| **5h / 7d bars** | Colour-coded utilization for each window |
| **Opus 7d / Sonnet 7d** | Per-model weekly usage (when your plan has them) |
| **↺ Resets in …** | Countdown to your next 5-hour reset |
| **Last 3 days** | Mini bars of your recent daily peaks |
| **● Live — Xm ago** | Connection status and last sync time |
| **Dashboard** | Full account info, plan, reset times, and a 21-day history chart |
| **Connect / Reconnect…** | Enter or update your session key |
| **Disconnect** | Remove your saved key and stop syncing |
| **Quit** | Close the app (your key stays saved for next time) |

> **Tip:** **Quit** just closes the app — it reopens connected next time. **Disconnect** actually removes your saved key, so you'd have to paste it again.

---

## Updating

Download the latest ZIP (Step 2) and run the installer again (Step 3). Your connection is kept.

## Uninstalling

```bash
launchctl unload ~/Library/LaunchAgents/com.user.claude-tracker.plist
rm ~/Library/LaunchAgents/com.user.claude-tracker.plist
rm -rf ~/.claude_tracker_venv ~/.claude_tracker.json ~/.claude_usage_history.jsonl
```

To also remove the saved key from your Keychain: open **Keychain Access**, search for
**“Claude Usage Tracker”**, and delete the entry.

---

## Privacy & security

- Your session key is stored in the **macOS Keychain** (encrypted by your Mac), never in a plain text file.
- Saving the key is confirmed with **Touch ID** / your login password.
- The app talks **only to `claude.ai`** to read your usage. There is no other server, no analytics, no tracking.
- Everything runs locally on your Mac.

## Troubleshooting

| Problem | Fix |
|---|---|
| No icon appears after install | Make sure you have Python 3.10+ (Step 1), then re-run the installer. |
| `python3: command not found` | Install Python from [python.org](https://www.python.org/downloads/) (Step 1). |
| Says “Not connected” | Click the icon → **Connect to Claude.ai…** and paste your `sessionKey` (Step 4). |
| Stopped updating after a while | Session keys expire. Click **Reconnect…** and paste a fresh key. |
| Want to restart it | `launchctl kickstart -k gui/$(id -u)/com.user.claude-tracker` |

---

## Requirements

- macOS 12 or later
- Python 3.10+ (a framework build, e.g. from [python.org](https://www.python.org/downloads/))
- Dependencies (installed automatically by the script): `rumps`, `pyobjc-framework-Cocoa`, `pyobjc-framework-LocalAuthentication`

## Disclaimer

This is an **unofficial, community-built tool** and is not affiliated with or endorsed by
Anthropic. It uses undocumented internal Claude.ai endpoints that may change at any time
without notice. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
