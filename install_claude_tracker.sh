#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Claude AI Usage Tracker – Installer & Launcher
# ─────────────────────────────────────────────────────────────────────────────
set -e

VENV_DIR="$HOME/.claude_tracker_venv"      # isolated venv – avoids PEP 668
PLIST_LABEL="com.user.claude-tracker"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_OUT="$HOME/Library/Logs/claude-tracker.log"
LOG_ERR="$HOME/Library/Logs/claude-tracker-error.log"

# Where to fetch the app from when run via `curl … | bash` (no local files).
REPO_SLUG="jsplmns/claude-macos-statusbar"
REPO_BRANCH="main"
BOOTSTRAP_DIR="$HOME/.claude-statusbar"    # stable home for the curl|bash case

# Official python.org installer used if no suitable Python is found.
PYTHON_PKG_VERSION="3.12.8"
PYTHON_PKG_MM="3.12"                        # framework version dir / app folder
PYTHON_PKG_URL="https://www.python.org/ftp/python/${PYTHON_PKG_VERSION}/python-${PYTHON_PKG_VERSION}-macos11.pkg"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Claude AI Usage Tracker – Installer        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Locate the app files ──────────────────────────────────────────────────────
# If the installer is run from a cloned/unzipped repo, use those files. If it's
# piped straight from the web (curl … | bash), download the repo first.
if [ -n "${BASH_SOURCE:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/claude_tracker.py" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    echo "📥  Downloading Claude Statusbar…"
    mkdir -p "$BOOTSTRAP_DIR"
    if ! curl -fsSL "https://github.com/$REPO_SLUG/archive/refs/heads/$REPO_BRANCH.tar.gz" \
            | tar -xz -C "$BOOTSTRAP_DIR" --strip-components=1; then
        echo "❌  Download failed. Check your internet connection and try again."
        exit 1
    fi
    SCRIPT_DIR="$BOOTSTRAP_DIR"
    echo "✅  Downloaded to $BOOTSTRAP_DIR"
fi
APP_SCRIPT="$SCRIPT_DIR/claude_tracker.py"

if [ ! -f "$APP_SCRIPT" ]; then
    echo "❌  App script not found: $APP_SCRIPT"
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
        # Must be 3.10+ (the app uses modern syntax; Apple's bundled 3.9 is skipped)
        VER=$("$PY" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo False)
        [ "$VER" = "True" ] || continue
        # Must be a framework build (AppKit requirement)
        FW=$("$PY" -c "import sysconfig; print(bool(sysconfig.get_config_var('PYTHONFRAMEWORK')))" 2>/dev/null || echo False)
        [ "$FW" = "True" ] || continue
        echo "$PY"; return 0
    done
    return 1
}

echo "🔍  Looking for a compatible (framework) Python 3.10+ ..."
BASE_PYTHON=$(find_base_python || true)

if [ -z "$BASE_PYTHON" ]; then
    echo ""
    echo "🐍  No suitable Python found — installing the official Python ${PYTHON_PKG_VERSION}"
    echo "    from python.org. macOS will ask for your login password to install it."
    echo ""
    PKG_TMP="$(mktemp -t claude-python)" && PKG_TMP="${PKG_TMP}.pkg"
    if ! curl -fSL --progress-bar "$PYTHON_PKG_URL" -o "$PKG_TMP"; then
        echo "❌  Couldn't download Python. Check your internet connection and try again."
        exit 1
    fi
    echo "   Installing… (enter your Mac password if prompted)"
    if ! sudo installer -pkg "$PKG_TMP" -target / ; then
        echo "❌  Python installation failed. Install it manually from"
        echo "    https://www.python.org/downloads/macos/  then run this command again."
        rm -f "$PKG_TMP"
        exit 1
    fi
    rm -f "$PKG_TMP"
    # python.org ships a helper that installs root SSL certificates so HTTPS
    # (and therefore pip) works. Run it quietly if present.
    CERTS="/Applications/Python ${PYTHON_PKG_MM}/Install Certificates.command"
    [ -f "$CERTS" ] && /bin/bash "$CERTS" >/dev/null 2>&1 || true
    hash -r 2>/dev/null || true
    BASE_PYTHON=$(find_base_python || true)
fi

if [ -z "$BASE_PYTHON" ]; then
    echo "❌  Still couldn't find a working Python after installation."
    echo "    Please install from https://www.python.org/downloads/macos/ and re-run."
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

