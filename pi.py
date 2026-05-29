"""
Pi CLI — thin client for pi_daemon (T-063).

Connects to the persistent Pi daemon instead of launching a fresh PiAgent.
Cold start goes from 6-10s to <200ms.

Usage:
    python pi.py            # interactive session
    python pi.py --status   # daemon health
    python pi.py --stop     # stop daemon
    python pi.py --no-daemon # fall back to direct pi_agent.py (for debugging)

Auto-start: if daemon is not running, starts it in the background and retries.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "127.0.0.1"
PORT = int(os.environ.get("PI_DAEMON_PORT", "7711"))
AUTHKEY = os.environ.get("DAEMON_KEY", "pi-local-7711").encode()
DAEMON_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi_daemon.py")


# ── Socket helpers ─────────────────────────────────────────────────────────────

def _send(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def _recv(sock: socket.socket, timeout: float = 120.0) -> dict | None:
    sock.settimeout(timeout)
    buf = b""
    try:
        while not buf.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
    except socket.timeout:
        return None
    return json.loads(buf.decode("utf-8").strip())


def _handshake(sock: socket.socket) -> bool:
    sock.settimeout(5.0)
    try:
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                return False
            raw += chunk
        challenge = json.loads(raw.decode().strip())
        _send(sock, {"key": challenge["challenge"]})
        return True
    except Exception:
        return False


def _connect(timeout: float = 3.0) -> socket.socket | None:
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        if _handshake(s):
            return s
        s.close()
    except (ConnectionRefusedError, socket.timeout, OSError):
        pass
    return None


# ── Daemon management ──────────────────────────────────────────────────────────

def _start_daemon() -> None:
    """Launch pi_daemon.py as a detached background process."""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "daemon.log")

    kwargs = dict(
        args=[sys.executable, DAEMON_SCRIPT],
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
        close_fds=True,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    subprocess.Popen(**kwargs)


def _ensure_daemon() -> socket.socket:
    """Connect to daemon, auto-starting it if necessary."""
    sock = _connect(timeout=2.0)
    if sock:
        return sock

    print("Starting Pi daemon...", end=" ", flush=True)
    _start_daemon()

    # Wait up to 12 seconds for daemon to come up
    for _ in range(24):
        time.sleep(0.5)
        sock = _connect(timeout=1.0)
        if sock:
            print("ready.", flush=True)
            return sock

    print("failed.", flush=True)
    print("[Pi] Daemon didn't start. Check logs/daemon.log for errors.", file=sys.stderr)
    sys.exit(1)


# ── Interactive loop ───────────────────────────────────────────────────────────

def _run_interactive() -> None:
    sock = _ensure_daemon()

    # Ask daemon for current mode for the banner
    _send(sock, {"action": "status"})
    status = _recv(sock, timeout=5.0) or {}
    mode = status.get("mode", "?")
    session = status.get("session_id", "?")
    print(f"Pi · {mode} · session {session} · daemon on :{PORT}")

    try:
        while True:
            try:
                user_input = input("Ash: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("Pi: bye")
                break

            _send(sock, {"action": "chat", "input": user_input})
            resp = _recv(sock, timeout=120.0)

            if resp is None:
                print("[Pi] Lost connection to daemon. Reconnecting...")
                sock.close()
                sock = _ensure_daemon()
                continue

            if resp.get("error"):
                print(f"[Pi] Error: {resp['error']}")
            else:
                output = resp.get("output", "")
                if output == "EXIT":
                    print("Pi: session ended.")
                    break
                print(f"Pi: {output}")

    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--no-daemon" in args:
        # Direct fallback — imports and runs pi_agent.py in-process
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.run()

    elif "--stop" in args:
        sock = _connect()
        if not sock:
            print("Daemon is not running.")
        else:
            _send(sock, {"action": "stop"})
            r = _recv(sock, timeout=5.0)
            print(r.get("output") if r else "done")
            sock.close()

    elif "--status" in args:
        sock = _connect()
        if not sock:
            print("Daemon: not running")
        else:
            _send(sock, {"action": "status"})
            r = _recv(sock, timeout=5.0) or {}
            sock.close()
            if r.get("error"):
                print(f"Daemon error: {r['error']}")
            else:
                print(f"Daemon: mode={r.get('mode')}  session={r.get('session_id')}  "
                      f"turns={r.get('turn_number')}  uptime={r.get('uptime_s')}s")

    elif "--stats" in args:
        sock = _connect()
        if not sock:
            print("Daemon: not running")
        else:
            _send(sock, {"action": "stats"})
            r = _recv(sock, timeout=5.0) or {}
            sock.close()
            if r.get("error"):
                print(f"Daemon error: {r['error']}")
            else:
                age = r.get("awareness_age_s")
                age_str = f"{age}s" if age is not None else "never"
                fails = r.get("awareness_refresh_failures", 0)
                fail_str = f" ({fails} failures)" if fails else ""
                print(f"Pi daemon stats")
                print(f"  mode             : {r.get('mode')}")
                print(f"  session          : {r.get('session_id')}")
                print(f"  uptime           : {r.get('uptime_s')}s")
                print(f"  turns this sess  : {r.get('turn_number')}")
                print(f"  msgs in context  : {r.get('messages_in_context')}")
                print(f"  tool defs cached : {r.get('tool_defs_cached')}")
                print(f"  log queue depth  : {r.get('log_queue_depth')}")
                print(f"  log dropped      : {r.get('log_queue_dropped')}")
                print(f"  awareness age    : {age_str}{fail_str}")
                print(f"  awareness refresh: {'in progress' if r.get('awareness_refreshing') else 'idle'}")

    else:
        _run_interactive()
