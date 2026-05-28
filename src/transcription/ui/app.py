"""NiceGUI desktop app for the transcription tool.

Single window with three cards: record, jobs queue, recordings list.
Runs the worker in a background thread (same process), drains queue
continuously while the GUI is open.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import os
import subprocess
import sys
from pathlib import Path

from nicegui import app, ui

from .. import config as cfg
from ..audio.devices import SystemLoopbackUnavailable
from ..paths import recordings_dir
from ..pipeline.hardware import HardwareProbe
from .humanize import format_duration, humanize_error, humanize_recording_id
from .settings import open_settings_dialog
from .state import STATE

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


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


_STATUS_ICONS = {
    "pending": "schedule",
    "running": "graphic_eq",
    "done": "check_circle",
    "failed": "error",
}

_STATUS_LABELS = {
    "pending": "Waiting",
    "running": "Processing",
    "done": "Done",
    "failed": "Failed",
}

_STATUS_TOOLTIPS = {
    "pending": "Queued — waiting for the background service to pick it up.",
    "running": "Currently being transcribed.",
    "done": "Transcription finished. Click the row to open the folder.",
    "failed": "Transcription failed. Click the row for details.",
}


def _humanize_iso_timestamp(iso: str | None) -> str:
    """Render an ISO 8601 timestamp as `humanize_recording_id` does.

    The jobs table stores creation as ISO; this maps it back to the same
    'Today at HH:MM' style used for recording IDs so both columns agree.
    """
    if not iso:
        return ""
    try:
        d = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return humanize_recording_id(d.strftime("%Y%m%d_%H%M%S"))


# Floor for the dB readout — peaks below this read as silent. Keeps the meter
# from flapping at -60 / -∞ / "—" while there's a tiny noise floor.
_DB_FLOOR = -60.0


def _peak_to_db_text(peak: float) -> str:
    """Format a [0, 1] peak as a `-NN dB` string for the live meter readout."""
    if peak <= 0 or not math.isfinite(peak):
        return "—"
    db = 20 * math.log10(min(1.0, peak))
    if db >= -0.5:
        return "0 dB"
    if db <= _DB_FLOOR:
        return "—"
    return f"{db:.0f} dB"


# ---------- actions ----------


def _start_recording() -> None:
    if STATE.recording.active:
        return
    rec_id = _new_recording_id()
    rec_dir = recordings_dir() / rec_id
    rec_dir.mkdir(parents=True, exist_ok=True)
    try:
        STATE.begin_recording(rec_id, rec_dir)
    except SystemLoopbackUnavailable as e:
        # Empty folder we just created — clean up so it doesn't pollute the list.
        try:
            rec_dir.rmdir()
        except OSError:
            pass
        ui.notify(
            f"System audio capture unavailable: {e}",
            type="negative",
            position="bottom",
            multi_line=True,
            close_button=True,
            timeout=0,  # stay until dismissed — the message is long and important
        )
        log.warning("UI: start recording aborted (system loopback unavailable): %s", e)
        return
    ui.notify(f"Recording started: {rec_id}", type="positive", position="bottom")
    log.info("UI: started recording %s", rec_id)


def _stop_recording_and_enqueue() -> None:
    if not STATE.recording.active:
        return
    rec_id = STATE.recording.rec_id
    rec_dir = STATE.recording.rec_dir
    elapsed = STATE.end_recording()

    # Initial meta (the worker will update transcribed/transcribed_at later).
    # `sample_rate` / `format` are read from the recorder instance that was
    # just used, so the meta matches what was actually written to disk.
    recorder = STATE.recording.recorder
    sr = getattr(recorder, "sample_rate", None) if recorder else None
    fmt = getattr(recorder, "format", None) if recorder else None
    (rec_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": rec_id,
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "duration_seconds": round(elapsed, 1),
                "tracks": ["mic", "system"],
                "sample_rate": sr,
                "format": fmt,
                "transcribed": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    job_id = STATE.queue.enqueue(recording_id=rec_id, recording_dir=rec_dir)
    ui.notify(
        f"Stopped after {_format_elapsed(elapsed)}. Enqueued as job #{job_id}.",
        type="positive",
        position="bottom",
    )
    log.info("UI: stopped %s after %.1fs, enqueued as job %d", rec_id, elapsed, job_id)


def _show_job_error(job_id: int) -> None:
    j = STATE.queue.get(job_id)
    if not j:
        return
    err_text = html.escape(j.error or "(no error message)")
    with (
        ui.dialog().classes("job-error-dialog") as dialog,
        ui.card().classes("min-w-[600px] gap-3"),
    ):
        ui.label(f"Job #{j.id} — {j.recording_id}").classes("dlg-title")
        ui.label(f"Status: {j.status}").classes("dlg-sub")
        ui.html('<div class="dlg-divider"></div>')
        ui.label("Error").classes("dlg-label")
        ui.html(f'<pre class="dlg-pre">{err_text}</pre>')
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Close", on_click=dialog.close).props("flat color=primary no-caps")
            ui.button(
                "Retry",
                on_click=lambda: (STATE.queue.retry(j.id), dialog.close()),
            ).props("color=primary unelevated no-caps")
    dialog.open()


# ---------- layout ----------


def _build_header(*, show_worker: bool = True) -> None:
    with ui.header(elevated=True).classes("items-center justify-between"):
        with ui.element("div").classes("brand-lockup"):
            ui.html(
                '<img src="/static/mark.svg" alt="" class="brand-mark"/><span>Transcription</span>'
            )
        with ui.row().classes("items-center gap-3"):
            if show_worker:
                worker_wrap = ui.element("div").classes("worker-status")
                with worker_wrap:
                    ui.label("Background service")
                    worker_dot = ui.icon("circle").classes("text-base")

                    def _update_dot() -> None:
                        alive = STATE.worker_alive()
                        worker_dot.props(f"color={'positive' if alive else 'grey'}")
                        worker_wrap.tooltip(
                            "Background service is running — new recordings will be transcribed automatically."
                            if alive
                            else "Background service is not running. Restart the app to recover."
                        )

                    _update_dot()
                    ui.timer(2.0, _update_dot)
            ui.button(icon="settings", on_click=open_settings_dialog).props(
                "flat round dense color=primary"
            ).tooltip("Settings")


def _build_ui() -> None:
    _build_header(show_worker=True)

    # --- main column ---
    with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
        # --- Recording card ---
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Recording").classes("card-title")
                track_legend = ui.row().classes("items-center gap-2")
                with track_legend:
                    ui.html(
                        '<span class="track-chip mic"><span class="swatch"></span>MIC</span>'
                        '<span class="track-chip sys"><span class="swatch"></span>SYSTEM</span>'
                    )
                track_legend.visible = False
            with ui.row().classes("items-center gap-4"):
                rec_dot = ui.html('<span class="rec-dot"></span>')
                rec_dot.visible = False
                start_btn = ui.button(
                    "● Start",
                    on_click=_start_recording,
                ).props("color=positive size=lg unelevated")
                stop_btn = ui.button(
                    "■ Stop",
                    on_click=_stop_recording_and_enqueue,
                ).props("color=negative size=lg unelevated")
                elapsed_label = (
                    ui.label("Ready")
                    .classes("rec-timer ml-auto")
                    .tooltip("Press Start to begin a new recording.")
                )

            # Live peak meters — visible only while recording. Two rows of
            # [LABEL | track | dB] using the design-system MIC/SYSTEM palette.
            meters = ui.column().classes("meters w-full")
            with meters:
                with ui.row().classes("meter-row w-full"):
                    ui.html('<span class="lbl mic">MIC</span>')
                    mic_track = ui.element("div").classes("meter-track mic")
                    mic_track.style("--level: 0%")
                    mic_db = ui.label("—").classes("db")
                with ui.row().classes("meter-row w-full"):
                    ui.html('<span class="lbl sys">SYSTEM</span>')
                    sys_track = ui.element("div").classes("meter-track sys")
                    sys_track.style("--level: 0%")
                    sys_db = ui.label("—").classes("db")
            meters.visible = False

            def _tick() -> None:
                if STATE.recording.active:
                    elapsed_label.text = _format_elapsed(STATE.recording_elapsed())
                    elapsed_label.classes(add="active")
                    rec_dot.visible = True
                    track_legend.visible = True
                    meters.visible = True
                    start_btn.disable()
                    stop_btn.enable()
                    rec = STATE.recording.recorder
                    mic_peak = float(getattr(rec, "mic_level", 0.0)) if rec else 0.0
                    sys_peak = float(getattr(rec, "system_level", 0.0)) if rec else 0.0
                    mic_track.style(f"--level: {min(100.0, mic_peak * 100):.1f}%")
                    sys_track.style(f"--level: {min(100.0, sys_peak * 100):.1f}%")
                    mic_db.text = _peak_to_db_text(mic_peak)
                    sys_db.text = _peak_to_db_text(sys_peak)
                else:
                    elapsed_label.text = "Ready"
                    elapsed_label.classes(remove="active")
                    rec_dot.visible = False
                    track_legend.visible = False
                    meters.visible = False
                    start_btn.enable()
                    stop_btn.disable()

            _tick()
            # 100ms cadence matches the audio chunk size — meters feel live
            # without burning CPU. The other 0.5s tasks (elapsed text, button
            # state) run on the same tick; cost is negligible.
            ui.timer(0.1, _tick)

        # --- Jobs card ---
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label("Jobs queue").classes("card-title").tooltip(
                    "Transcription work currently running, queued, or recently finished."
                )
            jobs_table = ui.table(
                columns=[
                    {"name": "id", "label": "#", "field": "id", "align": "right"},
                    {
                        "name": "recording_id",
                        "label": "Recording",
                        "field": "recording_id_display",
                        "align": "left",
                    },
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {
                        "name": "created_at",
                        "label": "Created",
                        "field": "created_at_display",
                        "align": "left",
                    },
                    {"name": "error", "label": "Error", "field": "error_display", "align": "left"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full")
            # Render the Status column as a colored pill badge with a leading
            # Material icon — see `.badge.*` rules in theme.css.
            jobs_table.add_slot(
                "body-cell-status",
                """
                <q-td :props="props">
                  <span :class="'badge ' + props.row.status" :title="props.row.status_tip">
                    <q-icon :name="props.row.status_icon" size="13px"></q-icon>
                    {{ props.row.status_label }}
                  </span>
                </q-td>
                """,
            )
            # Recording column shows the friendly label but keeps the raw ID
            # as a tooltip — power users can still copy it.
            jobs_table.add_slot(
                "body-cell-recording_id",
                """
                <q-td :props="props" :title="props.row.recording_id">
                  {{ props.row.recording_id_display }}
                </q-td>
                """,
            )
            # Created column likewise: humanized text with the ISO timestamp on hover.
            jobs_table.add_slot(
                "body-cell-created_at",
                """
                <q-td :props="props" :title="props.row.created_at">
                  {{ props.row.created_at_display }}
                </q-td>
                """,
            )
            # Error column: short human label with a chevron affording an expand.
            # Full traceback is in the dialog triggered by row click.
            jobs_table.add_slot(
                "body-cell-error",
                """
                <q-td :props="props" class="cell-wrap">
                  <span v-if="props.row.error_display" class="err-cell" :title="props.row.error_full">
                    <q-icon name="error_outline" size="14px" class="err-icon"></q-icon>
                    <span class="err-text">{{ props.row.error_display }}</span>
                    <q-icon name="chevron_right" size="14px" class="err-chev"></q-icon>
                  </span>
                </q-td>
                """,
            )
            # Clicking a row opens the recording folder, or the error dialog if failed.
            jobs_table.on("rowClick", lambda e: _on_job_row_click(e.args))

            def _refresh_jobs() -> None:
                rows = []
                for j in STATE.queue.list_jobs(limit=20):
                    err_full = j.error or ""
                    rows.append(
                        {
                            "id": j.id,
                            "recording_id": j.recording_id,
                            "recording_id_display": humanize_recording_id(j.recording_id),
                            "status": j.status,
                            "status_label": _STATUS_LABELS.get(j.status, j.status),
                            "status_tip": _STATUS_TOOLTIPS.get(j.status, ""),
                            "status_icon": _STATUS_ICONS.get(j.status, "schedule"),
                            "created_at": j.created_at,
                            "created_at_display": _humanize_iso_timestamp(j.created_at),
                            "error_display": humanize_error(err_full),
                            "error_full": err_full,
                        }
                    )
                jobs_table.rows = rows
                jobs_table.update()

            _refresh_jobs()
            ui.timer(2.0, _refresh_jobs)

        # --- Recordings card ---
        with ui.card().classes("w-full"):
            ui.label("Recordings (recent)").classes("card-title").tooltip(
                "Audio files captured on this machine. Click 'Show in folder' to open the file location."
            )
            recs_container = ui.column().classes("w-full gap-0")

            def _refresh_recs() -> None:
                recs_container.clear()
                root = recordings_dir()
                items = sorted(
                    [p for p in root.iterdir() if p.is_dir()],
                    reverse=True,
                )[:10]
                with recs_container:
                    if not items:
                        ui.html(
                            '<div class="rec-row">'
                            '<span class="empty">No recordings yet — press Start above to make one.</span>'
                            "</div>"
                        )
                        return
                    for d in items:
                        meta = _read_meta(d)
                        done = meta.get("transcribed", False)
                        icon = "check_circle" if done else "schedule"
                        color = "text-green-600" if done else "text-gray-400"
                        icon_tip = (
                            "Transcribed — folder contains text and subtitles."
                            if done
                            else "Audio captured, transcription pending."
                        )
                        dur = format_duration(meta.get("duration_seconds"))
                        with ui.row().classes("rec-row w-full"):
                            ui.icon(icon).classes(color).tooltip(icon_tip)
                            ui.label(humanize_recording_id(d.name)).classes("rec-id").tooltip(
                                d.name
                            )
                            ui.label(dur).classes("rec-dur")
                            ui.button(
                                "Show in folder",
                                icon="folder_open",
                                on_click=lambda d=d: _open_folder(d),
                            ).props("flat dense size=sm color=primary no-caps").tooltip(
                                "Open the folder containing the audio files and transcripts."
                            )

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

    _build_header(show_worker=False)

    with ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4"):
        ui.label("Welcome").classes("wizard-hero")
        ui.label(
            "First-run setup. Pick how transcription should run on this machine. "
            "You can change this later from the Settings menu."
        ).classes("wizard-lead")

        # Detected hardware
        with ui.card().classes("w-full"):
            ui.label("Detected hardware").classes("card-title")
            ui.label(f"• Platform : {hw.platform}").classes("hw-row")
            ui.label(f"• CPU      : {hw.cpu_cores} cores").classes("hw-row")
            if hw.has_cuda:
                gpu = f"• GPU      : {hw.gpu_name} — {hw.vram_gb:.1f} GB VRAM"
                ui.label(gpu).classes("hw-row ok")
            else:
                ui.label("• GPU      : none detected").classes("hw-row warn")
            ui.label(f"• Python   : {hw.python_version}").classes("hw-row")

        # Backend mode picker
        with ui.card().classes("w-full"):
            ui.label("Backend mode").classes("card-title")

            default = "local_gpu" if hw.has_cuda else "local_cpu"
            state: dict[str, str] = {"choice": default}
            rows: dict[str, ui.element] = {}

            def _select(value: str, disabled: bool) -> None:
                if disabled:
                    return
                state["choice"] = value
                for v, row in rows.items():
                    if v == value:
                        row.classes(add="selected")
                    else:
                        row.classes(remove="selected")

            def _radio_row(
                value: str,
                *,
                title: str,
                sub: str,
                tag: tuple[str, bool] | None = None,
                disabled: bool = False,
            ) -> None:
                klass = "radio-row"
                if value == state["choice"]:
                    klass += " selected"
                if disabled:
                    klass += " disabled"
                row = ui.row().classes(klass)
                row.on("click", lambda _v=value, _d=disabled: _select(_v, _d))
                with row:
                    ui.html('<span class="dot"></span>')
                    with ui.element("div").classes("body-l"):
                        if tag is not None:
                            tag_label, tag_warn = tag
                            tag_class = "tag warn" if tag_warn else "tag"
                            ui.html(
                                f'<div class="title-l">{html.escape(title)}'
                                f'<span class="{tag_class}">{html.escape(tag_label)}</span>'
                                "</div>"
                            )
                        else:
                            ui.html(f'<div class="title-l">{html.escape(title)}</div>')
                        ui.html(f'<div class="sub-l">{html.escape(sub)}</div>')
                rows[value] = row

            with ui.element("div").classes("radio-list w-full"):
                _radio_row(
                    "local_gpu",
                    title="Local — NVIDIA GPU",
                    sub=(
                        "WhisperX runs on your GPU. All data stays on this machine."
                        if hw.has_cuda
                        else "No CUDA GPU detected. Pick Local CPU instead."
                    ),
                    tag=("recommended", False) if hw.has_cuda else ("disabled", True),
                    disabled=not hw.has_cuda,
                )
                _radio_row(
                    "local_cpu",
                    title="Local — CPU only",
                    sub=(
                        "Slow (~30-60 min per hour of audio), "
                        "but all-local and works on any machine."
                    ),
                )
                _radio_row(
                    "remote_api",
                    title="Remote API",
                    sub=(
                        "Any OpenAI-compatible /audio/transcriptions endpoint "
                        "(OpenAI, Groq, self-hosted whisper.cpp). No diarization — "
                        "both tracks transcribe as single speakers. Configure the "
                        "endpoint and API key from Settings → Remote API after this step."
                    ),
                )

            def _save() -> None:
                v = state["choice"]
                if v == "local_gpu" and not hw.has_cuda:
                    ui.notify(
                        "No CUDA GPU detected — pick Local CPU instead.",
                        type="negative",
                    )
                    return
                cfg.set_config_key("backend_mode", v)
                ui.notify(f"Saved backend_mode={v}. Reloading…", type="positive")
                ui.navigate.reload()

            ui.button("Save and continue", on_click=_save).props(
                "color=positive size=lg unelevated no-caps"
            )


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

    # Serve the brand assets + theme stylesheet from /static/.
    app.add_static_files("/static", str(_STATIC_DIR))

    # NiceGUI 2.x needs the root UI inside a @ui.page handler so it can
    # rebuild it on each browser/webview connection. Defining the route
    # lazily here keeps everything inside run_gui() and avoids the
    # "Script mode requires a valid script file" error.
    @ui.page("/")
    def _index() -> None:
        # Stylesheet link must be inside the page scope (NiceGUI 3.x).
        ui.add_head_html('<link rel="stylesheet" href="/static/theme.css">')
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
        show=not native,  # native handles its own window; for browser mode auto-open
        favicon="🎙️",
    )