# ── Install dependencies inside the venv ──────────────────────────────────────
echo ""
echo "📦  Installing dependencies into venv..."
"$PYTHON" -m pip install --quiet --upgrade pip
# rumps → the menu bar app. LocalAuthentication → Touch ID confirmation when
# saving the session key to the Keychain. certifi → CA certificates so HTTPS
# works even on a fresh python.org Python (which ships without them).
"$PYTHON" -m pip install --quiet --upgrade rumps pyobjc-framework-LocalAuthentication certifi
echo "✅  dependencies installed."

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

# ── Build "Claude Tracker.app" ────────────────────────────────────────────────
# Packaging as a real .app makes macOS show "Claude Tracker" (not "Python") in
# the Touch ID prompt and elsewhere. py2app alias mode keeps it tiny and quick.
# If the build fails for any reason we fall back to running the script directly.
echo ""
echo "🏷️   Building Claude Tracker.app ..."
APP_DEST="$HOME/Applications/Claude Tracker.app"
BUNDLE_EXEC=""
if "$PYTHON" -m pip install --quiet py2app 2>/dev/null; then
    BUILD_TMP="$SCRIPT_DIR/.app_build"
    rm -rf "$BUILD_TMP"; mkdir -p "$BUILD_TMP"
    ICON_LINE=""
    [ -f "$SCRIPT_DIR/assets/icon.icns" ] && ICON_LINE="        \"iconfile\": \"$SCRIPT_DIR/assets/icon.icns\","
    cat > "$BUILD_TMP/setup.py" <<PYSETUP
from setuptools import setup
setup(
    app=["$APP_SCRIPT"],
    options={"py2app": {
$ICON_LINE
        "plist": {
            "CFBundleName": "Claude Tracker",
            "CFBundleDisplayName": "Claude Tracker",
            "CFBundleIdentifier": "com.jsplmns.claude-tracker",
            "LSUIElement": True,
            "CFBundleShortVersionString": "1.0",
        },
    }},
)
PYSETUP
    if ( cd "$BUILD_TMP" && "$PYTHON" setup.py py2app -A >/dev/null 2>&1 ); then
        BUILT_APP=$(/bin/ls -d "$BUILD_TMP/dist/"*.app 2>/dev/null | head -1)
        if [ -n "$BUILT_APP" ]; then
            mkdir -p "$HOME/Applications"
            rm -rf "$APP_DEST"
            mv "$BUILT_APP" "$APP_DEST"
            BUNDLE_EXEC=$(/usr/bin/find "$APP_DEST/Contents/MacOS" -maxdepth 1 -type f 2>/dev/null | head -1)
        fi
    fi
    rm -rf "$BUILD_TMP"
fi

if [ -n "$BUNDLE_EXEC" ]; then
    echo "✅  Built: $APP_DEST"
else
    echo "⚠️   Couldn't build the app bundle — running the script directly instead."
    echo "    (Everything works; the Touch ID prompt may just show \"Python\".)"
fi

# How the app gets launched (bundle if we built one, otherwise the venv Python).
if [ -n "$BUNDLE_EXEC" ]; then
    PROG_ARGS="        <string>${BUNDLE_EXEC}</string>"
else
    PROG_ARGS="        <string>${PYTHON}</string>
        <string>${APP_SCRIPT}</string>"
fi

# ── Kill any existing instance ────────────────────────────────────────────────
OLD_PID=$(pgrep -f "claude_tracker.py|Claude Tracker.app" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo ""
    echo "🔄  Stopping existing instance (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
fi

# ── Auto-start at login? ──────────────────────────────────────────────────────
echo ""
# Read from the terminal even when the installer is piped from curl. If there's
# no terminal to ask (fully non-interactive), default to enabling autostart.
AUTOSTART="y"
PROMPT="   Launch Claude Tracker automatically at every login? [Y/n] "
if [ -t 0 ]; then
    read -r -p "$PROMPT" AUTOSTART || AUTOSTART="y"
elif read -r -p "$PROMPT" AUTOSTART < /dev/tty 2>/dev/null; then
    :   # answered via the controlling terminal (curl | bash case)
else
    echo "   Enabling automatic launch at login (default)."
fi
echo ""

if [[ ! "$AUTOSTART" =~ ^[Nn]$ ]]; then
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
${PROG_ARGS}
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
    # One-shot background launch (bundle if available, else the venv Python)
    if [ -n "$BUNDLE_EXEC" ]; then
        nohup "$BUNDLE_EXEC" > "$LOG_OUT" 2> "$LOG_ERR" &
    else
        nohup "$PYTHON" "$APP_SCRIPT" > "$LOG_OUT" 2> "$LOG_ERR" &
    fi
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
