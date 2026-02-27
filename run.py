"""Entry point to run the server.

Starts the game server and prints access URLs so friends can connect.
Optionally launches a cloudflared tunnel for public internet access.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading

import uvicorn

# Store config in env vars so the FastAPI app can read them on startup
# (uvicorn reload mode spawns a child process that won't inherit local vars)


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def find_cloudflared() -> str | None:
    """Find cloudflared executable, checking PATH and common install locations."""
    cf = shutil.which("cloudflared")
    if cf:
        return cf
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "cloudflared", "cloudflared.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "cloudflared", "cloudflared.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "cloudflared", "cloudflared.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def start_tunnel(port: int) -> None:
    """Launch cloudflared quick-tunnel in background, print public URL when ready."""
    cf = find_cloudflared()
    if not cf:
        return

    def _run():
        try:
            proc = subprocess.Popen(
                [cf, "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                if ".trycloudflare.com" in line:
                    for part in line.split():
                        if "trycloudflare.com" in part:
                            url = part.strip()
                            if not url.startswith("http"):
                                url = "https://" + url
                            print(flush=True)
                            print("=" * 60, flush=True)
                            print(f"  PUBLIC URL: {url}", flush=True)
                            print("=" * 60, flush=True)
                            print(flush=True)
                            break
        except Exception as e:
            print(f"[cloudflared] tunnel error: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Blood on the Clocktower Server")
    parser.add_argument("--port", type=int, default=8000, help="listen port (default 8000)")
    parser.add_argument("--no-tunnel", action="store_true", help="skip cloudflared tunnel")
    args = parser.parse_args()

    port = args.port
    local_ip = get_local_ip()
    has_cf = find_cloudflared() is not None

    # Pass info to the FastAPI startup event via env
    os.environ["BT_PORT"] = str(port)
    os.environ["BT_LAN_IP"] = local_ip
    os.environ["BT_HAS_CF"] = "1" if has_cf else ""
    os.environ["BT_NO_TUNNEL"] = "1" if args.no_tunnel else ""

    print(flush=True)
    print("=" * 58, flush=True)
    print("  Blood on the Clocktower  |  Auto-Storyteller", flush=True)
    print("=" * 58, flush=True)
    print(flush=True)

    if not args.no_tunnel:
        if has_cf:
            print("  正在创建公网隧道 (cloudflared)...", flush=True)
            print("  公网链接稍后显示，请等待。", flush=True)
            start_tunnel(port)
        else:
            print("  未检测到 cloudflared，无法创建公网链接。", flush=True)
            print("  安装方法：winget install Cloudflare.cloudflared", flush=True)
            print(flush=True)
            print("  其他内网穿透工具：", flush=True)
            print(f"       ngrok http {port}", flush=True)
            print(f"       cpolar http {port}", flush=True)

    print(flush=True)
    print("-" * 58, flush=True)
    print(flush=True)

    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    main()
