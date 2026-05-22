"""Trainer entry point. See docs/architecture.md §3.4.

Usage:
    python -m tracerca.train \\
        --baseline-source <tempo-url | pkl-dir> \\
        --window 30m \\
        --out models/model_live.pkl \\
        [--tempo-url http://localhost:3200] \\
        [--hyperparams hp.yaml]
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import click
import yaml

from .converter import convert_otlp_to_raw_pkl
from .schema import write_sidecar
from .tempo_client import TempoClient

DEFAULT_HYPERPARAMS_PATH = Path(__file__).parent / "default_hyperparams.yaml"

# RE2TT 5-service set per architecture §3.4 (mode A).
RE2TT_SERVICES = (
    "ts-order-service",
    "ts-station-service",
    "ts-travel-service",
    "ts-route-service",
    "ts-ticketinfo-service",
)

VENDOR_DIR = Path("vendor/TraceRCA-CD")


def load_default_hyperparams() -> dict[str, Any]:
    with DEFAULT_HYPERPARAMS_PATH.open("r") as f:
        return yaml.safe_load(f)


def load_hyperparams(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return load_default_hyperparams()
    with Path(path).open("r") as f:
        return yaml.safe_load(f)


_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_window(window: str) -> int:
    """Parse '30m', '5m', '1h', '1d' → seconds."""
    m = _WINDOW_RE.match(window)
    if not m:
        raise click.BadParameter(f"invalid window: {window!r}; use e.g. 30m, 1h, 5m, 1d")
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def fetch_baseline_traces(
    tempo_url: str,
    window_seconds: int,
    services: tuple[str, ...] = RE2TT_SERVICES,
) -> tuple[Path, int, int]:
    """Mode A: pull fault-free traces from Tempo for all RE2TT services.

    Returns (pkl_dir, window_start_ts, window_end_ts).
    """
    end_ts = int(time.time())
    start_ts = end_ts - window_seconds
    pkl_dir = Path(tempfile.mkdtemp(prefix="tracerca-train-"))

    with TempoClient(tempo_url) as client:
        for svc in services:
            traceql = f'{{ resource.service.name = "{svc}" }}'
            traces = list(client.fetch_traces(traceql, start_ts, end_ts))
            out_pkl = pkl_dir / f"{svc}.pkl"
            convert_otlp_to_raw_pkl(traces, out_pkl, label=0)
    return pkl_dir, start_ts, end_ts


def list_pkl_dir(pkl_dir: str | Path) -> Path:
    """Mode B: pkls are already on disk in schema-1a form."""
    p = Path(pkl_dir)
    if not p.is_dir():
        raise click.BadParameter(f"--baseline-source pkl-dir requires --baseline-pkl-dir; not found: {p}")
    if not list(p.glob("*.pkl")):
        raise click.BadParameter(f"no *.pkl files in {p}")
    return p


def run_invo_encoding(pkl_dir: Path, invo_path: Path) -> None:
    """Subprocess: vendor/TraceRCA-CD/run_invo_encoding.py.

    The vendor script's `-i` actually expects a single .pkl file (the
    `default="*.pkl"` is misleading — the script does `open(input_file)` on
    whatever is passed). Mode-A baseline fetch writes one pkl per RE2TT
    service into pkl_dir; combine them into a single _combined.pkl since
    schema-1a is just list[dict] and concatenation preserves it.
    """
    import pickle
    script = VENDOR_DIR / "run_invo_encoding.py"
    pkls = sorted(p for p in pkl_dir.glob("*.pkl") if p.name != "_combined.pkl")
    if not pkls:
        raise click.ClickException(f"no *.pkl files in {pkl_dir}")
    if len(pkls) == 1:
        input_pkl = pkls[0]
    else:
        combined: list[Any] = []
        for p in pkls:
            with p.open("rb") as f:
                combined.extend(pickle.load(f))
        input_pkl = pkl_dir / "_combined.pkl"
        with input_pkl.open("wb") as f:
            pickle.dump(combined, f)
    cmd = [sys.executable, str(script), "-i", str(input_pkl), "-o", str(invo_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"run_invo_encoding.py failed (rc={result.returncode}): {result.stderr}"
        )


def run_trace_encoding(combined_pkl: Path, trace_npz: Path) -> None:
    """Subprocess: vendor/TraceRCA-CD/run_trace_encoding.py.

    Produces the trace-level .npz (np.savez archive with keys data/labels/
    masks/trace_ids) that the prepare_model script's `-t` flag reads via
    np.load. Without this, prepare_model's global baseline path errors with
    "allow_pickle=False" because it tries to np.load a pickle.
    """
    script = VENDOR_DIR / "run_trace_encoding.py"
    cmd = [sys.executable, str(script), "-i", str(combined_pkl), "-o", str(trace_npz)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"run_trace_encoding.py failed (rc={result.returncode}): {result.stderr}"
        )


def run_prepare_model(invo_path: Path, trace_npz: Path, out_path: Path) -> None:
    """Subprocess: vendor/TraceRCA-CD/run_anomaly_detection_prepare_model.py."""
    script = VENDOR_DIR / "run_anomaly_detection_prepare_model.py"
    cmd = [
        sys.executable, str(script),
        "-i", str(invo_path),
        "-t", str(trace_npz),
        "-o", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"run_anomaly_detection_prepare_model.py failed (rc={result.returncode}): {result.stderr}"
        )


@click.command(name="train")
@click.option(
    "--baseline-source",
    type=click.Choice(["tempo-url", "pkl-dir"]),
    required=True,
    help="tempo-url: pull fault-free traces live; pkl-dir: read schema-1a pkls from disk.",
)
@click.option("--window", required=True, help="e.g. 30m, 1h. Ignored in pkl-dir mode but required.")
@click.option("--out", "out_path", required=True, type=click.Path(), help="Output model pkl path.")
@click.option("--tempo-url", default="http://localhost:3200", show_default=True)
@click.option(
    "--hyperparams",
    "hyperparams_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Override default hyperparams YAML.",
)
@click.option(
    "--baseline-pkl-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Required when --baseline-source=pkl-dir.",
)
def train(
    baseline_source: str,
    window: str,
    out_path: str,
    tempo_url: str,
    hyperparams_path: str | None,
    baseline_pkl_dir: str | None,
) -> None:
    """Train a TraceRCA-CD model. See docs/architecture.md §3.4."""
    hp = load_hyperparams(hyperparams_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if baseline_source == "tempo-url":
        window_seconds = parse_window(window)
        pkl_dir, win_start, win_end = fetch_baseline_traces(tempo_url, window_seconds)
        source_mode = "tempo-url"
        source = tempo_url
    else:
        if baseline_pkl_dir is None:
            raise click.BadParameter("--baseline-source=pkl-dir requires --baseline-pkl-dir")
        pkl_dir = list_pkl_dir(baseline_pkl_dir)
        win_start = 0
        win_end = 0
        source_mode = "pkl-dir"
        source = str(pkl_dir)

    invo_path = out.with_suffix(".invo.pkl")
    run_invo_encoding(pkl_dir, invo_path)
    # Locate the _combined.pkl that run_invo_encoding produced as a side-effect
    # (or the only pkl if just one service). prepare_model's `-t` needs the
    # trace-encoded .npz of the same baseline data.
    combined_pkl = pkl_dir / "_combined.pkl"
    if not combined_pkl.exists():
        # only one service pkl was present
        all_pkls = [p for p in pkl_dir.glob("*.pkl") if p.suffix == ".pkl"]
        combined_pkl = all_pkls[0]
    trace_npz = out.with_suffix(".trace.npz")
    run_trace_encoding(combined_pkl, trace_npz)
    run_prepare_model(invo_path, trace_npz, out)

    write_sidecar(
        out,
        source_mode=source_mode,
        source=source,
        window_start_ts=win_start,
        window_end_ts=win_end,
        hyperparams=hp,
        trace_count=0,
        row_count=0,
    )
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    train()
