#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Claude AI Usage Tracker – Installer & Launcher
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SCRIPT="$SCRIPT_DIR/claude_tracker.py"
VENV_DIR="$HOME/.claude_tracker_venv"      # isolated venv – avoids PEP 668
PLIST_LABEL="com.user.claude-tracker"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_OUT="$HOME/Library/Logs/claude-tracker.log"
LOG_ERR="$HOME/Library/Logs/claude-tracker-error.log"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Claude AI Usage Tracker – Installer        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Verify the app script exists ──────────────────────────────────────────────
if [ ! -f "$APP_SCRIPT" ]; then
    echo "❌  App script not found: $APP_SCRIPT"
    echo "   Make sure claude_tracker.py is in the same folder as this script."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Find a base Python that is (a) 3.8+ and (b) a macOS framework build.
# rumps uses PyObjC / AppKit which refuses to run without a framework Python.
# We use it only to CREATE the venv; pip installs go inside the venv so
# PEP 668 "externally managed environment" errors never apply.
# ─────────────────────────────────────────────────────────────────────────────
find_base_python() {
    local CANDIDATES=(
        /opt/homebrew/bin/python3            # Homebrew Apple Silicon
        /opt/homebrew/opt/python@3.13/bin/python3.13
        /opt/homebrew/opt/python@3.12/bin/python3.12
        /opt/homebrew/opt/python@3.11/bin/python3.11
        /opt/homebrew/opt/python@3.10/bin/python3.10
        /usr/local/bin/python3               # Homebrew Intel
        /usr/local/opt/python@3.12/bin/python3.12
        /usr/local/opt/python@3.11/bin/python3.11
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
        /usr/bin/python3                     # Apple system Python
        python3
    )
    for PY in "${CANDIDATES[@]}"; do
        command -v "$PY" &>/dev/null 2>&1 || continue
        # Must be 3.8+
        VER=$("$PY" -c "import sys; print(sys.version_info >= (3,8))" 2>/dev/null || echo False)
        [ "$VER" = "True" ] || continue
        # Must be a framework build (AppKit requirement)
        FW=$("$PY" -c "import sysconfig; print(bool(sysconfig.get_config_var('PYTHONFRAMEWORK')))" 2>/dev/null || echo False)
        [ "$FW" = "True" ] || continue
        echo "$PY"; return 0
    done
    return 1
}

echo "🔍  Looking for a compatible (framework) Python 3..."
BASE_PYTHON=$(find_base_python || true)

if [ -z "$BASE_PYTHON" ]; then
    echo ""
    echo "❌  No framework Python found on this machine."
    echo ""
    echo "   rumps needs a 'framework' Python to draw macOS menu bar icons."
    echo "   Homebrew Python qualifies — make sure it is installed:"
    echo ""
    echo "   brew install python3"
    echo ""
    echo "   Or grab the official installer from python.org:"
    echo "   👉  https://www.python.org/downloads/macos/"
    echo ""
    echo "   Then re-run this script."
    exit 1
fi

BASE_VER=$("$BASE_PYTHON" --version 2>&1)
echo "✅  Base Python: $BASE_PYTHON  ($BASE_VER)"

# ─────────────────────────────────────────────────────────────────────────────
# Create (or reuse) a virtual environment.
# Installing into a venv bypasses PEP 668 entirely – no --break-system-packages
# needed, and Homebrew's managed environment stays untouched.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
if [ -d "$VENV_DIR" ]; then
    echo "♻️   Reusing existing venv: $VENV_DIR"
else
    echo "🏗️   Creating virtual environment at $VENV_DIR ..."
    "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python3"
echo "✅  venv Python: $PYTHON"

# ── Install rumps inside the venv ─────────────────────────────────────────────
echo ""
echo "📦  Installing 'rumps' into venv..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet --upgrade rumps
echo "✅  rumps installed."

# ── Sanity-check import ───────────────────────────────────────────────────────
echo ""
echo "🧪  Testing import..."
if ! "$PYTHON" -c "import rumps; print('   rumps OK')" 2>/dev/null; then
    echo "❌  rumps import failed inside venv. Something is wrong."
    echo "   Try deleting the venv and re-running:"
    echo "   rm -rf $VENV_DIR && bash $0"
    exit 1
fi

chmod +x "$APP_SCRIPT"

# ── Kill any existing instance ────────────────────────────────────────────────
OLD_PID=$(pgrep -f "python.*claude_tracker.py" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo ""
    echo "🔄  Stopping existing instance (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
fi

# ── Auto-start at login? ──────────────────────────────────────────────────────
echo ""
read -r -p "   Launch Claude Tracker automatically at every login? [y/N] " AUTOSTART
echo ""

if [[ "$AUTOSTART" =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    # LaunchAgent uses the venv Python so it always has rumps available
    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${APP_SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>
</dict>
</plist>
PLIST

    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load   "$PLIST_PATH"
    echo "✅  Auto-start enabled. Claude Tracker will appear at every login."
    echo "   To remove:  launchctl unload '$PLIST_PATH' && rm '$PLIST_PATH'"
else
    # One-shot background launch using the venv Python
    nohup "$PYTHON" "$APP_SCRIPT" \
        > "$LOG_OUT" \
        2> "$LOG_ERR" &
    NEW_PID=$!
    sleep 2

    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "✅  Claude Tracker is running (PID $NEW_PID)."
    else
        echo "❌  The app exited immediately after launch. Checking logs..."
        echo ""
        if [ -s "$LOG_ERR" ]; then
            echo "── Error output ────────────────────────────────"
            cat "$LOG_ERR"
            echo "────────────────────────────────────────────────"
        fi
        echo ""
        echo "   Run it directly to see the full error:"
        echo "   $PYTHON $APP_SCRIPT"
        exit 1
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅  All done! Check your menu bar.          ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Look for the progress bar icon near the clock."
echo ""
echo "  ── HOW TO USE ─────────────────────────────────"
echo "  • Click the icon → Connect to Claude.ai…"
echo "  • Paste your sessionKey (from browser DevTools)"
echo "  • Usage syncs automatically every 60 seconds"
echo ""
echo "  ── GET YOUR SESSION KEY ────────────────────────"
echo "  1. Open claude.ai in your browser"
echo "  2. Press Cmd+Option+I → Application → Cookies"
echo "  3. Find 'sessionKey' and copy its value"
echo ""
echo "  ── SUPPORT THE PROJECT ─────────────────────────"
echo "  ☕  buymeacoffee.com/tinytoolkit"
echo ""
echo "  ── LOGS ────────────────────────────────────────"
echo "  stdout : $LOG_OUT"
echo "  stderr : $LOG_ERR"
echo "  crash  : ~/.claude_tracker_crash.log"
echo "  venv   : $VENV_DIR"
echo ""
echo "  ── STOP ────────────────────────────────────────"
if [[ "$AUTOSTART" =~ ^[Yy]$ ]]; then
    echo "  launchctl unload '$PLIST_PATH'"
else
    echo "  pkill -f 'python.*claude_tracker.py'"
fi
echo ""
