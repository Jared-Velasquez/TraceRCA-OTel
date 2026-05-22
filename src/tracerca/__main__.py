"""Inference CLI entry point. See docs/architecture.md §3.5.

Usage:
    python -m tracerca \\
        --service ts-order-service \\
        --window 5m \\
        --model models/model_live.pkl \\
        [--tempo-url http://localhost:3200] \\
        [--out-dir eval/] \\
        [--save-input-pkl / --no-save-input-pkl]
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

import click

from .converter import OTLPTrace, convert_otlp_to_raw_pkl
from .ranked_output import parse_ranked_output
from .schema import SchemaMismatchError, read_sidecar
from .tempo_client import TempoClient

VENDOR_DIR = Path("vendor/TraceRCA-CD")

EXIT_OK = 0
EXIT_SCHEMA_MISMATCH = 2
EXIT_EMPTY_OR_UNKNOWN = 3
EXIT_SUBPROCESS_FAIL = 4


_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_window(window: str) -> int:
    m = _WINDOW_RE.match(window)
    if not m:
        raise click.BadParameter(f"invalid window: {window!r}; use e.g. 5m, 30s, 1h")
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# Step 1
def record_invoke_time() -> float:
    return time.time()


# Step 2
def load_model(model_path: str | Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Returns (model_dict, sidecar_meta, model_id). Raises SchemaMismatchError."""
    p = Path(model_path)
    model_bytes = p.read_bytes()
    model = pickle.loads(model_bytes)
    meta = read_sidecar(p)
    digest = hashlib.sha256(model_bytes).hexdigest()[:8]
    model_id = f"{p.stem}@{digest}"
    return model, meta, model_id


# Step 3
def fetch_tempo_traces(
    tempo_url: str,
    service: str,
    window_seconds: int,
) -> tuple[list[OTLPTrace], int, int]:
    end_ts = int(time.time())
    start_ts = end_ts - window_seconds
    traceql = f'{{ resource.service.name = "{service}" }}'
    with TempoClient(tempo_url) as client:
        traces = list(client.fetch_traces(traceql, start_ts, end_ts))
    return traces, start_ts, end_ts


# Step 4
def handle_empty_window(
    *,
    eval_path: Path,
    invoke_time: float,
    service: str,
    window: str,
    tempo_url: str,
    model_id: str,
    model_path: str,
) -> None:
    complete_time = time.time()
    line = {
        "invoke_time": invoke_time,
        "complete_time": complete_time,
        "service": service,
        "window": window,
        "tempo_url": tempo_url,
        "input_pkl_path": None,
        "ranked_list": [],
        "model_id": model_id,
        "model_path": model_path,
        "source": "cli",
        "note": "empty_window",
    }
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    with eval_path.open("a") as f:
        f.write(json.dumps(line) + "\n")


# Step 5
def convert_traces_to_pkl(
    traces: Iterable[OTLPTrace],
    work_dir: Path,
    out_dir: Path,
    invoke_time: float,
    save_input_pkl: bool,
) -> tuple[Path, Path]:
    """Returns (pkl_dir_for_invo, input_pkl_path_for_jsonl)."""
    pkl_dir = work_dir / "raw"
    pkl_dir.mkdir(parents=True, exist_ok=True)
    raw_pkl = pkl_dir / "trace.pkl"
    convert_otlp_to_raw_pkl(traces, raw_pkl, label=0)
    if save_input_pkl:
        inputs_dir = out_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        saved = inputs_dir / f"{invoke_time}.pkl"
        saved.write_bytes(raw_pkl.read_bytes())
        return pkl_dir, saved
    return pkl_dir, raw_pkl


