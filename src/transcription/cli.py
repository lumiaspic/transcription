"""Typer CLI - the user-facing surface of Jalon 1.

Commands:
    transcription record [--duration N] [--name X]
    transcription transcribe <id> [--model X] [--no-diarize]
    transcription list
    transcription devices
    transcription config show
    transcription config set-token <service>
    transcription config remove-token <service>
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
from .backends.base import DiarizationUnavailable, TranscriptResult
from .backends.whisperx_local import WhisperXLocalBackend
from .export.format import write_all
from .export.merge import merge_to_markdown
from .paths import recordings_dir
from .pipeline.speakers import SpeakerProfile, profile_for_track


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("transcription")

app = typer.Typer(help="Local audio capture + WhisperX transcription pipeline.", no_args_is_help=True)
config_app = typer.Typer(help="Configure tokens and settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")


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
) -> None:
    """Capture mic + system audio into two separate WAV files."""
    rec_id = name or _new_recording_id()
    rec_dir = _recording_path(rec_id)
    rec_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Recording -> {rec_dir}")
    typer.echo(f"  mic    : {mic or '(default)'}")
    typer.echo(f"  system : {speaker or '(default)'}")

    recorder = DualRecorder(rec_dir, mic_name=mic, speaker_name=speaker)
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

    _write_meta(rec_dir, {
        "id": rec_id,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(elapsed, 1),
        "tracks": ["mic", "system"],
        "transcribed": False,
    })
    typer.secho(f"Done. {elapsed:.1f}s captured. ID: {rec_id}", fg=typer.colors.GREEN)


# ---------- transcribe ----------

@app.command()
def transcribe(
    rec_id: str = typer.Argument(..., help="Recording ID (folder name under recordings/)."),
    model: str = typer.Option(None, help="Whisper model (tiny|base|small|medium|large-v3)."),
    no_diarize: bool = typer.Option(False, "--no-diarize", help="Disable diarization on the system track."),
    language: str = typer.Option(None, help="ISO code (e.g. 'fr'). Auto-detect if omitted."),
) -> None:
    """Run WhisperX on a recording's mic.wav and system.wav."""
    rec_dir = _recording_path(rec_id)
    if not rec_dir.exists():
        typer.secho(f"Recording not found: {rec_dir}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    tracks = [f for f in ("mic", "system") if (rec_dir / f"{f}.wav").exists()]
    if not tracks:
        typer.secho(f"No mic.wav / system.wav in {rec_dir}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    backend = WhisperXLocalBackend(model=model)
    results: list[TranscriptResult] = []

    for track in tracks:
        wav = rec_dir / f"{track}.wav"
        profile = profile_for_track(track)
        if no_diarize and profile == SpeakerProfile.MULTI:
            profile = SpeakerProfile.SOLO

        typer.echo(f"\n--- {track}.wav (profile={profile.value}) ---")
        t0 = time.time()
        try:
            result = backend.transcribe(wav, profile=profile, language=language)
        except DiarizationUnavailable as e:
            typer.secho(f"Diarization unavailable, falling back to single-speaker: {e}",
                        fg=typer.colors.YELLOW)
            result = backend.transcribe(wav, profile=SpeakerProfile.SOLO, language=language)
        result.track = track
        elapsed = time.time() - t0
        rtf = result.duration / elapsed if elapsed > 0 else 0
        typer.echo(f"  {len(result.segments)} segments, {result.duration:.1f}s audio, "
                   f"{elapsed:.1f}s wall ({rtf:.1f}x realtime)")
        paths = write_all(result, rec_dir, stem=track)
        typer.echo(f"  -> {', '.join(p.name for p in paths.values())}")
        results.append(result)

    merged = rec_dir / "transcript.md"
    merge_to_markdown(results, merged)
    typer.echo(f"\nMerged transcript -> {merged}")

    meta = _read_meta(rec_dir)
    meta["transcribed"] = True
    meta["transcribed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    meta["backend"] = backend.name
    meta["model"] = results[0].model if results else None
    _write_meta(rec_dir, meta)

    typer.secho("Done.", fg=typer.colors.GREEN)


# ---------- list ----------

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
        status = typer.style("set", fg=typer.colors.GREEN) if present else typer.style("not set", fg=typer.colors.YELLOW)
        desc = cfg.KNOWN_TOKEN_SERVICES.get(svc, "")
        typer.echo(f"  {svc:<12} {status}   {desc}")


@config_app.command("set-token")
def config_set_token(
    service: str = typer.Argument(..., help="Service name, e.g. 'huggingface'."),
) -> None:
    """Store a token in the OS keyring (input not echoed)."""
    if service not in cfg.KNOWN_TOKEN_SERVICES:
        typer.secho(f"Unknown service '{service}'. Known: {list(cfg.KNOWN_TOKEN_SERVICES)}",
                    fg=typer.colors.YELLOW)
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


if __name__ == "__main__":
    app()
