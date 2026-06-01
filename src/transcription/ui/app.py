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
from ..i18n import resolve_and_set_language, t
from ..paths import recordings_dir
from ..pipeline.hardware import HardwareProbe
from .humanize import format_duration, humanize_error, humanize_recording_id
from .settings import GROQ_BASE_URL, GROQ_DEFAULT_MODEL, REMOTE_PRESETS, open_settings_dialog
from .state import STATE

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def _theme_href() -> str:
    """Stylesheet URL with an mtime cache-buster.

    The native WebView2 cache survives app restarts, so without a version
    query a returning user can keep an outdated theme.css after an update.
    """
    try:
        ver = int((_STATIC_DIR / "theme.css").stat().st_mtime)
    except OSError:
        ver = 0
    return f"/static/theme.css?v={ver}"


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
            t("notify.loopback_unavailable", error=e),
            type="negative",
            position="bottom",
            multi_line=True,
            close_button=True,
            timeout=0,  # stay until dismissed — the message is long and important
        )
        log.warning("UI: start recording aborted (system loopback unavailable): %s", e)
        return
    ui.notify(t("notify.recording_started", rec_id=rec_id), type="positive", position="bottom")
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
        t("notify.recording_stopped", elapsed=_format_elapsed(elapsed), job_id=job_id),
        type="positive",
        position="bottom",
    )
    log.info("UI: stopped %s after %.1fs, enqueued as job %d", rec_id, elapsed, job_id)


def _show_job_error(job_id: int) -> None:
    j = STATE.queue.get(job_id)
    if not j:
        return
    err_text = html.escape(j.error or t("dialog.no_error"))
    with (
        ui.dialog().classes("job-error-dialog") as dialog,
        ui.card().classes("min-w-[600px] gap-3"),
    ):
        ui.label(t("dialog.job_title", job_id=j.id, rec_id=j.recording_id)).classes("dlg-title")
        ui.label(t("dialog.job_status", status=t(f"status.{j.status}.label"))).classes("dlg-sub")
        ui.html('<div class="dlg-divider"></div>')
        ui.label(t("dialog.error")).classes("dlg-label")
        ui.html(f'<pre class="dlg-pre">{err_text}</pre>')
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button(t("action.close"), on_click=dialog.close).props("flat color=primary no-caps")
            ui.button(
                t("action.retry"),
                on_click=lambda: (STATE.queue.retry(j.id), dialog.close()),
            ).props("color=primary unelevated no-caps")
    dialog.open()


# ---------- layout ----------


def _build_header(*, show_worker: bool = True) -> None:
    with ui.header(elevated=True).classes("items-center justify-between"):
        # Inject the whole lockup in one ui.html block so the <img> and
        # <span> end up as direct children of .brand-lockup (and therefore
        # direct flex items). Nesting them inside ui.element + a separate
        # ui.html introduces an extra wrapper div that breaks the row.
        ui.html(
            '<div class="brand-lockup">'
            '<img src="/static/mark.svg" alt="" class="brand-mark"/>'
            "<span>Transcription</span>"
            "</div>"
        )
        with ui.row().classes("items-center gap-3"):
            if show_worker:
                worker_wrap = ui.element("div").classes("worker-status")
                with worker_wrap:
                    ui.label(t("header.worker"))
                    worker_dot = ui.icon("circle").classes("text-base")

                    def _update_dot() -> None:
                        alive = STATE.worker_alive()
                        worker_dot.props(f"color={'positive' if alive else 'grey'}")
                        worker_wrap.tooltip(
                            t("header.worker_alive") if alive else t("header.worker_dead")
                        )

                    _update_dot()
                    ui.timer(2.0, _update_dot)
            ui.button(icon="settings", on_click=open_settings_dialog).props(
                "flat round dense color=primary"
            ).tooltip(t("header.settings"))