# Step 6
def run_invo_encoding(pkl_dir: Path, invo_path: Path) -> None:
    """Vendor's run_invo_encoding.py `-i` expects a single .pkl file (the
    `default="*.pkl"` is misleading — script does `open(input_file)`).
    Inference writes its raw pkl as `pkl_dir/trace.pkl`; pass that directly.
    """
    script = VENDOR_DIR / "run_invo_encoding.py"
    input_pkl = pkl_dir / "trace.pkl" if pkl_dir.is_dir() else pkl_dir
    cmd = [sys.executable, str(script), "-i", str(input_pkl), "-o", str(invo_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SubprocessError(f"run_invo_encoding.py failed: {result.stderr}")


# Step 7
def run_anomaly_detection_invo(
    invo_path: Path,
    predicted_path: Path,
    model_path: str | Path,
    useful_features_path: Path,
) -> None:
    script = VENDOR_DIR / "run_anomaly_detection_invo.py"
    cmd = [
        sys.executable, str(script),
        "-i", str(invo_path),
        "-o", str(predicted_path),
        "-c", str(model_path),
        "-t", "1",
        "-u", str(useful_features_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SubprocessError(f"run_anomaly_detection_invo.py failed: {result.stderr}")


# Step 8
def run_localization(
    predicted_path: Path,
    out_pkl: Path,
    *,
    min_support_rate: float = 0.1,
    k: int = 100,
    caller_discount_alpha: float = 0.0,
) -> tuple[str, list[str]]:
    """Returns (stdout, ranked_list)."""
    script = VENDOR_DIR / "run_localization_association_rule_mining_20210516.py"
    # Vendor's flags are `-i` / `-o` (not `--injected-file` / `--output-file`).
    cmd = [
        sys.executable, str(script),
        "-i", str(predicted_path),
        "-o", str(out_pkl),
        "--min-support-rate", str(min_support_rate),
        "--k", str(k),
        "--caller-discount-alpha", str(caller_discount_alpha),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SubprocessError(f"localization failed: {result.stderr}")
    ranked = parse_ranked_output(result.stdout, out_pkl)
    return result.stdout, ranked


# Step 9
def record_complete_time() -> float:
    return time.time()


# Step 10
def print_ranked(ranked: list[str]) -> None:
    for r in ranked:
        click.echo(r)


# Step 11
def append_eval_line(
    eval_path: Path,
    *,
    invoke_time: float,
    complete_time: float,
    service: str,
    window: str,
    tempo_url: str,
    input_pkl_path: Path | str | None,
    ranked_list: list[str],
    model_id: str,
    model_path: str,
) -> dict[str, Any]:
    line = {
        "invoke_time": invoke_time,
        "complete_time": complete_time,
        "service": service,
        "window": window,
        "tempo_url": tempo_url,
        "input_pkl_path": str(input_pkl_path) if input_pkl_path is not None else None,
        "ranked_list": ranked_list,
        "model_id": model_id,
        "model_path": model_path,
        "source": "cli",
    }
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    with eval_path.open("a") as f:
        f.write(json.dumps(line) + "\n")
    return line


class SubprocessError(Exception):
    pass


@click.command(name="tracerca")
@click.option("--service", required=True, help="e.g. ts-order-service")
@click.option("--window", required=True, help="e.g. 5m")
@click.option("--model", "model_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--tempo-url", default="http://localhost:3200", show_default=True)
@click.option("--out-dir", default="eval/", show_default=True, type=click.Path())
@click.option("--save-input-pkl/--no-save-input-pkl", default=True, show_default=True)
def main(
    service: str,
    window: str,
    model_path: str,
    tempo_url: str,
    out_dir: str,
    save_input_pkl: bool,
) -> None:
    """Inference CLI. See docs/architecture.md §3.5."""
    invoke_time = record_invoke_time()
    out_dir_p = Path(out_dir)
    eval_path = out_dir_p / "eval.jsonl"

    # Step 2: load model + sidecar
    try:
        _model, _meta, model_id = load_model(model_path)
    except SchemaMismatchError as e:
        click.echo(f"schema mismatch: {e}", err=True)
        sys.exit(EXIT_SCHEMA_MISMATCH)

    # Step 3: query Tempo
    window_seconds = parse_window(window)
    traces, _start_ts, _end_ts = fetch_tempo_traces(tempo_url, service, window_seconds)

    # Step 4: empty window
    if not traces:
        click.echo(
            f"no spans for service={service} window=[{_start_ts}..{_end_ts}]",
            err=True,
        )
        handle_empty_window(
            eval_path=eval_path,
            invoke_time=invoke_time,
            service=service,
            window=window,
            tempo_url=tempo_url,
            model_id=model_id,
            model_path=model_path,
        )
        sys.exit(EXIT_EMPTY_OR_UNKNOWN)

    # Step 5-8: convert + run pipeline
    try:
        with tempfile.TemporaryDirectory(prefix="tracerca-infer-") as tmp:
            tmp_path = Path(tmp)
            pkl_dir, input_pkl_path = convert_traces_to_pkl(
                traces, tmp_path, out_dir_p, invoke_time, save_input_pkl
            )
            invo_path = tmp_path / "invo.pkl"
            run_invo_encoding(pkl_dir, invo_path)

            useful_features = tmp_path / "useful_features.txt"
            # Vendor's run_anomaly_detection_invo.py reads this with
            # eval("".join(f.readlines())) — needs a Python list literal,
            # not newline-separated names.
            useful_features.write_text("['latency', 'http_status']\n")

            predicted_path = tmp_path / "invo.predicted.pkl"
            run_anomaly_detection_invo(invo_path, predicted_path, model_path, useful_features)

            loc_hp = (_meta.get("hyperparams") or {}).get("localization") or {}
            loc_out = tmp_path / "localization.pkl"
            _stdout, ranked = run_localization(
                predicted_path,
                loc_out,
                min_support_rate=loc_hp.get("min_support_rate", 0.1),
                k=loc_hp.get("k", 100),
                caller_discount_alpha=loc_hp.get("caller_discount_alpha", 0.0),
            )
    except SubprocessError as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_SUBPROCESS_FAIL)

    complete_time = record_complete_time()
    print_ranked(ranked)
    append_eval_line(
        eval_path,
        invoke_time=invoke_time,
        complete_time=complete_time,
        service=service,
        window=window,
        tempo_url=tempo_url,
        input_pkl_path=input_pkl_path,
        ranked_list=ranked,
        model_id=model_id,
        model_path=model_path,
    )
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
