"""NiceGUI desktop app for the transcription tool.

Single window with three cards: record, jobs queue, recordings list.
Runs the worker in a background thread (same process), drains queue
continuously while the GUI is open.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from nicegui import ui

from .. import config as cfg
from ..paths import recordings_dir
from ..pipeline.hardware import HardwareProbe
from .state import STATE

log = logging.getLogger(__name__)


# ---------- helpers ----------

def _new_recording_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _open_folder(path: Path) -> None:
    """Open the OS file explorer at `path`."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _read_meta(rec_dir: Path) -> dict:
    p = rec_dir / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _format_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------- actions ----------

def _start_recording() -> None:
    if STATE.recording.active:
        return
    rec_id = _new_recording_id()
    rec_dir = recordings_dir() / rec_id
    rec_dir.mkdir(parents=True, exist_ok=True)
    STATE.begin_recording(rec_id, rec_dir)
    ui.notify(f"Recording started: {rec_id}", type="positive", position="bottom")
    log.info("UI: started recording %s", rec_id)


def _stop_recording_and_enqueue() -> None:
    if not STATE.recording.active:
        return
    rec_id = STATE.recording.rec_id
    rec_dir = STATE.recording.rec_dir
    elapsed = STATE.end_recording()

    # Initial meta (the worker will update transcribed/transcribed_at later)
    (rec_dir / "meta.json").write_text(
        json.dumps({
            "id": rec_id,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(elapsed, 1),
            "tracks": ["mic", "system"],
            "transcribed": False,
        }, indent=2),
        encoding="utf-8",
    )

    job_id = STATE.queue.enqueue(recording_id=rec_id, recording_dir=rec_dir)
    ui.notify(
        f"Stopped after {_format_elapsed(elapsed)}. Enqueued as job #{job_id}.",
        type="positive", position="bottom",
    )
    log.info("UI: stopped %s after %.1fs, enqueued as job %d", rec_id, elapsed, job_id)


def _show_job_error(job_id: int) -> None:
    j = STATE.queue.get(job_id)
    if not j:
        return
    with ui.dialog() as dialog, ui.card().classes("min-w-[600px]"):
        ui.label(f"Job #{j.id} — {j.recording_id}").classes("text-lg font-bold")
        ui.label(f"Status: {j.status}").classes("text-sm text-gray-600")
        ui.separator()
        ui.label("Error:").classes("font-semibold")
        ui.code(j.error or "(no error message)").classes("w-full")
        with ui.row():
            ui.button("Retry", on_click=lambda: (STATE.queue.retry(j.id), dialog.close()))
            ui.button("Close", on_click=dialog.close)
    dialog.open()


# ---------- layout ----------

