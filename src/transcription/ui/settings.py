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
from ..i18n import SUPPORTED_LANGUAGES, t
from ..paths import config_file, jobs_db, logs_dir, recordings_dir
from ..pipeline.hardware import HardwareProbe

log = logging.getLogger(__name__)


WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
CUDA_COMPUTE_TYPES = ["float16", "float32", "int8_float16", "int8"]
CPU_COMPUTE_TYPES = ["int8", "float32"]
RECORDING_FORMATS = ["opus", "flac", "wav"]
SAMPLE_RATES = [16000, 22050, 44100, 48000]
COMPRESS_CODECS = ["opus", "mp3"]

# Subset of ISO 639-1 codes Whisper handles well, in display order ("auto"
# first). The empty-string code maps to auto-detection in the config layer.
# Display names come from the i18n catalog ("lang.<code>") at render time.
LANGUAGE_CODES: tuple[str, ...] = (
    "",
    "ar",
    "zh",
    "cs",
    "da",
    "nl",
    "en",
    "fi",
    "fr",
    "de",
    "el",
    "he",
    "hi",
    "hu",
    "id",
    "it",
    "ja",
    "ko",
    "no",
    "pl",
    "pt",
    "ro",
    "ru",
    "es",
    "sv",
    "tr",
    "uk",
    "vi",
)


def _language_options() -> dict[str, str]:
    """Build {code: localized name} for the spoken-language picker."""
    return {code: t("lang.auto") if code == "" else t(f"lang.{code}") for code in LANGUAGE_CODES}


# Groq's OpenAI-compatible endpoint + a fast, accurate default model. The
# first-run wizard steers users here (best results for most people), so these
# are shared between the wizard's one-click preset and the presets drop-down.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "whisper-large-v3"

