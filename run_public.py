"""
CuiSync - put the app online with a Cloudflare Tunnel.

Starts app.py on http://127.0.0.1:8000, opens a tunnel to it and prints a public link
(plus a QR code) you can send to others or open on your phone.

    python run_public.py                 # quick tunnel, random *.trycloudflare.com link
    python run_public.py --tunnel NAME   # your own named tunnel (fixed link, see README)

Needs cloudflared (see README). The QR code needs: pip install "qrcode[pil]"
Ctrl+C stops both the app and the tunnel.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HOST, PORT = "127.0.0.1", 8000
LOCAL_URL = f"http://{HOST}:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

INSTALL_HELP = """
cloudflared is not installed (or not on your PATH). Install it, then run this again:

  Mac:      brew install cloudflared
  Windows:  winget install --id Cloudflare.cloudflared
            (then open a NEW terminal so the command is found)
  Other:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
"""


def wait_for_app(app_proc, timeout=180):
    """Wait until the Flask app is up (loading the data can take a while)."""
    start = time.time()
    while time.time() - start < timeout:
        if app_proc.poll() is not None:
            return False  # app.py crashed, its error is already printed
        try:
            urllib.request.urlopen(LOCAL_URL + "/", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


QR_FILE = os.path.join(HERE, "cuisync_qr.png")


def show_qr(url):
    """Print a QR code of the link and save it as a PNG."""
    try:
        import qrcode
    except ImportError:
        print('  (Install "qrcode[pil]" to also get a QR code: pip install "qrcode[pil]")\n', flush=True)
        return

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    print("  Scan with a phone camera:\n")
    qr.print_ascii(invert=True)  # light squares for a dark terminal

    try:
        qr.make_image().save(QR_FILE)
        print(f"\n  QR code also saved as {QR_FILE}")
        if sys.platform == "darwin":
            subprocess.Popen(["open", QR_FILE])  # Mac: open in Preview
        elif sys.platform.startswith("win"):
            os.startfile(QR_FILE)  # Windows: open in Photos
    except Exception as error:  # no Pillow or no image viewer
        print(f"  (Could not save the QR image: {error})")
    print(flush=True)


def banner(url):
    line = "=" * (len(url) + 8)
    print(f"\n{line}\n    {url}\n{line}")
    print("  CuiSync is online. Anyone with this link can use the app while this window stays open.")
    print("  Press Ctrl+C to stop.\n", flush=True)
    show_qr(url)


def main():
    parser = argparse.ArgumentParser(description="Run CuiSync behind a Cloudflare Tunnel.")
    parser.add_argument("--tunnel", metavar="NAME",
                        help="name of a named tunnel created with `cloudflared tunnel create` "
                             "(gives a fixed link on your own domain). Omit for a quick tunnel.")
    parser.add_argument("--url", metavar="PUBLIC_URL",
                        help="the public link of your named tunnel, only used to print it")
    args = parser.parse_args()

    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        print(INSTALL_HELP)
        sys.exit(1)

    print(f"Starting CuiSync on {LOCAL_URL} ...", flush=True)
    app_proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py")], cwd=HERE)

    procs = [app_proc]

    def stop(*_):
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("\nStopped CuiSync and the tunnel.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if not wait_for_app(app_proc):
        print("app.py did not start (see the error above). The tunnel was not opened.")
        stop()

    if args.tunnel:
        cmd = [cloudflared, "tunnel", "--no-autoupdate", "run", "--url", LOCAL_URL, args.tunnel]
    else:
        cmd = [cloudflared, "tunnel", "--no-autoupdate", "--url", LOCAL_URL]
    print("Opening Cloudflare Tunnel ...", flush=True)
    tunnel_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
    procs.append(tunnel_proc)

    if args.tunnel and args.url:
        banner(args.url)

    def read_tunnel_log():
        shown = bool(args.tunnel)
        for line in tunnel_proc.stdout:
            match = QUICK_URL_RE.search(line)
            if match and not shown:
                shown = True
                banner(match.group(0))
            elif " ERR " in line or "error" in line.lower():
                print("[cloudflared]", line.rstrip(), flush=True)
            elif args.tunnel and "Registered tunnel connection" in line:
                print("[cloudflared] connected", flush=True)

    threading.Thread(target=read_tunnel_log, daemon=True).start()

    # Keep running until the app or the tunnel stops (or Ctrl+C)
    while True:
        if app_proc.poll() is not None:
            print("app.py stopped, closing the tunnel.")
            stop()
        if tunnel_proc.poll() is not None:
            print("The Cloudflare Tunnel closed (see [cloudflared] messages above). Stopping the app.")
            stop()
        time.sleep(1)


if __name__ == "__main__":
    main()
