"""
Pi Daemon — persistent agent process (T-063).

Keeps PiAgent alive between terminal sessions, eliminating the 6-10s cold start.
Imports, L3 cache, tool instances, and Anthropic prompt cache all stay warm.

Protocol: newline-delimited JSON over TCP 127.0.0.1:7711.
Authkey:  DAEMON_KEY env var or fallback constant.

Usage:
    python pi_daemon.py          # start daemon (stays in foreground; redirect stdout for logging)
    python pi_daemon.py --stop   # send stop signal to running daemon
    python pi_daemon.py --status # print daemon status
"""
import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "127.0.0.1"
PORT = int(os.environ.get("PI_DAEMON_PORT", "7711"))
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pi_daemon.pid")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "daemon.log")
AUTHKEY = os.environ.get("DAEMON_KEY", "pi-local-7711").encode()

_agent = None
_agent_lock = threading.Lock()
_stop_event = threading.Event()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _send(conn: socket.socket, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False) + "\n"
    conn.sendall(data.encode("utf-8"))


def _recv(conn: socket.socket) -> dict | None:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    return json.loads(buf.decode("utf-8").strip())


def _check_authkey(conn: socket.socket) -> bool:
    """Simple HMAC-free handshake — good enough for localhost-only."""
    try:
        challenge = json.dumps({"challenge": AUTHKEY.hex()}) + "\n"
        conn.sendall(challenge.encode("utf-8"))
        resp = _recv(conn)
        return resp and resp.get("key") == AUTHKEY.hex()
    except Exception:
        return False


# ── Agent management ───────────────────────────────────────────────────────────

def _get_agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                print("[Daemon] Starting PiAgent...", flush=True)
                from pi_agent import PiAgent
                _agent = PiAgent()
                # T-085 R4: if a prior session crashed mid-exit, finish its
                # remaining steps now — before the scheduler / Telegram start
                # and before listen() so the daemon never accepts a connection
                # while memory state is mid-write from a previous session.
                try:
                    from agent.session import resume_exit_if_needed
                    resume_exit_if_needed(_agent)
                except Exception as e:
                    # Fail-open: a corrupted state file or partial-resume
                    # error shouldn't strand the daemon. The "non-fatal"
                    # contract from pre-R4 is preserved at the boundary.
                    print(f"[Daemon] exit-resume failed (non-fatal): {e}", flush=True)
                # Start background services
                if _agent.scheduler is not None:
                    _agent.scheduler.start()
                if _agent.telegram is not None and _agent.telegram.is_available():
                    _agent.telegram.start_polling(block=False)
                print("[Daemon] PiAgent ready.", flush=True)
    return _agent


# ── Connection handler ─────────────────────────────────────────────────────────

def _handle_client(conn: socket.socket, addr) -> None:
    """Handle one client connection — runs in its own thread."""
    peer = f"{addr[0]}:{addr[1]}"
    try:
        if not _check_authkey(conn):
            _send(conn, {"error": "auth failed"})
            return

        while True:
            msg = _recv(conn)
            if msg is None:
                break

            action = msg.get("action", "chat")

            if action == "ping":
                _send(conn, {"pong": True, "mode": _get_agent().mode})

            elif action == "status":
                ag = _get_agent()
                _send(conn, {
                    "mode": ag.mode,
                    "session_id": ag.session_id,
                    "turn_number": ag.turn_number,
                    "uptime_s": int(time.time() - _start_time),
                })

            elif action == "stats":
                ag = _get_agent()
                # Observability: queue depth, dropped logs, awareness refresh age & failures,
                # tool-defs cache state, message history size, daemon uptime.
                now = time.time()
                aware_age = None
                if ag._awareness_last_refresh is not None:
                    aware_age = int((time.time() - ag._awareness_last_refresh.timestamp()))
                _send(conn, {
                    "mode": ag.mode,
                    "session_id": ag.session_id,
                    "turn_number": ag.turn_number,
                    "uptime_s": int(now - _start_time),
                    "log_queue_depth": ag._log_queue.qsize(),
                    "log_queue_dropped": ag._log_queue_dropped,
                    "awareness_age_s": aware_age,
                    "awareness_refreshing": ag._awareness_refreshing,
                    "awareness_refresh_failures": ag._awareness_refresh_failures,
                    "tool_defs_cached": ag._tool_defs_cache is not None,
                    "messages_in_context": len(ag.messages),
                })

            elif action == "chat":
                user_input = msg.get("input", "").strip()
                if not user_input:
                    _send(conn, {"output": "", "error": None})
                    continue
                try:
                    ag = _get_agent()
                    response = ag.process_input(user_input)
                    _send(conn, {"output": response, "error": None, "mode": ag.mode})
                except Exception as e:
                    _send(conn, {"output": f"[Pi] Error: {e}", "error": str(e)})

            elif action == "stop":
                _send(conn, {"output": "Daemon stopping.", "error": None})
                _stop_event.set()
                break

            else:
                _send(conn, {"error": f"unknown action: {action}"})

    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Server loop ────────────────────────────────────────────────────────────────

