#!/usr/bin/env python
"""
MCP Wrapper for Antigravity IDE on Windows.
Normalizes CRLF to LF and propagates subprocess failures to caller.
"""
import os
import subprocess
import sys
import threading
from typing import List, Tuple


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_server_path = os.path.join(script_dir, "mcp_server.py")

    try:
        process = subprocess.Popen(
            [sys.executable, mcp_server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            bufsize=0,
            cwd=script_dir,
        )
    except OSError as exc:
        print(f"Failed to start MCP server: {exc}", file=sys.stderr)
        sys.exit(1)

    io_errors: List[Tuple[str, str]] = []
    stop_forwarding = threading.Event()

    def _record_io_error(channel: str, exc: Exception) -> None:
        io_errors.append((channel, str(exc)))
        stop_forwarding.set()

    def forward_stdin() -> None:
        """Forward stdin to subprocess while normalizing CRLF to LF."""
        try:
            while not stop_forwarding.is_set():
                data = sys.stdin.buffer.read(1)
                if not data:
                    break
                if data == b"\r":
                    continue
                if process.stdin is None:
                    break
                process.stdin.write(data)
                process.stdin.flush()
        except Exception as exc:
            _record_io_error("stdin", exc)
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass

    def forward_stdout() -> None:
        """Forward stdout from subprocess while normalizing CRLF to LF."""
        try:
            while not stop_forwarding.is_set():
                if process.stdout is None:
                    break
                data = process.stdout.read(1)
                if not data:
                    break
                if data == b"\r":
                    continue
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except Exception as exc:
            _record_io_error("stdout", exc)

    stdin_thread = threading.Thread(target=forward_stdin, daemon=True)
    stdout_thread = threading.Thread(target=forward_stdout, daemon=True)
    stdin_thread.start()
    stdout_thread.start()

    process.wait()
    stop_forwarding.set()
    stdout_thread.join(timeout=1)
    stdin_thread.join(timeout=1)

    return_code = int(process.returncode or 0)
    if io_errors:
        channel, message = io_errors[0]
        print(f"Wrapper I/O error ({channel}): {message}", file=sys.stderr)
        if return_code == 0:
            return_code = 1
    sys.exit(return_code)


if __name__ == "__main__":
    main()
