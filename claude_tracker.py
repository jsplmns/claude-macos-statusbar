#!/usr/bin/env python3
"""
Claude Usage Tracker  ·  macOS menu bar
Run:     python3 claude_tracker.py
Install: bash install_claude_tracker.sh
"""

import http.client
import json
import ssl
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import rumps
except ImportError:
    print(
        "\n❌  'rumps' is not installed.\n"
        f"   Python: {sys.executable}\n"
        f"   Fix:    {sys.executable} -m pip install rumps\n",
        file=sys.stderr,
    )
    sys.exit(1)
except Exception as e:
    print(f"\n❌  Failed to import rumps: {e}\n", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)


# ── Config ─────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".claude_tracker.json"

DEFAULTS: dict = {
    "messages_used":    0,
    "messages_limit":   45,
    "reset_time":       None,
    "last_updated":     None,
    "session_key":      "",
    "org_id":           "",
    "live_mode":        False,
    "utilization_5h":   None,
    "utilization_7d":   None,
    "reset_7d":         None,
}


def load_cfg() -> dict:
    if CONFIG_PATH.exists():
        try:
            d = json.loads(CONFIG_PATH.read_text())
            cfg = DEFAULTS.copy()
            cfg.update(d)
            return cfg
        except Exception:
            pass
    cfg = DEFAULTS.copy()
    save_cfg(cfg)
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


# ── Formatting helpers ─────────────────────────────────────────────────────────

def pbar(pct: float, width: int = 10) -> str:
    """Block progress bar — used in the dropdown menu and as status-bar fallback."""
    filled = round(width * min(max(pct, 0.0), 100.0) / 100)
    return "█" * filled + "░" * (width - filled)