def _write_pid():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def _read_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _shutdown_agent() -> None:
    """Stop subsystems and drain async log queue before exit. Best-effort."""
    if _agent is None:
        return
    print("[Daemon] Shutting down agent...", flush=True)
    try:
        if getattr(_agent, "scheduler", None) is not None:
            try:
                _agent.scheduler.stop()
            except Exception as e:
                print(f"[Daemon] scheduler.stop failed: {e}", flush=True)
        if getattr(_agent, "telegram", None) is not None:
            try:
                _agent.telegram.stop()
            except Exception as e:
                print(f"[Daemon] telegram.stop failed: {e}", flush=True)
        if getattr(_agent, "watchers", None) is not None:
            try:
                _agent.watchers.stop()
            except Exception as e:
                print(f"[Daemon] watchers.stop failed: {e}", flush=True)
        # Drain async log queue last so any final Supabase writes complete
        try:
            drained = _agent.flush_logs(timeout=5.0)
            print(f"[Daemon] log queue drained={drained}", flush=True)
        except Exception as e:
            print(f"[Daemon] flush_logs failed: {e}", flush=True)
    except Exception as e:
        print(f"[Daemon] shutdown error: {e}", flush=True)


def _install_signal_handlers() -> None:
    """SIGTERM / SIGINT trigger graceful stop (not just hard kill)."""
    def _handler(signum, frame):
        print(f"[Daemon] signal {signum} received — stopping.", flush=True)
        _stop_event.set()
    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGBREAK"):  # Windows Ctrl+Break
            signal.signal(signal.SIGBREAK, _handler)
    except (ValueError, OSError):
        pass  # not main thread or platform-unsupported — graceful no-op


HTTP_PORT = int(os.environ.get("PI_HTTP_PORT", "7712"))


def _start_http_server() -> None:
    """Launch FastAPI brain server on 127.0.0.1:7712 in a daemon thread."""
    try:
        import uvicorn
        from app.server import app as _http_app, mount_agent as _mount, SERVER_HOST as _HTTP_HOST, SERVER_PORT as _HTTP_PORT_CFG

        _mount(_get_agent())

        def _run():
            uvicorn.run(
                _http_app,
                host=_HTTP_HOST,
                port=_HTTP_PORT_CFG,
                log_level="warning",
                access_log=False,
            )

        t = threading.Thread(target=_run, daemon=True, name="pi-http")
        t.start()
        print(f"[Daemon] HTTP brain server started on 127.0.0.1:{HTTP_PORT}", flush=True)
    except ImportError:
        print("[Daemon] fastapi/uvicorn not installed — HTTP server skipped.", flush=True)
    except Exception as e:
        print(f"[Daemon] HTTP server failed to start (non-fatal): {e}", flush=True)


def _write_daemon_info() -> None:
    """T-284: record which code this daemon is running, so staleness after a
    repo update (silent for months — l3_cache truncation, real email sends,
    unauthorized button taps all shipped this way) can be detected instead
    of discovered by luck.
    """
    import subprocess
    from agent.observability import track_silent

    info = {"started_at": datetime.now(timezone.utc).isoformat(), "git_rev": None, "dirty_file_count": None}
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5)
        if rev.returncode == 0:
            info["git_rev"] = rev.stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, timeout=5)
        if status.returncode == 0:
            info["dirty_file_count"] = len([l for l in status.stdout.splitlines() if l.strip()])
    except Exception as e:
        track_silent("daemon.write_daemon_info", e)

    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daemon_info.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
    except Exception as e:
        track_silent("daemon.write_daemon_info", e)


def serve():
    global _start_time
    _start_time = time.time()

    _write_pid()
    _write_daemon_info()
    _install_signal_handlers()

    # Redirect daemon's own stdout/stderr to log file so client terminal stays clean
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log_fh = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_fh
    sys.stderr = log_fh

    print(f"[Daemon] Starting on {HOST}:{PORT} (pid {os.getpid()})", flush=True)

    # Warm up the agent before accepting connections
    try:
        _get_agent()
    except Exception as e:
        print(f"[Daemon] Agent init failed: {e}", flush=True)
        _clear_pid()
        sys.exit(1)

    # T-187: start HTTP+SSE server on 7712 in a background thread (non-blocking).
    _start_http_server()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, PORT))
    except OSError as e:
        print(f"[Daemon] Bind failed: {e}", flush=True)
        _clear_pid()
        sys.exit(1)

    server.listen(5)
    server.settimeout(1.0)
    print(f"[Daemon] Listening on {HOST}:{PORT}", flush=True)

    try:
        while not _stop_event.is_set():
            try:
                conn, addr = server.accept()
                t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
                t.start()
            except socket.timeout:
                continue
    finally:
        server.close()
        _shutdown_agent()
        _clear_pid()
        print("[Daemon] Stopped.", flush=True)


# ── CLI helpers ────────────────────────────────────────────────────────────────

def _client_send_action(action: str) -> dict | None:
    """Open a one-shot client connection, send action, return response."""
    try:
        s = socket.create_connection((HOST, PORT), timeout=3)
        challenge = json.loads(s.recv(4096).decode().strip())
        data = json.dumps({"key": challenge["challenge"]}) + "\n"
        s.sendall(data.encode())
        s.sendall((json.dumps({"action": action}) + "\n").encode())
        resp = b""
        while not resp.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        s.close()
        return json.loads(resp.decode().strip())
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--stop" in args:
        r = _client_send_action("stop")
        print(r.get("output") or r.get("error") or "done")

    elif "--status" in args:
        r = _client_send_action("status")
        if "error" in r and r["error"]:
            print(f"Daemon not running: {r['error']}")
        else:
            print(f"mode={r.get('mode')}  session={r.get('session_id')}  "
                  f"turns={r.get('turn_number')}  uptime={r.get('uptime_s')}s")

    else:
        serve()