def _build_ui() -> None:
    # --- header ---
    with ui.header(elevated=True).classes("items-center justify-between"):
        ui.label("Transcription").classes("text-xl font-bold")
        with ui.row().classes("items-center gap-2"):
            ui.label("Worker:").classes("text-sm")
            worker_dot = ui.icon("circle").classes("text-base")

            def _update_dot() -> None:
                worker_dot.props(
                    f"color={'positive' if STATE.worker_alive() else 'grey'}"
                )
            _update_dot()
            ui.timer(2.0, _update_dot)

    # --- main column ---
    with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):

        # --- Recording card ---
        with ui.card().classes("w-full"):
            ui.label("Recording").classes("text-lg font-semibold")
            with ui.row().classes("items-center gap-4"):
                start_btn = ui.button(
                    "● Start", on_click=_start_recording,
                ).props("color=positive size=lg")
                stop_btn = ui.button(
                    "■ Stop", on_click=_stop_recording_and_enqueue,
                ).props("color=negative size=lg")
                elapsed_label = ui.label("—").classes("text-3xl font-mono ml-auto")

            def _tick() -> None:
                if STATE.recording.active:
                    elapsed_label.text = _format_elapsed(STATE.recording_elapsed())
                    elapsed_label.classes(add="text-red-500", remove="text-gray-400")
                    start_btn.disable()
                    stop_btn.enable()
                else:
                    elapsed_label.text = "—"
                    elapsed_label.classes(add="text-gray-400", remove="text-red-500")
                    start_btn.enable()
                    stop_btn.disable()
            _tick()
            ui.timer(0.5, _tick)

        # --- Jobs card ---
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Jobs queue").classes("text-lg font-semibold")
                ui.label(f"{STATE.queue.db_path}").classes(
                    "text-xs text-gray-400 font-mono"
                )
            jobs_table = ui.table(
                columns=[
                    {"name": "id", "label": "#", "field": "id", "align": "right"},
                    {"name": "recording_id", "label": "Recording", "field": "recording_id", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "created_at", "label": "Created", "field": "created_at", "align": "left"},
                    {"name": "error", "label": "Error", "field": "error", "align": "left"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full")
            # Clicking a row opens the recording folder, or the error dialog if failed.
            jobs_table.on("rowClick", lambda e: _on_job_row_click(e.args))

            def _refresh_jobs() -> None:
                rows = []
                for j in STATE.queue.list_jobs(limit=20):
                    rows.append({
                        "id": j.id,
                        "recording_id": j.recording_id,
                        "status": j.status,
                        "created_at": j.created_at,
                        "error": (j.error or "")[:80],
                    })
                jobs_table.rows = rows
                jobs_table.update()
            _refresh_jobs()
            ui.timer(2.0, _refresh_jobs)

        # --- Recordings card ---
        with ui.card().classes("w-full"):
            ui.label("Recordings (recent)").classes("text-lg font-semibold")
            recs_container = ui.column().classes("w-full gap-1")

            def _refresh_recs() -> None:
                recs_container.clear()
                root = recordings_dir()
                items = sorted(
                    [p for p in root.iterdir() if p.is_dir()], reverse=True,
                )[:10]
                with recs_container:
                    if not items:
                        ui.label("No recordings yet.").classes("text-sm text-gray-400 italic")
                        return
                    for d in items:
                        meta = _read_meta(d)
                        done = meta.get("transcribed", False)
                        icon = "check_circle" if done else "schedule"
                        color = "text-green-600" if done else "text-gray-400"
                        dur = meta.get("duration_seconds", "?")
                        with ui.row().classes(
                            "w-full items-center gap-2 hover:bg-gray-100 px-2 py-1 rounded"
                        ):
                            ui.icon(icon).classes(color)
                            ui.label(f"{d.name}").classes("font-mono text-sm flex-grow")
                            ui.label(f"{dur}s").classes("text-xs text-gray-500")
                            ui.button(
                                "Open", on_click=lambda d=d: _open_folder(d),
                            ).props("flat dense size=sm color=primary")
            _refresh_recs()
            ui.timer(3.0, _refresh_recs)


def _on_job_row_click(args) -> None:
    """args = [_, row_dict, _] from quasar's rowClick."""
    try:
        row = args[1]
        job_id = int(row["id"])
        status = row["status"]
    except (KeyError, IndexError, TypeError, ValueError):
        return
    if status == "failed":
        _show_job_error(job_id)
        return
    # Try to open the recording folder
    j = STATE.queue.get(job_id)
    if j:
        p = Path(j.recording_dir)
        if p.exists():
            _open_folder(p)


# ---------- first-run wizard ----------

def _build_wizard() -> None:
    """One-screen wizard shown the very first time the GUI is launched.

    Picks the backend mode (local GPU / local CPU / remote API) based on
    detected hardware. The choice is persisted in config; subsequent
    launches skip this and go straight to the main UI.
    """
    hw = HardwareProbe.detect()

    with ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4"):
        ui.label("Welcome").classes("text-3xl font-bold")
        ui.label(
            "First-run setup. Pick how transcription should run on this machine. "
            "You can change this later via `transcription config set backend_mode <mode>`."
        ).classes("text-gray-600")

        # Detected hardware
        with ui.card().classes("w-full"):
            ui.label("Detected hardware").classes("text-lg font-semibold")
            ui.label(f"• Platform : {hw.platform}").classes("font-mono text-sm")
            ui.label(f"• CPU      : {hw.cpu_cores} cores").classes("font-mono text-sm")
            if hw.has_cuda:
                gpu = f"• GPU      : {hw.gpu_name} — {hw.vram_gb:.1f} GB VRAM"
                ui.label(gpu).classes("font-mono text-sm text-green-700")
            else:
                ui.label("• GPU      : none detected").classes("font-mono text-sm text-yellow-700")
            ui.label(f"• Python   : {hw.python_version}").classes("font-mono text-sm")

        # Backend mode picker
        with ui.card().classes("w-full"):
            ui.label("Backend mode").classes("text-lg font-semibold")

            # Build options with informative labels. Disabled-state is communicated
            # via the label text and validated on save (NiceGUI radio has no
            # per-option disabled flag).
            gpu_label = (
                "Local — NVIDIA GPU (recommended): WhisperX runs on your GPU. "
                "All data stays on this machine."
                if hw.has_cuda else
                "Local — NVIDIA GPU (DISABLED: no GPU detected)"
            )
            cpu_label = (
                "Local — CPU only: slow (~30-60 min per hour of audio), "
                "but all-local and works on any machine."
            )
            remote_label = "Remote API (coming soon — not implemented yet)"

            options = {
                "local_gpu": gpu_label,
                "local_cpu": cpu_label,
                "remote_api": remote_label,
            }
            default = "local_gpu" if hw.has_cuda else "local_cpu"
            choice = ui.radio(options, value=default).props("inline=false").classes("w-full")

            def _save() -> None:
                v = choice.value
                if v == "local_gpu" and not hw.has_cuda:
                    ui.notify(
                        "No CUDA GPU detected — pick Local CPU instead.",
                        type="negative",
                    )
                    return
                if v == "remote_api":
                    ui.notify(
                        "Remote API backend is not implemented yet. "
                        "Pick a local option for now.",
                        type="negative",
                    )
                    return
                cfg.set_config_key("backend_mode", v)
                ui.notify(f"Saved backend_mode={v}. Reloading…", type="positive")
                ui.navigate.reload()

            ui.button("Save and continue", on_click=_save).props("color=positive size=lg")


# ---------- entry point ----------

def run_gui(*, port: int = 8765, native: bool = True) -> None:
    """Launch the desktop app.

    `native=True` opens a real desktop window via pywebview (Edge WebView2 on
    Windows 11). `native=False` opens a browser tab — useful in dev to inspect
    DOM / use devtools.
    """
    STATE.start_worker()
    # Auto-finalize crashed recordings before the worker starts processing,
    # so they show up in the jobs queue alongside any clean Stop from this session.
    STATE.recover_orphans()

    # NiceGUI 2.x needs the root UI inside a @ui.page handler so it can
    # rebuild it on each browser/webview connection. Defining the route
    # lazily here keeps everything inside run_gui() and avoids the
    # "Script mode requires a valid script file" error.
    @ui.page("/")
    def _index() -> None:
        # Dispatch: wizard on first run, main UI otherwise.
        if cfg.load_config().get("backend_mode") is None:
            _build_wizard()
        else:
            _build_ui()

    ui.run(
        title="Transcription",
        port=port,
        native=native,
        window_size=(1000, 800),
        reload=False,
        show=not native,    # native handles its own window; for browser mode auto-open
        favicon="🎙️",
    )