def countdown(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt  = datetime.fromisoformat(iso)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        diff = dt - now
        if diff.total_seconds() <= 0:
            return "now"
        s = int(diff.total_seconds())
        h, r = divmod(s, 3600)
        m    = r // 60
        return f"{h}h {m:02d}m" if h else f"{m}m"
    except Exception:
        return None


def time_ago(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt  = datetime.fromisoformat(iso)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        s   = int((now - dt).total_seconds())
        if s < 10:
            return "just now"
        if s < 60:
            return f"{s}s ago"
        return f"{s // 60}m ago"
    except Exception:
        return None


def _pct_and_cd(cfg: dict) -> tuple[float, str | None]:
    live_pct = cfg.get("utilization_5h")
    pct = (
        live_pct
        if live_pct is not None
        else ((cfg["messages_used"] / cfg["messages_limit"] * 100)
              if cfg["messages_limit"] > 0 else 0.0)
    )
    return pct, countdown(cfg.get("reset_time"))


def make_title(cfg: dict) -> str:
    """Plain-text fallback title (emoji dot). Used when PyObjC is unavailable."""
    pct, cd = _pct_and_cd(cfg)
    dot   = "🔴" if pct >= 90 else ("🟡" if pct >= 65 else "🟢")
    parts = [dot, f"{pct:.0f}%"]
    if cd == "now":   parts.append("↺")
    elif cd:          parts.append(cd)
    return "  ".join(parts)


# ── Claude.ai API helpers ──────────────────────────────────────────────────────

_HEADERS = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
    "User-Agent":   (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://claude.ai/",
}


def _conn(session_key: str) -> tuple[http.client.HTTPSConnection, dict]:
    ctx = ssl.create_default_context()
    c   = http.client.HTTPSConnection("claude.ai", timeout=10, context=ctx)
    h   = {**_HEADERS, "Cookie": f"sessionKey={session_key}"}
    return c, h


def _get(session_key: str, path: str) -> tuple[int, dict | None, http.client.HTTPResponse, str]:
    try:
        c, h = _conn(session_key)
        c.request("GET", path, headers=h)
        r   = c.getresponse()
        raw = r.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        return r.status, body, r, raw
    except Exception as e:
        return 0, None, None, str(e)


def fetch_org_id(session_key: str) -> str | None:
    status, body, _, _ = _get(session_key, "/api/organizations")
    if status == 200 and isinstance(body, list) and body:
        return body[0].get("uuid") or body[0].get("id")
    return None


def fetch_live_usage(session_key: str, org_id: str) -> dict | None:
    attempts = [
        f"/api/organizations/{org_id}/usage",
        f"/api/organizations/{org_id}/limits",
        f"/api/organizations/{org_id}/rate_limits",
        f"/api/organizations/{org_id}",
        "/api/account",
    ]
    for path in attempts:
        status, body, resp, _ = _get(session_key, path)
        if status not in (200, 206) or not body:
            continue
        result = _parse_usage_body(body)
        if result:
            return result
        if resp:
            result = _parse_usage_headers(resp)
            if result:
                return result
    return None


def _parse_usage_body(body: dict | list) -> dict | None:
    if isinstance(body, list):
        return None

    if "five_hour" in body or "seven_day" in body:
        result: dict = {}
        fh = body.get("five_hour")
        sd = body.get("seven_day")
        if isinstance(fh, dict) and fh.get("utilization") is not None:
            result["utilization_5h"] = float(fh["utilization"])
            result["reset_time"]     = fh.get("resets_at")
        if isinstance(sd, dict) and sd.get("utilization") is not None:
            result["utilization_7d"] = float(sd["utilization"])
            result["reset_7d"]       = sd.get("resets_at")
        if result:
            return result

    u = body.get("usage", {})
    if isinstance(u, dict):
        msgs  = u.get("messages", u)
        used  = msgs.get("used") or msgs.get("messages_used")
        limit = msgs.get("limit") or msgs.get("messages_limit")
        reset = msgs.get("reset_at") or msgs.get("reset_time") or u.get("reset_at")
        if used is not None and limit is not None:
            return {"messages_used": int(used), "messages_limit": int(limit),
                    "reset_time": reset}

    for used_key in ("messages_used", "used_messages", "message_count"):
        for limit_key in ("messages_limit", "message_limit", "max_messages"):
            if used_key in body and limit_key in body:
                return {
                    "messages_used":  int(body[used_key]),
                    "messages_limit": int(body[limit_key]),
                    "reset_time":     body.get("reset_at") or body.get("reset_time"),
                }

    rl = body.get("rate_limit") or body.get("rateLimit", {})
    if isinstance(rl, dict):
        remaining = rl.get("remaining") or rl.get("requests_remaining")
        limit     = rl.get("limit") or rl.get("requests_limit")
        reset     = rl.get("reset") or rl.get("reset_at")
        if remaining is not None and limit is not None:
            return {"messages_used":  int(limit) - int(remaining),
                    "messages_limit": int(limit),
                    "reset_time":     reset}
    return None


def _parse_usage_headers(resp) -> dict | None:
    for prefix in ("anthropic-ratelimit-requests", "x-ratelimit-requests",
                   "ratelimit-requests"):
        remaining = resp.getheader(f"{prefix}-remaining")
        limit     = resp.getheader(f"{prefix}-limit")
        reset     = resp.getheader(f"{prefix}-reset")
        if remaining is not None and limit is not None:
            try:
                return {
                    "messages_used":  int(limit) - int(remaining),
                    "messages_limit": int(limit),
                    "reset_time":     reset,
                }
            except ValueError:
                pass
    return None


def validate_session_key(session_key: str) -> tuple[bool, str, str]:
    status, body, _, _ = _get(session_key, "/api/organizations")
    if status == 200 and isinstance(body, list) and body:
        org_id = body[0].get("uuid") or body[0].get("id") or ""
        name   = body[0].get("name", "your account")
        return True, org_id, f"Connected to {name}"
    if status in (401, 403):
        return False, "", "Session key rejected — expired or invalid"
    return False, "", f"Unexpected response (HTTP {status})"


# ── App ────────────────────────────────────────────────────────────────────────

class ClaudeTracker(rumps.App):

    def __init__(self) -> None:
        super().__init__("", quit_button=None)
        self.cfg          = load_cfg()
        self._fetch_error = ""

        # ── Status display (non-interactive, grayed-out in menu) ──────────
        self._i_head   = rumps.MenuItem("Claude Usage")          # section header
        self._i_bar    = rumps.MenuItem("")                       # ████████████░░  68%
        self._i_data   = rumps.MenuItem("")                       # 5h · 68%    7d · 12%
        self._i_timer  = rumps.MenuItem("")                       # ↺  1h 42m
        self._i_source = rumps.MenuItem("")                       # ●  Live  —  2m ago

        # ── Actions ───────────────────────────────────────────────────────
        self._conn    = rumps.MenuItem("Connect to Claude.ai…", callback=self.connect_claude)
        self._disconn = rumps.MenuItem("Disconnect",            callback=self.disconnect_claude)
        self._web     = rumps.MenuItem("Open Claude.ai  ↗",     callback=self.open_web)
        self._qui     = rumps.MenuItem("Quit",                   callback=rumps.quit_application)

        self.menu = [
            self._i_head,
            None,
            self._i_bar,
            self._i_data,
            self._i_timer,
            None,
            self._i_source,
            None,
            self._conn,
            self._disconn,
            None,
            self._web,
            None,
            self._qui,
        ]

        self._refresh()

        if self.cfg.get("session_key") and self.cfg.get("live_mode"):
            self._do_live_fetch()

    # ── Status bar title ───────────────────────────────────────────────────

    def _set_title(self, pct: float, cd: str | None) -> None:
        """
        Status bar title: drawn pill-progress-bar (NSImage) to the left,
        percentage + countdown as plain text to the right.

        Strategy — text first, image second:
          1. self.title is ALWAYS set so something is visible even if drawing fails.
          2. We then attempt to prepend a drawn bar via PyObjC:
               track  2 pt, alpha 0.25 → thin, faint background line
               fill   3.5 pt, alpha 0.85 → slightly thicker, fully rounded caps
             setTemplate_(True) → macOS handles light/dark inversion automatically.
          3. On any exception the text title (with a Unicode block bar) is already set.
        """
        # Build the text suffix once
        suffix = ""
        if cd == "now": suffix = "  ↺"
        elif cd:        suffix = f"  {cd}"

        # ── Attempt drawn bar via PyObjC ───────────────────────────────
        try:
            from AppKit import NSBezierPath, NSColor, NSImage

            W, H    = 44.0, 12.0   # canvas in screen points
            track_h = 2.0          # thin background track
            fill_h  = 3.5          # slightly thicker foreground fill
            fill_w  = W * min(max(pct, 0.0), 100.0) / 100.0

            img = NSImage.alloc().initWithSize_((W, H))
            img.lockFocus()

            # Track — very transparent, centered vertically
            ty = (H - track_h) / 2
            NSColor.colorWithWhite_alpha_(0.0, 0.25).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((0, ty), (W, track_h)), track_h / 2, track_h / 2
            ).fill()

            # Fill — taller, opaque, pill-shaped (radius = height / 2)
            if fill_w >= fill_h:
                fy = (H - fill_h) / 2
                NSColor.colorWithWhite_alpha_(0.0, 0.85).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((0, fy), (fill_w, fill_h)), fill_h / 2, fill_h / 2
                ).fill()

            img.unlockFocus()
            img.setTemplate_(True)   # macOS inverts correctly in dark mode

            btn = self._status_item.button()
            btn.setImage_(img)
            btn.setImagePosition_(2)   # NSImageLeft
            # Two leading spaces = visual gap between the drawn bar and the text
            self.title = f"  {pct:.0f}%{suffix}"

        except Exception:
            # ── Unicode fallback: block bar + emoji dot in title ───────
            dot = "🔴" if pct >= 90 else ("🟡" if pct >= 65 else "🟢")
            self.title = f"{dot}  {pbar(pct, 8)}  {pct:.0f}%{suffix}"

    # ── Display refresh ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        cfg      = self.cfg
        used     = cfg["messages_used"]
        limit    = cfg["messages_limit"]
        pct      = (used / limit * 100) if limit > 0 else 0.0
        cd       = countdown(cfg.get("reset_time"))
        live_pct = cfg.get("utilization_5h")
        bar_pct  = live_pct if live_pct is not None else pct

        # ── Status bar: small colored dot via NSAttributedString ──────────
        self._set_title(bar_pct, cd)

        # ── Dropdown: thin line bar ────────────────────────────────────────
        self._i_bar.title = f"  {pbar(bar_pct, 22)}  {bar_pct:.0f}%"

        # ── Usage data ─────────────────────────────────────────────────────
        if live_pct is not None:
            pct_7d = cfg.get("utilization_7d")
            line   = f"  5h · {bar_pct:.0f}%"
            if pct_7d is not None:
                line += f"    ·    7d · {pct_7d:.0f}%"
            self._i_data.title = line
        else:
            self._i_data.title = f"  {used} / {limit} messages"

        # ── Reset countdown ────────────────────────────────────────────────
        if cd == "now":
            self._i_timer.title = "  ↺  Resetting…"
        elif cd:
            self._i_timer.title = f"  ↺  {cd}"
        else:
            self._i_timer.title = "  ↺  —"

        # ── Connection status ──────────────────────────────────────────────
        connected = bool(cfg.get("live_mode") and cfg.get("session_key"))
        if connected:
            synced = time_ago(cfg.get("last_updated"))
            if self._fetch_error:
                self._i_source.title = f"  ●  Error  —  {self._fetch_error}"
            elif synced:
                self._i_source.title = f"  ●  Live  —  {synced}"
            else:
                self._i_source.title = "  ●  Live"
            self._conn.title = "Reconnect…"
            self._disconn.set_callback(self.disconnect_claude)
        else:
            self._i_source.title = "  ○  Not connected"
            self._conn.title = "Connect to Claude.ai…"
            self._disconn.set_callback(None)   # grayed out when disconnected

    # ── Live fetch ─────────────────────────────────────────────────────────

    def _do_live_fetch(self) -> None:
        key    = self.cfg.get("session_key", "")
        org_id = self.cfg.get("org_id", "")
        if not key:
            return

        if not org_id:
            org_id = fetch_org_id(key) or ""
            if org_id:
                self.cfg["org_id"] = org_id
                save_cfg(self.cfg)

        data = fetch_live_usage(key, org_id) if org_id else None

        if data:
            self._fetch_error = ""
            if "utilization_5h" in data:
                pct_5h = data["utilization_5h"]
                self.cfg["messages_used"] = round(
                    pct_5h / 100.0 * self.cfg["messages_limit"]
                )
                if data.get("reset_time"):
                    self.cfg["reset_time"] = data["reset_time"]
                self.cfg["utilization_5h"] = pct_5h
                self.cfg["utilization_7d"] = data.get("utilization_7d")
                self.cfg["reset_7d"]       = data.get("reset_7d")
            else:
                for field in ("messages_used", "messages_limit", "reset_time"):
                    if field in data and data[field] is not None:
                        self.cfg[field] = data[field]
            self.cfg["last_updated"] = datetime.now().isoformat()
            save_cfg(self.cfg)
        else:
            self._fetch_error = "usage endpoint unavailable"

        self._refresh()

    # ── Connect / disconnect ───────────────────────────────────────────────

    def connect_claude(self, _) -> None:
        win = rumps.Window(
            message=(
                "Find your session key in your browser:\n\n"
                "1. Open claude.ai  →  Cmd+Option+I\n"
                "2. Application tab  →  Cookies  →  claude.ai\n"
                "3. Copy the value of  sessionKey\n"
                "   (starts with  sk-ant-sid01…)"
            ),
            title="Connect to Claude.ai",
            default_text="",
            ok="Connect",
            cancel="Cancel",
            dimensions=(440, 24),
        )
        response = win.run()
        if not response.clicked:
            return

        key = (response.text or "").strip()
        if not key:
            return

        ok, org_id, message = validate_session_key(key)
        if ok:
            cfg = load_cfg()
            cfg["session_key"] = key
            cfg["org_id"]      = org_id
            cfg["live_mode"]   = True
            save_cfg(cfg)
            self.cfg = cfg
            self._fetch_error = ""
            self._do_live_fetch()
            rumps.alert(title="Connected", message=f"{message}\n\nUsage syncs every minute.", ok="OK")
        else:
            rumps.alert(title="Connection failed", message=f"{message}\n\nPlease check the key and try again.", ok="OK")

    def disconnect_claude(self, _) -> None:
        cfg = load_cfg()
        cfg["session_key"] = ""
        cfg["org_id"]      = ""
        cfg["live_mode"]   = False
        save_cfg(cfg)
        self.cfg = cfg
        self._fetch_error = ""
        self._refresh()

    def open_web(self, _) -> None:
        subprocess.run(["open", "https://claude.ai"])

    # ── Background tick ────────────────────────────────────────────────────

    @rumps.timer(60)
    def tick(self, _) -> None:
        self.cfg = load_cfg()
        if self.cfg.get("live_mode") and self.cfg.get("session_key"):
            self._do_live_fetch()
        else:
            pct, cd = _pct_and_cd(self.cfg)
            self._set_title(pct, cd)
            self._i_timer.title = f"  ↺  {cd}" if cd else "  ↺  —"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(
        "\n"
        "  ┌──────────────────────────────────────────┐\n"
        "  │        Claude Usage Tracker  🟢           │\n"
        "  │  Monitoring your Claude.ai usage live.   │\n"
        "  │  Look for the icon in your menu bar ↗    │\n"
        "  └──────────────────────────────────────────┘\n"
        "\n"
        "  Press Ctrl-C to quit.\n"
        "\n"
        "  ☕  Like this tool? Support it on Buy Me a Coffee:\n"
        "      https://buymeacoffee.com/tinytoolkit\n"
    )

    try:
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        except Exception:
            pass

        ClaudeTracker().run()
    except KeyboardInterrupt:
        print("\n  👋  Bye!\n")
        sys.exit(0)
    except Exception as e:
        log = Path.home() / ".claude_tracker_crash.log"
        log.write_text(f"[{datetime.now()}] CRASH: {e}\n{traceback.format_exc()}\n")
        print(f"\n❌  App crashed. Details: {log}\n{e}", file=sys.stderr)
        sys.exit(1)
