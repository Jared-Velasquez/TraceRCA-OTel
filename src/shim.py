"""Alertmanager webhook → tracerca CLI. See docs/architecture.md §3.7."""
import json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

EVAL = Path("eval/eval.jsonl")
MODEL = os.environ.get("TRACERCA_MODEL", "models/model_live.pkl")

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(body)
        self.send_response(200); self.end_headers()
        for alert in payload.get("alerts", []):
            if alert.get("status") != "firing":
                continue
            service = alert["labels"].get("service_name")
            if not service:
                continue
            t0 = time.time()
            rc = subprocess.run(
                [sys.executable, "-m", "tracerca",
                 "--service", service, "--window", "5m", "--model", MODEL],
                capture_output=True, text=True,
            ).returncode
            t1 = time.time()
            EVAL.parent.mkdir(exist_ok=True)
            with EVAL.open("a") as f:
                f.write(json.dumps({
                    "invoke_time": t0, "complete_time": t1, "service": service,
                    "window": "5m", "source": "shim", "subprocess_rc": rc,
                    "model_path": MODEL,
                }) + "\n")

if __name__ == "__main__":
    # Bind 0.0.0.0 not 127.0.0.1 — Alertmanager runs in the compose container and
    # reaches the host via Docker Desktop's `host.docker.internal`, which routes
    # to the host's IP. Loopback-only would be unreachable from there.
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
