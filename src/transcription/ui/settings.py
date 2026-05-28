"""Settings dialog — full GUI surface for everything in config.toml + keyring.

Opens from the gear icon in the header. Each tab maps to a logical group
of config keys (general, recording, local backend, remote API, tokens,
paths). Save writes the merged config to disk and the keyring; some
changes (backend mode, recordings dir) prompt for an app reload.

Keeping this in its own module avoids bloating app.py — settings.py
only renders UI and calls into config.py.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

from nicegui import run, ui

from .. import config as cfg
from ..connectivity import test_huggingface_token, test_remote_connection
from ..paths import config_file, jobs_db, logs_dir, recordings_dir
from ..pipeline.hardware import HardwareProbe

log = logging.getLogger(__name__)


WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
CUDA_COMPUTE_TYPES = ["float16", "float32", "int8_float16", "int8"]
CPU_COMPUTE_TYPES = ["int8", "float32"]
RECORDING_FORMATS = ["flac", "wav"]
SAMPLE_RATES = [16000, 22050, 44100, 48000]
COMPRESS_CODECS = ["opus", "mp3"]

# Subset of ISO 639-1 codes Whisper handles well. Order: "auto" first, then
# the rest alphabetical by language name. The empty-string code maps to
# auto-detection in the config layer.
LANGUAGE_OPTIONS: dict[str, str] = {
    "": "Auto-detect",
    "ar": "Arabic",
    "zh": "Chinese",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "fi": "Finnish",
    "fr": "French",
    "de": "German",
    "el": "Greek",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "es": "Spanish",
    "sv": "Swedish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}


# Common OpenAI-compatible endpoints — drop-down presets that just prefill
# the base URL field. User can still type anything.
REMOTE_PRESETS = {
    "(custom)": "",
    "OpenAI": "https://api.openai.com/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "Local whisper.cpp": "http://localhost:8080/v1",
}


def _none_if_blank(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _int_or_default(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _set_token_dialog(service: str, label: str, on_done) -> None:
    """Small password prompt — keyring writes happen on Save."""
    with ui.dialog() as d, ui.card().classes("min-w-[420px] gap-3"):
        ui.label(f"Set {label}").classes("dlg-title")
        ui.label(
            f"Stored in the OS keyring under service '{service}'. "
            "Never written to disk in plain text."
        ).classes("dlg-sub")
        token_input = (
            ui.input(label="Token", password=True, password_toggle_button=True)
            .props("autofocus outlined dense")
            .classes("w-full")
        )

        def _save() -> None:
            token = (token_input.value or "").strip()
            if not token:
                ui.notify("Empty token — not saved.", type="warning")
                return
            try:
                cfg.set_token(service, token)
                ui.notify(f"Token for '{service}' saved in keyring.", type="positive")
                on_done()
                d.close()
            except Exception as e:  # pragma: no cover - keyring backend errors
                log.exception("set_token failed")
                ui.notify(f"Failed to store token: {e}", type="negative")

        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=d.close).props("flat no-caps color=primary")
            ui.button("Save", on_click=_save).props("color=primary unelevated no-caps")
    d.open()


def _confirm_reload(message: str) -> None:
    """Offer to reload the UI when a saved change needs a restart to take effect."""
    with ui.dialog() as d, ui.card().classes("min-w-[420px] gap-3"):
        ui.label("Reload required").classes("dlg-title")
        ui.label(message).classes("dlg-sub")

        def _reload() -> None:
            d.close()
            ui.navigate.reload()

        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Later", on_click=d.close).props("flat no-caps color=primary")
            ui.button("Reload now", on_click=_reload).props("color=primary unelevated no-caps")
    d.open()


def open_settings_dialog() -> None:
    """Top-level settings UI — a full-screen tabbed dialog."""
    c = cfg.load_config()
    hw = HardwareProbe.detect()

    initial_backend_mode = c.get("backend_mode") or ("local_gpu" if hw.has_cuda else "local_cpu")

    # Form state — populated from current config, mutated by widgets, then
    # flushed to disk on Save.
    form: dict[str, Any] = {
        "backend_mode": initial_backend_mode,
        "model": c.get("model") or "small",
        "language": c.get("language") or "",
        "recording_sample_rate": _int_or_default(c.get("recording_sample_rate"), 16000),
        "recording_format": (c.get("recording_format") or "flac").lower(),
        "recordings_dir": c.get("recordings_dir") or "",
        "compute_type_cuda": c.get("compute_type_cuda") or "float16",
        "compute_type_cpu": c.get("compute_type_cpu") or "int8",
        "remote_api_base_url": c.get("remote_api_base_url") or "",
        "remote_api_model": c.get("remote_api_model") or "",
        "remote_api_token_service": c.get("remote_api_token_service") or "remote_api",
        "remote_api_timeout_seconds": _int_or_default(c.get("remote_api_timeout_seconds"), 600),
        "remote_api_max_upload_mb": _int_or_default(c.get("remote_api_max_upload_mb"), 20),
        "remote_api_compress_codec": c.get("remote_api_compress_codec") or "opus",
        "remote_api_compress_bitrate": c.get("remote_api_compress_bitrate") or "16k",
    }
    initial_snapshot = dict(form)

    with (
        ui.dialog().props("maximized persistent") as dialog,
        ui.card().classes("settings-dialog"),
    ):
        # Header
        with ui.row().classes("settings-header items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("settings").classes("text-xl")
                ui.label("Settings").classes("settings-title")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=primary")

        # Tabs
        with ui.tabs().props("dense no-caps inline-label").classes("settings-tabs") as tabs:
            tab_general = ui.tab("General", icon="tune")
            tab_record = ui.tab("Recording", icon="mic")
            tab_local = ui.tab("Local backend", icon="memory")
            tab_remote = ui.tab("Remote API", icon="cloud")
            tab_tokens = ui.tab("Tokens", icon="key")
            tab_about = ui.tab("About", icon="info")

        with ui.tab_panels(tabs, value=tab_general).classes("settings-panels w-full"):
            # ---------- General ----------
            with ui.tab_panel(tab_general):
                _section_title("Backend mode")
                _section_help(
                    "How transcription runs on this machine. Local GPU is fastest "
                    "if you have a CUDA-capable NVIDIA card. Remote API offloads "
                    "the work to an OpenAI-compatible endpoint."
                )
                _backend_mode_picker(form, hw)

                _section_title("Whisper model")
                _section_help(
                    "Bigger models are more accurate but slower and need more "
                    "memory. 'small' is a good default for most CPUs/GPUs."
                )
                ui.select(
                    WHISPER_MODELS,
                    value=form["model"],
                    on_change=lambda e: form.update(model=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("Language")
                _section_help(
                    "Pick the spoken language to transcribe. 'Auto-detect' lets "
                    "the model decide on each track."
                )
                # Normalize the stored value: anything not in the known
                # list (custom code, blank, None) falls back to "" (Auto).
                lang_initial = form["language"] if form["language"] in LANGUAGE_OPTIONS else ""
                ui.select(
                    LANGUAGE_OPTIONS,
                    value=lang_initial,
                    with_input=True,
                    on_change=lambda e: form.update(language=e.value or ""),
                ).props("outlined dense").classes("w-full max-w-xs")

            # ---------- Recording ----------
            with ui.tab_panel(tab_record):
                _section_title("Sample rate")
                _section_help(
                    "Whisper and pyannote both resample to 16 kHz internally. "
                    "Higher rates only matter if you also want playback-quality archives."
                )
                ui.select(
                    {sr: f"{sr // 1000} kHz" for sr in SAMPLE_RATES},
                    value=form["recording_sample_rate"],
                    on_change=lambda e: form.update(recording_sample_rate=int(e.value)),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("File format")
                _section_help(
                    "FLAC is lossless and about half the size of WAV. "
                    "Use WAV only if you need raw uncompressed audio."
                )
                ui.select(
                    RECORDING_FORMATS,
                    value=form["recording_format"],
                    on_change=lambda e: form.update(recording_format=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("Recordings folder")
                _section_help(
                    "Where audio and transcripts are stored. Leave blank to "
                    "use ./recordings/ in the current working directory."
                )
                rec_dir_input = (
                    ui.input(
                        placeholder=str(recordings_dir()),
                        value=form["recordings_dir"],
                        on_change=lambda e: form.update(recordings_dir=e.value),
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Open current folder",
                        on_click=lambda: _open_path(recordings_dir()),
                    ).props("flat dense no-caps color=primary")
                    ui.button(
                        "Use default",
                        on_click=lambda: _reset_field(form, rec_dir_input, "recordings_dir", ""),
                    ).props("flat dense no-caps color=primary")

            # ---------- Local backend ----------
            with ui.tab_panel(tab_local):
                _section_title("CUDA compute type")
                _section_help(
                    "Precision used when running WhisperX on an NVIDIA GPU. "
                    "'float16' is the fastest, 'int8_float16' uses less VRAM."
                )
                ui.select(
                    CUDA_COMPUTE_TYPES,
                    value=form["compute_type_cuda"],
                    on_change=lambda e: form.update(compute_type_cuda=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("CPU compute type")
                _section_help(
                    "Precision used when running WhisperX on CPU. "
                    "'int8' is the only practical choice on most machines."
                )
                ui.select(
                    CPU_COMPUTE_TYPES,
                    value=form["compute_type_cpu"],
                    on_change=lambda e: form.update(compute_type_cpu=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("Detected hardware")
                with ui.element("div").classes("hw-grid"):
                    _hw_row("Platform", hw.platform)
                    _hw_row("CPU cores", str(hw.cpu_cores))
                    if hw.has_cuda:
                        _hw_row(
                            "GPU",
                            f"{hw.gpu_name} — {hw.vram_gb:.1f} GB VRAM",
                            kind="ok",
                        )
                        _hw_row("CUDA", hw.cuda_version or "unknown")
                        if hw.driver_version:
                            _hw_row("Driver", hw.driver_version)
                    elif hw.has_mps:
                        _hw_row("GPU", "Apple Silicon (MPS)", kind="ok")
                    else:
                        _hw_row("GPU", "none detected", kind="warn")
                    _hw_row("Python", hw.python_version)

            # ---------- Remote API ----------
            with ui.tab_panel(tab_remote):
                _section_title("Endpoint")
                _section_help(
                    "Any OpenAI-compatible /audio/transcriptions endpoint. "
                    "Pick a preset to prefill the base URL or type a custom one."
                )

                base_url_input = (
                    ui.input(
                        label="Base URL",
                        placeholder="https://api.openai.com/v1",
                        value=form["remote_api_base_url"],
                        on_change=lambda e: form.update(remote_api_base_url=e.value),
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )

                def _apply_preset(name: str) -> None:
                    url = REMOTE_PRESETS.get(name, "")
                    if url:
                        base_url_input.value = url
                        form["remote_api_base_url"] = url

                ui.select(
                    list(REMOTE_PRESETS.keys()),
                    label="Preset",
                    value="(custom)",
                    on_change=lambda e: _apply_preset(e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                remote_test_result = ui.label("").classes("wizard-test-result")

                async def _test_remote() -> None:
                    base = (form.get("remote_api_base_url") or "").strip()
                    slot = form.get("remote_api_token_service") or "remote_api"
                    token = cfg.get_token(slot) or ""
                    if not token:
                        remote_test_result.text = (
                            f"No API key stored under '{slot}' — add it in the Tokens tab first."
                        )
                        remote_test_result.classes(remove="ok pending", add="error")
                        return
                    remote_test_result.text = "Testing…"
                    remote_test_result.classes(remove="ok error", add="pending")
                    ok, msg = await run.io_bound(test_remote_connection, base, token)
                    remote_test_result.text = msg
                    remote_test_result.classes(remove="pending", add="ok" if ok else "error")

                ui.button("Test connection", on_click=_test_remote).props("outline dense no-caps")

                _section_title("Model")
                _section_help("Server-side model name, e.g. 'whisper-1' or 'whisper-large-v3'.")
                ui.input(
                    placeholder="whisper-1",
                    value=form["remote_api_model"],
                    on_change=lambda e: form.update(remote_api_model=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title("Timeout")
                _section_help("How long to wait for a single transcription request (seconds).")
                ui.number(
                    value=form["remote_api_timeout_seconds"],
                    min=10,
                    max=3600,
                    step=10,
                    on_change=lambda e: form.update(
                        remote_api_timeout_seconds=_int_or_default(e.value, 600)
                    ),
                ).props("outlined dense suffix=s").classes("w-full max-w-xs")

                _section_title("Upload compression")
                _section_help(
                    "Files larger than this threshold are re-encoded to mono "
                    "Opus before upload to stay under provider size caps "
                    "(OpenAI / Groq cap at 25 MB)."
                )
                with ui.row().classes("gap-3 w-full"):
                    ui.number(
                        label="Threshold (MB)",
                        value=form["remote_api_max_upload_mb"],
                        min=1,
                        max=500,
                        step=1,
                        on_change=lambda e: form.update(
                            remote_api_max_upload_mb=_int_or_default(e.value, 20)
                        ),
                    ).props("outlined dense").classes("max-w-[160px]")
                    ui.select(
                        COMPRESS_CODECS,
                        label="Codec",
                        value=form["remote_api_compress_codec"],
                        on_change=lambda e: form.update(remote_api_compress_codec=e.value),
                    ).props("outlined dense").classes("max-w-[140px]")
                    ui.input(
                        label="Bitrate",
                        placeholder="16k",
                        value=form["remote_api_compress_bitrate"],
                        on_change=lambda e: form.update(remote_api_compress_bitrate=e.value),
                    ).props("outlined dense").classes("max-w-[140px]")

                _section_title("Keyring slot")
                _section_help(
                    "Service name under which the API key is stored. "
                    "Defaults to 'remote_api'; pick a custom name to keep "
                    "multiple providers side by side."
                )
                ui.input(
                    placeholder="remote_api",
                    value=form["remote_api_token_service"],
                    on_change=lambda e: form.update(remote_api_token_service=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

            # ---------- Tokens ----------
            with ui.tab_panel(tab_tokens):
                _section_help(
                    "Tokens are stored in the OS keyring (Windows Credential "
                    "Manager / macOS Keychain / libsecret on Linux) — never on "
                    "disk in plain text."
                )
                tokens_container = ui.column().classes("w-full gap-3")
                _render_tokens(tokens_container, form["remote_api_token_service"])

                # Re-render when the keyring slot field changes so the row
                # under "Remote API" reflects the right service.
                ui.timer(
                    1.5,
                    lambda: _render_tokens(
                        tokens_container, form.get("remote_api_token_service") or "remote_api"
                    ),
                )

            # ---------- About ----------
            with ui.tab_panel(tab_about):
                _section_title("Paths")
                with ui.element("div").classes("hw-grid"):
                    _hw_row("Config file", str(config_file()))
                    _hw_row("Jobs DB", str(jobs_db()))
                    _hw_row("Logs", str(logs_dir()))
                    _hw_row("Recordings", str(recordings_dir()))
                with ui.row().classes("gap-2 mt-2"):
                    ui.button(
                        "Open config folder",
                        on_click=lambda: _open_path(config_file().parent),
                    ).props("flat dense no-caps color=primary")
                    ui.button(
                        "Open logs folder",
                        on_click=lambda: _open_path(logs_dir()),
                    ).props("flat dense no-caps color=primary")

                _section_title("First-run wizard")
                _section_help(
                    "Clears the backend mode so the welcome wizard is shown "
                    "again on next launch. Useful if you swapped hardware."
                )
                ui.button(
                    "Re-run wizard…",
                    on_click=lambda: _reset_wizard(),
                ).props("flat dense no-caps color=primary")

        # Footer
        with ui.row().classes("settings-footer items-center justify-end w-full"):
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps color=primary")
                ui.button(
                    "Save",
                    on_click=lambda: _save_all(form, initial_snapshot, dialog),
                ).props("color=positive unelevated no-caps")

    dialog.open()


# ---------- helpers ----------


def _section_title(text: str) -> None:
    ui.label(text).classes("settings-section-title")


def _section_help(text: str) -> None:
    ui.label(text).classes("settings-section-help")


def _hw_row(k: str, v: str, *, kind: str = "") -> None:
    klass = "hw-pair" + (f" {kind}" if kind else "")
    ui.html(
        f'<div class="{klass}"><span class="k">{html.escape(k)}</span>'
        f'<span class="v">{html.escape(v)}</span></div>'
    )


def _backend_mode_picker(form: dict[str, Any], hw: HardwareProbe) -> None:
    rows: dict[str, ui.element] = {}

    def _select(value: str, disabled: bool) -> None:
        if disabled:
            return
        form["backend_mode"] = value
        for v, row in rows.items():
            if v == value:
                row.classes(add="selected")
            else:
                row.classes(remove="selected")

    def _row(value: str, *, title: str, sub: str, disabled: bool = False) -> None:
        klass = "radio-row"
        if value == form["backend_mode"]:
            klass += " selected"
        if disabled:
            klass += " disabled"
        row = ui.row().classes(klass)
        row.on("click", lambda _v=value, _d=disabled: _select(_v, _d))
        with row:
            ui.html('<span class="dot"></span>')
            with ui.element("div").classes("body-l"):
                ui.html(f'<div class="title-l">{html.escape(title)}</div>')
                ui.html(f'<div class="sub-l">{html.escape(sub)}</div>')
        rows[value] = row

    with ui.element("div").classes("radio-list w-full"):
        _row(
            "local_gpu",
            title="Local — NVIDIA GPU",
            sub=(
                "WhisperX runs on your GPU. All data stays on this machine."
                if hw.has_cuda
                else "No CUDA GPU detected. Pick Local CPU or Remote API instead."
            ),
            disabled=not hw.has_cuda,
        )
        _row(
            "local_cpu",
            title="Local — CPU only",
            sub="Slow (~30-60 min per hour of audio), but all-local and works on any machine.",
        )
        _row(
            "remote_api",
            title="Remote API",
            sub=(
                "Send audio to an OpenAI-compatible endpoint. Configure the URL, "
                "model, and API token in the Remote API and Tokens tabs."
            ),
        )


def _render_tokens(container: ui.element, remote_service: str) -> None:
    """Render the list of known token rows. Re-invokable to reflect changes."""
    container.clear()
    # Known services + the live remote_api_token_service (in case the user
    # picked a custom slot name).
    services: list[tuple[str, str]] = []
    seen: set[str] = set()
    for svc, desc in cfg.KNOWN_TOKEN_SERVICES.items():
        services.append((svc, desc))
        seen.add(svc)
    if remote_service and remote_service not in seen:
        services.append((remote_service, f"Custom remote API key slot '{remote_service}'"))

    with container:
        for svc, desc in services:
            present = cfg.get_token(svc) is not None
            with ui.row().classes("token-row w-full items-center"):
                with ui.column().classes("token-meta"):
                    ui.html(f'<div class="token-name">{html.escape(svc)}</div>')
                    ui.html(f'<div class="token-desc">{html.escape(desc)}</div>')
                ui.html(
                    f'<span class="token-status {"set" if present else "missing"}">'
                    f"{'set' if present else 'not set'}</span>"
                )
                with ui.row().classes("gap-1 ml-auto"):
                    if present:

                        async def _test(_e=None, s: str = svc) -> None:
                            token = cfg.get_token(s) or ""
                            if s == "huggingface":
                                ok, msg = await run.io_bound(test_huggingface_token, token)
                            else:
                                base = (cfg.load_config().get("remote_api_base_url") or "").strip()
                                if not base:
                                    ui.notify(
                                        "Set the Remote API base URL first (Remote API tab).",
                                        type="warning",
                                    )
                                    return
                                ok, msg = await run.io_bound(test_remote_connection, base, token)
                            ui.notify(msg, type="positive" if ok else "negative")

                        ui.button("Test", on_click=_test).props("flat dense no-caps color=primary")
                    ui.button(
                        "Set…" if not present else "Replace…",
                        on_click=lambda _e=None, s=svc, d=desc: _set_token_dialog(
                            s, d, lambda: _render_tokens(container, remote_service)
                        ),
                    ).props("flat dense no-caps color=primary")
                    if present:

                        def _remove(_e=None, s: str = svc) -> None:
                            cfg.remove_token(s)
                            ui.notify(f"Removed token for '{s}'.", type="warning")
                            _render_tokens(container, remote_service)

                        ui.button("Remove", on_click=_remove).props(
                            "flat dense no-caps color=negative"
                        )


def _reset_field(form: dict[str, Any], widget: Any, key: str, value: Any) -> None:
    form[key] = value
    widget.value = value
    widget.update()


def _open_path(path: Path) -> None:
    import os
    import subprocess
    import sys as _sys

    if not path.exists():
        ui.notify(f"Path does not exist: {path}", type="warning")
        return
    if _sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif _sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _reset_wizard() -> None:
    """Clear backend_mode so the first-run wizard reappears on next load."""
    c = cfg.load_config()
    c["backend_mode"] = None
    cfg.save_config(c)
    ui.notify("Wizard will run on next launch.", type="positive")
    _confirm_reload("The first-run wizard will appear after reload.")


def _save_all(form: dict[str, Any], initial: dict[str, Any], dialog: ui.dialog) -> None:
    """Flush form state to config.toml. Prompt for reload when needed."""
    c = cfg.load_config()

    # Map each form key to its config key + a coercer. None/blank values
    # become None on disk, which save_config strips out so the default
    # from DEFAULT_CONFIG kicks back in.
    c["backend_mode"] = form["backend_mode"]
    c["model"] = form["model"]
    c["language"] = _none_if_blank(form.get("language"))
    c["recording_sample_rate"] = _int_or_default(form["recording_sample_rate"], 16000)
    c["recording_format"] = form["recording_format"]
    c["recordings_dir"] = _none_if_blank(form.get("recordings_dir"))
    c["compute_type_cuda"] = form["compute_type_cuda"]
    c["compute_type_cpu"] = form["compute_type_cpu"]
    c["remote_api_base_url"] = _none_if_blank(form.get("remote_api_base_url"))
    c["remote_api_model"] = _none_if_blank(form.get("remote_api_model"))
    c["remote_api_token_service"] = (
        _none_if_blank(form.get("remote_api_token_service")) or "remote_api"
    )
    c["remote_api_timeout_seconds"] = _int_or_default(form["remote_api_timeout_seconds"], 600)
    c["remote_api_max_upload_mb"] = _int_or_default(form["remote_api_max_upload_mb"], 20)
    c["remote_api_compress_codec"] = form["remote_api_compress_codec"]
    c["remote_api_compress_bitrate"] = form["remote_api_compress_bitrate"]

    try:
        cfg.save_config(c)
    except Exception as e:  # pragma: no cover - disk error
        log.exception("save_config failed")
        ui.notify(f"Failed to save settings: {e}", type="negative")
        return

    ui.notify("Settings saved.", type="positive")
    log.info("Settings saved via GUI")

    # Some keys only take effect after a fresh worker / page load.
    reload_keys = (
        "backend_mode",
        "recordings_dir",
        "compute_type_cuda",
        "compute_type_cpu",
        "remote_api_base_url",
        "remote_api_model",
        "remote_api_token_service",
    )
    if any(form.get(k) != initial.get(k) for k in reload_keys):
        dialog.close()
        _confirm_reload(
            "Some changes (backend mode, paths, remote API endpoint) "
            "only take effect after a reload."
        )
    else:
        dialog.close()