def _setup_banner() -> None:
    """Show an actionable banner when the chosen backend is missing credentials.

    Non-blocking: the app still works (diarization just degrades, or remote
    jobs fail with a clear error). The banner is the gentle nudge that tells
    a non-technical user exactly what's missing and where to fix it.
    """
    c = cfg.load_config()
    mode = c.get("backend_mode")
    msg: str | None = None

    if mode in ("local_gpu", "local_cpu"):
        if not cfg.get_token("huggingface"):
            msg = t("banner.hf_missing")
    elif mode == "remote_api":
        slot = c.get("remote_api_token_service") or "remote_api"
        missing = []
        if not (c.get("remote_api_base_url") or "").strip():
            missing.append(t("banner.missing.endpoint"))
        if not (c.get("remote_api_model") or "").strip():
            missing.append(t("banner.missing.model"))
        if not cfg.get_token(slot):
            missing.append(t("banner.missing.api_key"))
        if missing:
            msg = t("banner.remote_missing", missing=", ".join(missing))

    if not msg:
        return

    with ui.element("div").classes("setup-banner w-full"):
        ui.icon("info").classes("setup-banner-icon")
        with ui.column().classes("gap-0"):
            ui.label(t("banner.title")).classes("setup-banner-title")
            ui.label(msg).classes("setup-banner-text")
        ui.button(t("banner.open_settings"), on_click=open_settings_dialog).props(
            "unelevated dense no-caps color=primary"
        ).classes("ml-auto")


