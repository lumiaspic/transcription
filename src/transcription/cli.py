"""Typer CLI - the user-facing surface.

Commands:
    transcription record [--duration N] [--name X] [--no-transcribe] [--model X]
    transcription transcribe <id> [--model X] [--no-diarize] [--language X]
    transcription list
    transcription devices
    transcription daemon [--poll-interval N]
    transcription jobs list [--status pending|running|done|failed|all]
    transcription jobs show <job-id>
    transcription jobs retry <job-id>
    transcription config show
    transcription config set-token <service>
    transcription config remove-token <service>
    transcription config set <key> <value>
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from getpass import getpass
from pathlib import Path

import typer

from . import config as cfg
from .audio import devices as audio_devices
from .audio.recorder import DualRecorder
from .backends.factory import get_backend
from .paths import recordings_dir
from .pipeline.jobs import JobQueue
from .pipeline.transcribe import run_transcription
from .pipeline.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("transcription")

app = typer.Typer(
    help="Local audio capture + WhisperX transcription pipeline.", no_args_is_help=True
)
config_app = typer.Typer(help="Configure tokens and settings.", no_args_is_help=True)
jobs_app = typer.Typer(help="Manage queued transcription jobs.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(jobs_app, name="jobs")


# ---------- helpers ----------


def _new_recording_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _recording_path(rec_id: str) -> Path:
    return recordings_dir() / rec_id


def _write_meta(rec_dir: Path, meta: dict) -> None:
    (rec_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _read_meta(rec_dir: Path) -> dict:
    p = rec_dir / "meta.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- record ----------


@app.command()
def record(
    duration: float = typer.Option(None, help="Seconds. Omit to record until Ctrl+C."),
    name: str = typer.Option(None, help="Custom name; default = current timestamp."),
    mic: str = typer.Option(None, help="Override mic device name."),
    speaker: str = typer.Option(None, help="Override speaker (loopback source) name."),
    no_transcribe: bool = typer.Option(
        False,
        "--no-transcribe",
        help="Don't enqueue a transcription job at the end.",
    ),
    model: str = typer.Option(None, help="Whisper model for the queued job (overrides config)."),
    language: str = typer.Option(None, help="Force language code (e.g. 'fr') for the queued job."),
    no_diarize: bool = typer.Option(
        False,
        "--no-diarize",
        help="Skip diarization on the system track for the queued job.",
    ),
    sample_rate: int = typer.Option(
        None,
        "--sample-rate",
        help="Recording sample rate in Hz (overrides config). Default 16000 (matches Whisper/pyannote internal rate).",
    ),
    format: str = typer.Option(
        None,
        "--format",
        help="Recording format: 'flac' or 'wav' (overrides config). Default 'flac'.",
    ),
) -> None:
    """Capture mic + system audio, then enqueue it for transcription."""
    rec_id = name or _new_recording_id()
    rec_dir = _recording_path(rec_id)
    rec_dir.mkdir(parents=True, exist_ok=True)

    c = cfg.load_config()
    sr = sample_rate or int(c.get("recording_sample_rate", 16000))
    fmt = (format or c.get("recording_format", "flac")).lower()

    typer.echo(f"Recording -> {rec_dir}")
    typer.echo(f"  mic    : {mic or '(default)'}")
    typer.echo(f"  system : {speaker or '(default)'}")
    typer.echo(f"  audio  : {sr} Hz {fmt}")

    recorder = DualRecorder(rec_dir, mic_name=mic, speaker_name=speaker, sample_rate=sr, format=fmt)
    recorder.start()
    started = time.time()
    try:
        if duration:
            typer.echo(f"Recording for {duration}s... (Ctrl+C to stop early)")
            time.sleep(duration)
        else:
            typer.echo("Recording. Press Ctrl+C to stop.")
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        typer.echo("\nStopping...")
    finally:
        recorder.stop()
        elapsed = time.time() - started

    _write_meta(
        rec_dir,
        {
            "id": rec_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(elapsed, 1),
            "tracks": ["mic", "system"],
            "sample_rate": sr,
            "format": fmt,
            "transcribed": False,
        },
    )
    typer.secho(f"Done. {elapsed:.1f}s captured. ID: {rec_id}", fg=typer.colors.GREEN)

    if no_transcribe:
        typer.echo("Skipping job enqueue (--no-transcribe).")
        return

    queue = JobQueue()
    job_id = queue.enqueue(
        recording_id=rec_id,
        recording_dir=rec_dir,
        model=model,
        language=language,
        diarize=not no_diarize,
    )
    typer.secho(
        f"Enqueued as job #{job_id}. Start a daemon to process it: `transcription daemon`",
        fg=typer.colors.CYAN,
    )


# ---------- transcribe (synchronous) ----------


@app.command()
def transcribe(
    rec_id: str = typer.Argument(..., help="Recording ID (folder name under recordings/)."),
    model: str = typer.Option(None, help="Whisper model (tiny|base|small|medium|large-v3)."),
    no_diarize: bool = typer.Option(
        False, "--no-diarize", help="Disable diarization on the system track."
    ),
    language: str = typer.Option(None, help="ISO code (e.g. 'fr'). Auto-detect if omitted."),
) -> None:
    """Run WhisperX on a recording's mic and system audio (synchronous; blocks until done).

    For background processing, use `record` (auto-enqueue) + `daemon` instead.
    """
    rec_dir = _recording_path(rec_id)
    if not rec_dir.exists():
        typer.secho(f"Recording not found: {rec_dir}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    backend = get_backend(model=model)
    try:
        results = run_transcription(
            rec_dir=rec_dir,
            backend=backend,
            language=language,
            diarize=not no_diarize,
            progress=lambda msg: typer.echo(msg),
        )
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    typer.echo(f"\nMerged transcript -> {rec_dir / 'transcript.md'}")
    typer.secho(f"Done. {len(results)} track(s) transcribed.", fg=typer.colors.GREEN)


# ---------- list (recordings) ----------


@app.command("list")
def list_recordings() -> None:
    """List recordings with their status."""
    root = recordings_dir()
    items = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    if not items:
        typer.echo(f"No recordings in {root}")
        return
    typer.echo(f"{'ID':<22} {'Duration':>10} {'Tracks':<12} {'Transcribed':<12}")
    for d in items:
        meta = _read_meta(d)
        tracks = ",".join(meta.get("tracks", []))
        dur = meta.get("duration_seconds", "?")
        done = "yes" if meta.get("transcribed") else "no"
        typer.echo(f"{d.name:<22} {str(dur):>10} {tracks:<12} {done:<12}")


# ---------- devices ----------


@app.command()
def devices() -> None:
    """List available audio input/output devices."""
    for d in audio_devices.list_devices():
        marker = " (default)" if d.is_default else ""
        typer.echo(f"  [{d.kind:7}] {d.name}{marker}")


# ---------- recover ----------


@app.command()
def recover(
    no_enqueue: bool = typer.Option(
        False,
        "--no-enqueue",
        help="Only finalize meta.json; don't enqueue transcription jobs.",
    ),
) -> None:
    """Recover orphan recordings (PC shutdown / crash before clean Stop).

    Scans the recordings/ directory for dirs that contain audio files but no
    meta.json -- typical sign of a recorder process killed mid-write. Computes
    duration from the audio file, writes meta.json with `recovered: true`,
    and enqueues a transcription job.
    """
    from .pipeline.recovery import recover_all

    results = recover_all(enqueue=not no_enqueue)
    if not results:
        typer.echo("No orphan recordings found.")
        return
    for o, job_id in results:
        tag = ",".join(o.tracks_found)
        msg = f"Recovered {o.rec_id} ({o.duration_seconds:.1f}s, tracks={tag})"
        if job_id:
            msg += f" -> enqueued as job #{job_id}"
        typer.secho(msg, fg=typer.colors.GREEN)


# ---------- doctor ----------


@app.command()
def doctor() -> None:
    """Print health checks: Python, PyTorch+CUDA, HF token, backend mode, jobs DB.

    Use this first whenever something feels off. It surfaces config issues
    before they blow up the first job 15 seconds in.
    """
    from .pipeline.hardware import HardwareProbe
    from .pipeline.health import Severity, run_health_checks

    hw = HardwareProbe.detect()
    typer.echo(f"Platform   : {hw.platform}")
    typer.echo(f"CPU cores  : {hw.cpu_cores}")
    if hw.driver_version:
        typer.echo(f"NV driver  : {hw.driver_version}")
    typer.echo("")
    typer.echo("Health:")
    color_map = {
        Severity.OK: typer.colors.GREEN,
        Severity.WARN: typer.colors.YELLOW,
        Severity.ERROR: typer.colors.RED,
    }
    symbol_map = {Severity.OK: "OK ", Severity.WARN: "!  ", Severity.ERROR: "X  "}
    n_err = 0
    for item in run_health_checks(hw):
        marker = typer.style(symbol_map[item.severity], fg=color_map[item.severity], bold=True)
        typer.echo(f"  {marker} {item.name:<22} {item.message}")
        if item.severity == Severity.ERROR:
            n_err += 1
    if n_err:
        raise typer.Exit(code=1)


# ---------- gui ----------


@app.command()
def gui(
    port: int = typer.Option(8765, help="HTTP port for the embedded server."),
    browser: bool = typer.Option(
        False,
        "--browser",
        help="Open in default browser instead of a native window (useful for devtools).",
    ),
) -> None:
    """Launch the desktop app (NiceGUI in a native window).

    Starts an internal Worker thread that drains the job queue while the GUI
    is open. Do NOT run `transcription daemon` in another terminal at the
    same time -- both would race on the same queue.
    """
    from .ui.app import run_gui

    run_gui(port=port, native=not browser)


# ---------- daemon ----------


@app.command()
def daemon(
    poll_interval: float = typer.Option(
        2.0, "--poll-interval", help="Seconds between queue polls."
    ),
) -> None:
    """Run the background worker: drains the job queue forever (Ctrl+C to stop).

    Recovers orphan 'running' jobs on startup (daemon was killed mid-job).
    Keeps loaded models cached in VRAM across jobs.
    """
    worker = Worker(poll_interval=poll_interval)
    worker.run_forever()


# ---------- jobs ----------


def _fmt_job_row(j) -> str:
    err = (j.error or "").replace("\n", " ")[:50]
    return (
        f"{j.id:>4} {j.recording_id:<22} {j.status:<8} "
        f"{(j.model or '-'):<10} {j.created_at:<20} {err}"
    )


@jobs_app.command("list")
def jobs_list(
    status: str = typer.Option(
        "all",
        help="pending | running | done | failed | all",
    ),
    limit: int = typer.Option(50, help="Max rows."),
) -> None:
    """List queued jobs."""
    queue = JobQueue()
    items = queue.list_jobs(status=status, limit=limit)
    if not items:
        typer.echo(f"No jobs (status={status}).")
        return
    typer.echo(f"{'ID':>4} {'RECORDING':<22} {'STATUS':<8} {'MODEL':<10} {'CREATED':<20} ERROR")
    for j in items:
        typer.echo(_fmt_job_row(j))


@jobs_app.command("show")
def jobs_show(job_id: int = typer.Argument(...)) -> None:
    """Show full details of one job (including its full error text)."""
    queue = JobQueue()
    j = queue.get(job_id)
    if not j:
        typer.secho(f"No job with id {job_id}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    for k, v in vars(j).items():
        typer.echo(f"  {k:<14} {v}")


@jobs_app.command("retry")
def jobs_retry(job_id: int = typer.Argument(...)) -> None:
    """Re-queue a failed job."""
    queue = JobQueue()
    if queue.retry(job_id):
        typer.secho(
            f"Job {job_id} re-queued. Make sure a daemon is running.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"Job {job_id} could not be re-queued (not in 'failed' state, or doesn't exist).",
            fg=typer.colors.YELLOW,
        )


# ---------- config ----------


@config_app.command("show")
def config_show() -> None:
    """Show current config + which tokens are set."""
    c = cfg.load_config()
    typer.echo("Settings:")
    for k, v in c.items():
        typer.echo(f"  {k} = {v!r}")
    typer.echo("\nTokens (stored in OS keyring):")
    for svc, present in cfg.list_token_status().items():
        status = (
            typer.style("set", fg=typer.colors.GREEN)
            if present
            else typer.style("not set", fg=typer.colors.YELLOW)
        )
        desc = cfg.KNOWN_TOKEN_SERVICES.get(svc, "")
        typer.echo(f"  {svc:<12} {status}   {desc}")


@config_app.command("set-token")
def config_set_token(
    service: str = typer.Argument(..., help="Service name, e.g. 'huggingface'."),
) -> None:
    """Store a token in the OS keyring (input not echoed)."""
    if service not in cfg.KNOWN_TOKEN_SERVICES:
        typer.secho(
            f"Unknown service '{service}'. Known: {list(cfg.KNOWN_TOKEN_SERVICES)}",
            fg=typer.colors.YELLOW,
        )
    token = getpass(f"Token for {service}: ")
    if not token.strip():
        typer.secho("Empty token, aborted.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    cfg.set_token(service, token.strip())
    typer.secho(f"Token for '{service}' stored in OS keyring.", fg=typer.colors.GREEN)


@config_app.command("remove-token")
def config_remove_token(
    service: str = typer.Argument(...),
) -> None:
    """Delete a stored token."""
    if cfg.remove_token(service):
        typer.secho(f"Removed token for '{service}'.", fg=typer.colors.GREEN)
    else:
        typer.secho(f"No token was set for '{service}'.", fg=typer.colors.YELLOW)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
) -> None:
    """Set a config key (e.g. 'model' = 'medium')."""
    cfg.set_config_key(key, value)
    typer.secho(f"Set {key} = {value!r}", fg=typer.colors.GREEN)


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(
        ..., help="Config key to remove (e.g. 'backend_mode' to re-trigger the wizard)."
    ),
) -> None:
    """Remove a config key. Useful to reset 'backend_mode' and see the wizard again."""
    c = cfg.load_config()
    if c.get(key) is None:
        typer.secho(f"{key} is not set.", fg=typer.colors.YELLOW)
        return
    c[key] = None
    cfg.save_config(c)
    typer.secho(f"Unset {key}.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