# Common OpenAI-compatible endpoints — drop-down presets that just prefill
# the base URL field. User can still type anything.
REMOTE_PRESETS = {
    "(custom)": "",
    "OpenAI": "https://api.openai.com/v1",
    "Groq": GROQ_BASE_URL,
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
        ui.label(t("settings.set_token_title", label=label)).classes("dlg-title")
        ui.label(t("settings.set_token_sub", service=service)).classes("dlg-sub")
        token_input = (
            ui.input(label=t("input.token"), password=True, password_toggle_button=True)
            .props("autofocus outlined dense")
            .classes("w-full")
        )

        def _save() -> None:
            token = (token_input.value or "").strip()
            if not token:
                ui.notify(t("settings.token_empty"), type="warning")
                return
            try:
                cfg.set_token(service, token)
                ui.notify(t("settings.token_saved", service=service), type="positive")
                on_done()
                d.close()
            except Exception as e:  # pragma: no cover - keyring backend errors
                log.exception("set_token failed")
                ui.notify(t("settings.token_store_failed", error=e), type="negative")

        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button(t("action.cancel"), on_click=d.close).props("flat no-caps color=primary")
            ui.button(t("action.save"), on_click=_save).props("color=primary unelevated no-caps")
    d.open()


def _confirm_reload(message: str) -> None:
    """Offer to reload the UI when a saved change needs a restart to take effect."""
    with ui.dialog() as d, ui.card().classes("min-w-[420px] gap-3"):
        ui.label(t("settings.reload_required")).classes("dlg-title")
        ui.label(message).classes("dlg-sub")

        def _reload() -> None:
            d.close()
            ui.navigate.reload()

        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button(t("action.later"), on_click=d.close).props("flat no-caps color=primary")
            ui.button(t("action.reload_now"), on_click=_reload).props(
                "color=primary unelevated no-caps"
            )
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
        "ui_language": c.get("ui_language") or "",
        "recording_sample_rate": _int_or_default(c.get("recording_sample_rate"), 16000),
        "recording_format": (c.get("recording_format") or "opus").lower(),
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
                ui.label(t("settings.title")).classes("settings-title")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense color=primary")

        # Tabs
        with ui.tabs().props("dense no-caps inline-label").classes("settings-tabs") as tabs:
            tab_general = ui.tab("general", label=t("settings.tab.general"), icon="tune")
            tab_record = ui.tab("recording", label=t("settings.tab.recording"), icon="mic")
            tab_local = ui.tab("local", label=t("settings.tab.local"), icon="memory")
            tab_remote = ui.tab("remote", label=t("settings.tab.remote"), icon="cloud")
            tab_tokens = ui.tab("tokens", label=t("settings.tab.tokens"), icon="key")
            tab_about = ui.tab("about", label=t("settings.tab.about"), icon="info")

        with ui.tab_panels(tabs, value=tab_general).classes("settings-panels w-full"):
            # ---------- General ----------
            with ui.tab_panel(tab_general):
                _section_title(t("settings.ui_language"))
                _section_help(t("settings.ui_language_help"))
                # "" => auto-detect from OS locale.
                ui_lang_options = {"": t("lang.auto"), **dict(SUPPORTED_LANGUAGES)}
                ui_lang_initial = (
                    form["ui_language"] if form["ui_language"] in SUPPORTED_LANGUAGES else ""
                )
                ui.select(
                    ui_lang_options,
                    value=ui_lang_initial,
                    on_change=lambda e: form.update(ui_language=e.value or ""),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.backend_mode"))
                _section_help(t("settings.backend_mode_help"))
                _backend_mode_picker(form, hw)

                _section_title(t("settings.whisper_model"))
                _section_help(t("settings.whisper_model_help"))
                ui.select(
                    WHISPER_MODELS,
                    value=form["model"],
                    on_change=lambda e: form.update(model=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.language"))
                _section_help(t("settings.language_help"))
                # Normalize the stored value: anything not in the known
                # list (custom code, blank, None) falls back to "" (Auto).
                lang_initial = form["language"] if form["language"] in LANGUAGE_CODES else ""
                ui.select(
                    _language_options(),
                    value=lang_initial,
                    with_input=True,
                    on_change=lambda e: form.update(language=e.value or ""),
                ).props("outlined dense").classes("w-full max-w-xs")

            # ---------- Recording ----------
            with ui.tab_panel(tab_record):
                _section_title(t("settings.sample_rate"))
                _section_help(t("settings.sample_rate_help"))
                ui.select(
                    {sr: f"{sr // 1000} kHz" for sr in SAMPLE_RATES},
                    value=form["recording_sample_rate"],
                    on_change=lambda e: form.update(recording_sample_rate=int(e.value)),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.file_format"))
                _section_help(t("settings.file_format_help"))
                ui.select(
                    RECORDING_FORMATS,
                    value=form["recording_format"],
                    on_change=lambda e: form.update(recording_format=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.recordings_folder"))
                _section_help(t("settings.recordings_folder_help"))
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
                        t("settings.open_current_folder"),
                        on_click=lambda: _open_path(recordings_dir()),
                    ).props("flat dense no-caps color=primary")
                    ui.button(
                        t("settings.use_default"),
                        on_click=lambda: _reset_field(form, rec_dir_input, "recordings_dir", ""),
                    ).props("flat dense no-caps color=primary")

            # ---------- Local backend ----------
            with ui.tab_panel(tab_local):
                _section_title(t("settings.cuda_compute"))
                _section_help(t("settings.cuda_compute_help"))
                ui.select(
                    CUDA_COMPUTE_TYPES,
                    value=form["compute_type_cuda"],
                    on_change=lambda e: form.update(compute_type_cuda=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.cpu_compute"))
                _section_help(t("settings.cpu_compute_help"))
                ui.select(
                    CPU_COMPUTE_TYPES,
                    value=form["compute_type_cpu"],
                    on_change=lambda e: form.update(compute_type_cpu=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.detected_hw"))
                with ui.element("div").classes("hw-grid"):
                    _hw_row(t("hw.platform"), hw.platform)
                    _hw_row(t("hw.cpu_cores"), str(hw.cpu_cores))
                    if hw.has_cuda:
                        _hw_row(
                            t("hw.gpu"),
                            f"{hw.gpu_name} — {hw.vram_gb:.1f} GB VRAM",
                            kind="ok",
                        )
                        _hw_row(t("hw.cuda"), hw.cuda_version or t("hw.cuda_unknown"))
                        if hw.driver_version:
                            _hw_row(t("hw.driver"), hw.driver_version)
                    elif hw.has_mps:
                        _hw_row(t("hw.gpu"), t("hw.gpu_apple"), kind="ok")
                    else:
                        _hw_row(t("hw.gpu"), t("hw.gpu_none"), kind="warn")
                    _hw_row(t("hw.python"), hw.python_version)

            # ---------- Remote API ----------
            with ui.tab_panel(tab_remote):
                _section_title(t("settings.endpoint"))
                _section_help(t("settings.endpoint_help"))

                base_url_input = (
                    ui.input(
                        label=t("input.base_url"),
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
                    label=t("input.preset"),
                    value="(custom)",
                    on_change=lambda e: _apply_preset(e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                remote_test_result = ui.label("").classes("wizard-test-result")

                async def _test_remote() -> None:
                    base = (form.get("remote_api_base_url") or "").strip()
                    slot = form.get("remote_api_token_service") or "remote_api"
                    token = cfg.get_token(slot) or ""
                    if not token:
                        remote_test_result.text = t("settings.remote_no_key", slot=slot)
                        remote_test_result.classes(remove="ok pending", add="error")
                        return
                    remote_test_result.text = t("common.testing")
                    remote_test_result.classes(remove="ok error", add="pending")
                    ok, msg = await run.io_bound(test_remote_connection, base, token)
                    remote_test_result.text = msg
                    remote_test_result.classes(remove="pending", add="ok" if ok else "error")

                ui.button(t("action.test_connection"), on_click=_test_remote).props(
                    "outline dense no-caps"
                )

                _section_title(t("settings.remote_model"))
                _section_help(t("settings.remote_model_help"))
                ui.input(
                    placeholder="whisper-1",
                    value=form["remote_api_model"],
                    on_change=lambda e: form.update(remote_api_model=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

                _section_title(t("settings.timeout"))
                _section_help(t("settings.timeout_help"))
                ui.number(
                    value=form["remote_api_timeout_seconds"],
                    min=10,
                    max=3600,
                    step=10,
                    on_change=lambda e: form.update(
                        remote_api_timeout_seconds=_int_or_default(e.value, 600)
                    ),
                ).props("outlined dense suffix=s").classes("w-full max-w-xs")

                _section_title(t("settings.upload_compression"))
                _section_help(t("settings.upload_compression_help"))
                with ui.row().classes("gap-3 w-full"):
                    ui.number(
                        label=t("settings.threshold_mb"),
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
                        label=t("settings.codec"),
                        value=form["remote_api_compress_codec"],
                        on_change=lambda e: form.update(remote_api_compress_codec=e.value),
                    ).props("outlined dense").classes("max-w-[140px]")
                    ui.input(
                        label=t("settings.bitrate"),
                        placeholder="16k",
                        value=form["remote_api_compress_bitrate"],
                        on_change=lambda e: form.update(remote_api_compress_bitrate=e.value),
                    ).props("outlined dense").classes("max-w-[140px]")

                _section_title(t("settings.keyring_slot"))
                _section_help(t("settings.keyring_slot_help"))
                ui.input(
                    placeholder="remote_api",
                    value=form["remote_api_token_service"],
                    on_change=lambda e: form.update(remote_api_token_service=e.value),
                ).props("outlined dense").classes("w-full max-w-xs")

            # ---------- Tokens ----------
            with ui.tab_panel(tab_tokens):
                _section_help(t("settings.tokens_help"))
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
                _section_title(t("settings.paths"))
                with ui.element("div").classes("hw-grid"):
                    _hw_row(t("settings.path.config"), str(config_file()))
                    _hw_row(t("settings.path.jobs_db"), str(jobs_db()))
                    _hw_row(t("settings.path.logs"), str(logs_dir()))
                    _hw_row(t("settings.path.recordings"), str(recordings_dir()))
                with ui.row().classes("gap-2 mt-2"):
                    ui.button(
                        t("settings.open_config_folder"),
                        on_click=lambda: _open_path(config_file().parent),
                    ).props("flat dense no-caps color=primary")
                    ui.button(
                        t("settings.open_logs_folder"),
                        on_click=lambda: _open_path(logs_dir()),
                    ).props("flat dense no-caps color=primary")

                _section_title(t("settings.first_run_wizard"))
                _section_help(t("settings.first_run_wizard_help"))
                ui.button(
                    t("settings.rerun_wizard"),
                    on_click=lambda: _reset_wizard(),
                ).props("flat dense no-caps color=primary")

        # Footer
        with ui.row().classes("settings-footer items-center justify-end w-full"):
            with ui.row().classes("gap-2"):
                ui.button(t("action.cancel"), on_click=dialog.close).props(
                    "flat no-caps color=primary"
                )
                ui.button(
                    t("action.save"),
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
        services.append((remote_service, t("settings.token_custom_desc", slot=remote_service)))

    with container:
        for svc, desc in services:
            present = cfg.get_token(svc) is not None
            with ui.row().classes("token-row w-full items-center"):
                with ui.column().classes("token-meta"):
                    ui.html(f'<div class="token-name">{html.escape(svc)}</div>')
                    ui.html(f'<div class="token-desc">{html.escape(desc)}</div>')
                ui.html(
                    f'<span class="token-status {"set" if present else "missing"}">'
                    f"{html.escape(t('settings.token_set') if present else t('settings.token_not_set'))}</span>"
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
                                        t("settings.set_base_url_first"),
                                        type="warning",
                                    )
                                    return
                                ok, msg = await run.io_bound(test_remote_connection, base, token)
                            ui.notify(msg, type="positive" if ok else "negative")

                        ui.button(t("action.test"), on_click=_test).props(
                            "flat dense no-caps color=primary"
                        )
                    ui.button(
                        t("action.set") if not present else t("action.replace"),
                        on_click=lambda _e=None, s=svc, d=desc: _set_token_dialog(
                            s, d, lambda: _render_tokens(container, remote_service)
                        ),
                    ).props("flat dense no-caps color=primary")
                    if present:

                        def _remove(_e=None, s: str = svc) -> None:
                            cfg.remove_token(s)
                            ui.notify(t("settings.token_removed", service=s), type="warning")
                            _render_tokens(container, remote_service)

                        ui.button(t("action.remove"), on_click=_remove).props(
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
        ui.notify(t("settings.path_missing", path=path), type="warning")
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
    ui.notify(t("settings.wizard_next_launch"), type="positive")
    _confirm_reload(t("settings.wizard_reload_message"))


def _save_all(form: dict[str, Any], initial: dict[str, Any], dialog: ui.dialog) -> None:
    """Flush form state to config.toml. Prompt for reload when needed."""
    c = cfg.load_config()

    # Map each form key to its config key + a coercer. None/blank values
    # become None on disk, which save_config strips out so the default
    # from DEFAULT_CONFIG kicks back in.
    c["backend_mode"] = form["backend_mode"]
    c["model"] = form["model"]
    c["language"] = _none_if_blank(form.get("language"))
    c["ui_language"] = _none_if_blank(form.get("ui_language"))
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
        ui.notify(t("settings.save_failed", error=e), type="negative")
        return

    ui.notify(t("settings.saved"), type="positive")
    log.info("Settings saved via GUI")

    # Some keys only take effect after a fresh worker / page load. ui_language
    # is read when the page is built, so it needs a reload to re-render text.
    reload_keys = (
        "backend_mode",
        "recordings_dir",
        "compute_type_cuda",
        "compute_type_cpu",
        "remote_api_base_url",
        "remote_api_model",
        "remote_api_token_service",
        "ui_language",
    )
    if any(form.get(k) != initial.get(k) for k in reload_keys):
        dialog.close()
        _confirm_reload(t("settings.reload_message"))
    else:
        dialog.close()