def _build_ui() -> None:
    _build_header(show_worker=True)

    # --- main column ---
    with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
        _setup_banner()
        # --- Recording card ---
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label(t("card.recording")).classes("card-title")
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
                    t("rec.start"),
                    on_click=_start_recording,
                ).props("color=positive size=lg unelevated")
                stop_btn = ui.button(
                    t("rec.stop"),
                    on_click=_stop_recording_and_enqueue,
                ).props("color=negative size=lg unelevated")
                elapsed_label = (
                    ui.label(t("rec.ready"))
                    .classes("rec-timer ml-auto")
                    .tooltip(t("rec.ready_tip"))
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
                    elapsed_label.text = t("rec.ready")
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
                ui.label(t("card.jobs")).classes("card-title").tooltip(t("card.jobs_tip"))
            jobs_table = ui.table(
                columns=[
                    {"name": "id", "label": "#", "field": "id", "align": "right"},
                    {
                        "name": "recording_id",
                        "label": t("table.recording"),
                        "field": "recording_id_display",
                        "align": "left",
                    },
                    {
                        "name": "status",
                        "label": t("table.status"),
                        "field": "status",
                        "align": "left",
                    },
                    {
                        "name": "created_at",
                        "label": t("table.created"),
                        "field": "created_at_display",
                        "align": "left",
                    },
                    {
                        "name": "error",
                        "label": t("table.error"),
                        "field": "error_display",
                        "align": "left",
                    },
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
                            "status_label": t(f"status.{j.status}.label") or j.status,
                            "status_tip": t(f"status.{j.status}.tip"),
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
            ui.label(t("card.recordings")).classes("card-title").tooltip(t("card.recordings_tip"))
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
                            f'<span class="empty">{html.escape(t("recs.empty"))}</span>'
                            "</div>"
                        )
                        return
                    for d in items:
                        meta = _read_meta(d)
                        done = meta.get("transcribed", False)
                        icon = "check_circle" if done else "schedule"
                        color = "text-green-600" if done else "text-gray-400"
                        icon_tip = t("recs.transcribed_tip") if done else t("recs.pending_tip")
                        dur = format_duration(meta.get("duration_seconds"))
                        with ui.row().classes("rec-row w-full"):
                            ui.icon(icon).classes(color).tooltip(icon_tip)
                            ui.label(humanize_recording_id(d.name)).classes("rec-id").tooltip(
                                d.name
                            )
                            ui.label(dur).classes("rec-dur")
                            ui.button(
                                t("recs.show_in_folder"),
                                icon="folder_open",
                                on_click=lambda d=d: _open_folder(d),
                            ).props("flat dense size=sm color=primary no-caps").tooltip(
                                t("recs.show_in_folder_tip")
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
    """Two-step wizard shown the very first time the GUI is launched.

    Step 1 picks the backend mode (local GPU / local CPU / remote API) based
    on detected hardware. Step 2 walks the user through the matching token
    setup (HuggingFace for diarization, or the remote API key + endpoint).
    `backend_mode` is only persisted at the very end so a mid-wizard reload
    doesn't skip the remaining steps.
    """
    hw = HardwareProbe.detect()
    _build_header(show_worker=False)

    # Remote (Groq) is the recommended default for everyone — it's the path to
    # the best results for most people, GPU or not.
    state: dict[str, str] = {"choice": "remote_api", "step": "backend"}

    container = ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4")

    def _goto(step: str) -> None:
        state["step"] = step
        _render()

    def _finish() -> None:
        cfg.set_config_key("backend_mode", state["choice"])
        ui.notify(t("wizard.complete_notify"), type="positive")
        ui.navigate.reload()

    def _render() -> None:
        container.clear()
        with container:
            if state["step"] == "backend":
                _wizard_step_backend(hw, state, _goto)
            else:
                _wizard_step_token(hw, state, _goto, _finish)

    _render()


def _wizard_step_backend(hw, state: dict[str, str], goto) -> None:
    ui.label(t("wizard.welcome")).classes("wizard-hero")
    ui.label(t("wizard.backend_lead")).classes("wizard-lead")

    with ui.card().classes("w-full"):
        ui.label(t("wizard.detected_hw")).classes("card-title")
        ui.label(t("wizard.hw_platform", value=hw.platform)).classes("hw-row")
        ui.label(t("wizard.hw_cpu", cores=hw.cpu_cores)).classes("hw-row")
        if hw.has_cuda:
            ui.label(t("wizard.hw_gpu", value=f"{hw.gpu_name} — {hw.vram_gb:.1f} GB VRAM")).classes(
                "hw-row ok"
            )
        else:
            ui.label(t("wizard.hw_gpu_none")).classes("hw-row warn")
        ui.label(t("wizard.hw_python", value=hw.python_version)).classes("hw-row")

    with ui.card().classes("w-full"):
        ui.label(t("wizard.backend_mode")).classes("card-title")

        rows: dict[str, ui.element] = {}

        def _select(value: str, disabled: bool) -> None:
            if disabled:
                return
            state["choice"] = value
            for v, row in rows.items():
                row.classes(add="selected") if v == value else row.classes(remove="selected")

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
                title=t("backend.local_gpu.title"),
                sub=(
                    t("backend.local_gpu.sub_ok")
                    if hw.has_cuda
                    else t("backend.local_gpu.sub_none")
                ),
                tag=None if hw.has_cuda else (t("tag.disabled"), True),
                disabled=not hw.has_cuda,
            )
            _radio_row(
                "local_cpu",
                title=t("backend.local_cpu.title"),
                sub=t("backend.local_cpu.sub"),
            )
            _radio_row(
                "remote_api",
                title=t("backend.remote.title"),
                sub=t("backend.remote.sub_wizard"),
                tag=(t("tag.recommended"), False),
            )

    def _continue() -> None:
        if state["choice"] == "local_gpu" and not hw.has_cuda:
            ui.notify(t("wizard.no_cuda_notify"), type="negative")
            return
        goto("token")

    with ui.row().classes("w-full justify-end"):
        ui.button(t("action.continue"), on_click=_continue).props(
            "color=positive size=lg unelevated no-caps icon-right=arrow_forward"
        )


def _wizard_step_token(hw, state: dict[str, str], goto, finish) -> None:
    if state["choice"] == "remote_api":
        _wizard_step_remote(state, goto, finish)
    else:
        _wizard_step_huggingface(state, goto, finish)


def _wizard_step_huggingface(state: dict[str, str], goto, finish) -> None:
    ui.label(t("wizard.hf_hero")).classes("wizard-hero")
    ui.label(t("wizard.hf_lead")).classes("wizard-lead")

    with ui.card().classes("w-full gap-2"):
        ui.label(t("wizard.hf_get_token")).classes("card-title")
        ui.html(
            '<ol class="wizard-steps">'
            f"<li>{html.escape(t('wizard.hf_step1_pre'))}<b>{html.escape(t('wizard.hf_step1_read'))}</b>"
            f"{html.escape(t('wizard.hf_step1_post'))}"
            '<a href="https://huggingface.co/settings/tokens" target="_blank">'
            "huggingface.co/settings/tokens</a>.</li>"
            f"<li>{html.escape(t('wizard.hf_step2_pre'))}"
            '<a href="https://huggingface.co/pyannote/speaker-diarization-community-1" '
            f'target="_blank">{html.escape(t("wizard.hf_step2_link"))}</a>'
            f"{html.escape(t('wizard.hf_step2_post'))}</li>"
            f"<li>{html.escape(t('wizard.hf_step3'))}</li>"
            "</ol>"
        )

        token_input = (
            ui.input(label=t("input.hf_token"), password=True, password_toggle_button=True)
            .props("outlined dense")
            .classes("w-full")
        )
        result = ui.label("").classes("wizard-test-result")

        async def _test() -> None:
            from nicegui import run

            from ..connectivity import test_huggingface_token

            result.text = t("common.testing")
            result.classes(remove="ok error", add="pending")
            ok, msg = await run.io_bound(test_huggingface_token, (token_input.value or "").strip())
            result.text = msg
            result.classes(remove="pending", add="ok" if ok else "error")

        def _save_and_finish() -> None:
            token = (token_input.value or "").strip()
            if not token:
                ui.notify(t("wizard.hf_paste_first"), type="warning")
                return
            try:
                cfg.set_token("huggingface", token)
            except Exception as e:  # pragma: no cover - keyring backend errors
                log.exception("set_token failed")
                ui.notify(t("wizard.token_save_failed", error=e), type="negative")
                return
            finish()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t("action.test_token"), on_click=_test).props("outline no-caps")

    with ui.row().classes("w-full justify-between"):
        ui.button(t("action.back"), on_click=lambda: goto("backend")).props(
            "flat no-caps icon=arrow_back"
        )
        with ui.row().classes("gap-2"):
            ui.button(t("action.skip_for_now"), on_click=finish).props("flat no-caps")
            ui.button(t("action.save_and_finish"), on_click=_save_and_finish).props(
                "color=positive size=lg unelevated no-caps"
            )


def _wizard_step_remote(state: dict[str, str], goto, finish) -> None:
    ui.label(t("wizard.remote_hero")).classes("wizard-hero")
    ui.label(t("wizard.remote_lead")).classes("wizard-lead")

    c = cfg.load_config()
    form = {
        "base_url": c.get("remote_api_base_url") or "",
        "model": c.get("remote_api_model") or "",
    }

    def _apply_groq() -> None:
        # base_input / model_input are created in the card below; this handler
        # only fires on click, by which point they're bound.
        base_input.value = GROQ_BASE_URL
        model_input.value = GROQ_DEFAULT_MODEL
        ui.notify(t("wizard.groq_applied"), type="positive")

    with ui.card().classes("w-full gap-2"):
        ui.label(t("wizard.groq_get_key")).classes("card-title")
        ui.html(
            '<ol class="wizard-steps">'
            f"<li>{html.escape(t('wizard.groq_step1_pre'))}"
            '<a href="https://console.groq.com/keys" target="_blank">console.groq.com/keys</a>.</li>'
            f"<li>{html.escape(t('wizard.groq_step2'))}</li>"
            f"<li>{html.escape(t('wizard.groq_step3'))}</li>"
            "</ol>"
        )
        with ui.row().classes("w-full"):
            ui.button(t("action.use_groq"), on_click=_apply_groq).props(
                "color=primary unelevated no-caps icon=bolt"
            )

    with ui.card().classes("w-full gap-3"):
        ui.label(t("wizard.endpoint")).classes("card-title")

        base_input = (
            ui.input(label=t("input.base_url"), value=form["base_url"])
            .props("outlined dense placeholder=https://api.groq.com/openai/v1")
            .classes("w-full")
        )

        def _apply_preset(e) -> None:
            url = REMOTE_PRESETS.get(e.value, "")
            if url:
                base_input.value = url

        ui.select(
            list(REMOTE_PRESETS.keys()),
            value="(custom)",
            label=t("input.quick_presets"),
            on_change=_apply_preset,
        ).props("outlined dense").classes("w-full")

        model_input = (
            ui.input(label=t("input.model"), value=form["model"])
            .props("outlined dense placeholder=whisper-large-v3")
            .classes("w-full")
        )
        ui.label(t("wizard.model_hint")).classes("wizard-hint")

        key_input = (
            ui.input(label=t("input.api_key"), password=True, password_toggle_button=True)
            .props("outlined dense")
            .classes("w-full")
        )
        result = ui.label("").classes("wizard-test-result")

        async def _test() -> None:
            from nicegui import run

            from ..connectivity import test_remote_connection

            result.text = t("common.testing")
            result.classes(remove="ok error", add="pending")
            ok, msg = await run.io_bound(
                test_remote_connection,
                (base_input.value or "").strip(),
                (key_input.value or "").strip(),
            )
            result.text = msg
            result.classes(remove="pending", add="ok" if ok else "error")

        def _save_and_finish() -> None:
            base = (base_input.value or "").strip().rstrip("/")
            model = (model_input.value or "").strip()
            key = (key_input.value or "").strip()
            if not (base and model and key):
                ui.notify(t("wizard.remote_fill"), type="warning")
                return
            service = c.get("remote_api_token_service") or "remote_api"
            try:
                cfg.set_config_key("remote_api_base_url", base)
                cfg.set_config_key("remote_api_model", model)
                cfg.set_token(service, key)
            except Exception as e:  # pragma: no cover - keyring backend errors
                log.exception("remote setup save failed")
                ui.notify(t("wizard.settings_save_failed", error=e), type="negative")
                return
            finish()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t("action.test_connection"), on_click=_test).props("outline no-caps")

    with ui.row().classes("w-full justify-between"):
        ui.button(t("action.back"), on_click=lambda: goto("backend")).props(
            "flat no-caps icon=arrow_back"
        )
        ui.button(t("action.save_and_finish"), on_click=_save_and_finish).props(
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

    # Windows groups taskbar buttons (and picks the taskbar icon) by
    # AppUserModelID. Without an explicit one, pywebview runs under pythonw's
    # generic icon. Setting our own makes Windows reuse the shortcut's app.ico.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("transcription.app")
        except Exception:
            log.debug("Could not set AppUserModelID", exc_info=True)

    # Serve the brand assets + theme stylesheet from /static/.
    app.add_static_files("/static", str(_STATIC_DIR))

    # NiceGUI 2.x needs the root UI inside a @ui.page handler so it can
    # rebuild it on each browser/webview connection. Defining the route
    # lazily here keeps everything inside run_gui() and avoids the
    # "Script mode requires a valid script file" error.
    @ui.page("/")
    def _index() -> None:
        # Resolve the UI language per connection (config may have changed since
        # the last reload), before any t() call builds the page.
        config = cfg.load_config()
        resolve_and_set_language(config.get("ui_language"))
        # Stylesheet link must be inside the page scope (NiceGUI 3.x).
        ui.add_head_html(f'<link rel="stylesheet" href="{_theme_href()}">')
        # Dispatch: wizard on first run, main UI otherwise.
        if config.get("backend_mode") is None:
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
        favicon=str(_STATIC_DIR / "app.ico"),
    )
