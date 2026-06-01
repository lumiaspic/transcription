"""Tiny dependency-free i18n layer for the GUI.

English is the source language and the fallback for any missing key. A second
catalog (French) lives alongside it; adding a language is just adding another
entry to ``_CATALOG`` (plus its weekday/month abbreviations).

Design notes:
- ``t(key, **kwargs)`` looks up the active language, falls back to English,
  then to the key itself, and applies ``str.format`` interpolation.
- The active language is a module global defaulting to ``"en"`` so non-GUI
  code paths (and the test suite) get English without any setup.
- The NiceGUI page reads the configured language once per connection and calls
  ``set_language`` before building the UI; changing it needs a page reload,
  which the settings dialog already offers for other keys.
- No NiceGUI import here, so ``humanize`` (also NiceGUI-free) can use it and
  stay unit-testable.
"""

from __future__ import annotations

import locale as _locale
import os as _os

# Languages offered in the UI: code -> endonym shown in the picker.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "fr": "Français",
}

_DEFAULT = "en"
_current = _DEFAULT


# Weekday (Mon=0) and month (Jan=1) abbreviations per language. Kept out of the
# main catalog because they are indexed lists, not format strings. The English
# values match ``datetime.strftime('%a' / '%b')`` so existing output is stable.
_WEEKDAYS: dict[str, list[str]] = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "fr": ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
}
_MONTHS: dict[str, list[str]] = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "fr": [
        "janv.",
        "févr.",
        "mars",
        "avr.",
        "mai",
        "juin",
        "juil.",
        "août",
        "sept.",
        "oct.",
        "nov.",
        "déc.",
    ],
}


_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # ----- relative dates (humanize_recording_id) -----
        "date.today": "Today at {time}",
        "date.yesterday": "Yesterday at {time}",
        "date.weekday": "{wd} at {time}",
        "date.same_year": "{mon} {day} at {time}",
        "date.other_year": "{mon} {day}, {year} at {time}",
        # ----- humanized error labels -----
        "err.cuda_oom": "Graphics card memory exhausted",
        "err.oom": "Out of memory",
        "err.file_not_found": "File not found",
        "err.permission": "Permission denied",
        "err.connection": "Could not reach the server",
        "err.timeout": "Request timed out",
        "err.unauthorized": "Invalid API key",
        "err.forbidden": "Access forbidden by the server",
        "err.rate_limit": "Rate limit reached — try again later",
        "err.server": "Remote server error",
        "err.ffmpeg": "Audio conversion failed",
        "err.diarize": "Speaker diarization failed",
        "err.gpu": "GPU error",
        # ----- job status -----
        "status.pending.label": "Waiting",
        "status.running.label": "Processing",
        "status.done.label": "Done",
        "status.failed.label": "Failed",
        "status.pending.tip": "Queued — waiting for the background service to pick it up.",
        "status.running.tip": "Currently being transcribed.",
        "status.done.tip": "Transcription finished. Click the row to open the folder.",
        "status.failed.tip": "Transcription failed. Click the row for details.",
        # ----- notifications -----
        "notify.loopback_unavailable": "System audio capture unavailable: {error}",
        "notify.recording_started": "Recording started: {rec_id}",
        "notify.recording_stopped": "Stopped after {elapsed}. Enqueued as job #{job_id}.",
        # ----- common actions -----
        "action.close": "Close",
        "action.retry": "Retry",
        "action.cancel": "Cancel",
        "action.save": "Save",
        "action.continue": "Continue",
        "action.back": "Back",
        "action.skip_for_now": "Skip for now",
        "action.save_and_finish": "Save and finish",
        "action.test_token": "Test token",
        "action.test_connection": "Test connection",
        "action.use_groq": "Use Groq",
        "action.later": "Later",
        "action.reload_now": "Reload now",
        "common.testing": "Testing…",
        # ----- job error dialog -----
        "dialog.job_title": "Job #{job_id} — {rec_id}",
        "dialog.job_status": "Status: {status}",
        "dialog.error": "Error",
        "dialog.no_error": "(no error message)",
        # ----- header -----
        "header.worker": "Background service",
        "header.worker_alive": "Background service is running — new recordings will be transcribed automatically.",
        "header.worker_dead": "Background service is not running. Restart the app to recover.",
        "header.settings": "Settings",
        # ----- setup banner -----
        "banner.title": "Finish setup",
        "banner.hf_missing": "Speaker labels are off. Add a free HuggingFace token to tell different speakers apart on the system track.",
        "banner.remote_missing": "Remote API isn't fully set up — missing: {missing}.",
        "banner.missing.endpoint": "endpoint URL",
        "banner.missing.model": "model",
        "banner.missing.api_key": "API key",
        "banner.open_settings": "Open settings",
        # ----- recording card -----
        "card.recording": "Recording",
        "rec.start": "● Start",
        "rec.stop": "■ Stop",
        "rec.ready": "Ready",
        "rec.ready_tip": "Press Start to begin a new recording.",
        # ----- jobs card -----
        "card.jobs": "Jobs queue",
        "card.jobs_tip": "Transcription work currently running, queued, or recently finished.",
        "table.recording": "Recording",
        "table.status": "Status",
        "table.created": "Created",
        "table.error": "Error",
        # ----- recordings card -----
        "card.recordings": "Recordings (recent)",
        "card.recordings_tip": "Audio files captured on this machine. Click 'Show in folder' to open the file location.",
        "recs.empty": "No recordings yet — press Start above to make one.",
        "recs.transcribed_tip": "Transcribed — folder contains text and subtitles.",
        "recs.pending_tip": "Audio captured, transcription pending.",
        "recs.show_in_folder": "Show in folder",
        "recs.show_in_folder_tip": "Open the folder containing the audio files and transcripts.",
        # ----- wizard -----
        "wizard.complete_notify": "Setup complete — loading the app…",
        "wizard.welcome": "Welcome",
        "wizard.backend_lead": "Step 1 of 2 — pick how transcription should run on this machine. You can change this later from the Settings menu.",
        "wizard.detected_hw": "Detected hardware",
        "wizard.hw_platform": "• Platform : {value}",
        "wizard.hw_cpu": "• CPU      : {cores} cores",
        "wizard.hw_gpu": "• GPU      : {value}",
        "wizard.hw_gpu_none": "• GPU      : none detected",
        "wizard.hw_python": "• Python   : {value}",
        "wizard.backend_mode": "Backend mode",
        "wizard.no_cuda_notify": "No CUDA GPU detected — pick Local CPU instead.",
        "wizard.hf_hero": "Speaker labels (optional)",
        "wizard.hf_lead": "Step 2 of 2 — to tell speakers apart on the system track, the app uses pyannote, which needs a free HuggingFace token. Skip this and transcription still works — everyone on the system track is just labelled as one speaker.",
        "wizard.hf_get_token": "Get your free token (2 minutes)",
        "wizard.hf_step1_pre": "Create a free account and a ",
        "wizard.hf_step1_read": "read",
        "wizard.hf_step1_post": " token at ",
        "wizard.hf_step2_pre": "Accept the model licence (one click) at ",
        "wizard.hf_step2_link": "the pyannote model page",
        "wizard.hf_step2_post": ".",
        "wizard.hf_step3": "Paste the token below.",
        "wizard.hf_paste_first": "Paste a token first, or use 'Skip for now'.",
        "wizard.token_save_failed": "Couldn't save the token: {error}",
        "wizard.remote_hero": "Connect your transcription API",
        "wizard.remote_lead": "Step 2 of 2 — Groq is the recommended provider for fast, accurate results. Set it up below in three steps, or point the app at any OpenAI-compatible endpoint. You can change this later in Settings → Remote API.",
        "wizard.groq_get_key": "Get your free Groq API key (2 minutes)",
        "wizard.groq_step1_pre": "Create a free account and an API key at ",
        "wizard.groq_step2": "Click “Use Groq” below to fill in the endpoint and model.",
        "wizard.groq_step3": "Paste your key into the form below, then save.",
        "wizard.groq_applied": "Groq endpoint and model filled in — just paste your API key below.",
        "wizard.endpoint": "Endpoint",
        "wizard.model_hint": "e.g. 'whisper-large-v3' (Groq) or 'whisper-1' (OpenAI).",
        "wizard.remote_fill": "Fill in the endpoint URL, model, and API key.",
        "wizard.settings_save_failed": "Couldn't save settings: {error}",
        # ----- backend modes (wizard + settings) -----
        "backend.local_gpu.title": "Local — NVIDIA GPU",
        "backend.local_gpu.sub_ok": "WhisperX runs on your GPU. All data stays on this machine.",
        "backend.local_gpu.sub_none": "No CUDA GPU detected. Pick Local CPU instead.",
        "backend.local_gpu.sub_none_settings": "No CUDA GPU detected. Pick Local CPU or Remote API instead.",
        "backend.local_cpu.title": "Local — CPU only",
        "backend.local_cpu.sub": "Slow (~30-60 min per hour of audio), but all-local and works on any machine.",
        "backend.remote.title": "Remote API",
        "backend.remote.sub_wizard": "Any OpenAI-compatible /audio/transcriptions endpoint (OpenAI, Groq, self-hosted whisper.cpp). No diarization — both tracks transcribe as single speakers.",
        "backend.remote.sub_settings": "Send audio to an OpenAI-compatible endpoint. Configure the URL, model, and API token in the Remote API and Tokens tabs.",
        "tag.recommended": "recommended",
        "tag.disabled": "disabled",
        # ----- settings: inputs / shared -----
        "input.token": "Token",
        "input.hf_token": "HuggingFace token",
        "input.base_url": "Base URL",
        "input.quick_presets": "Quick presets",
        "input.preset": "Preset",
        "input.model": "Model",
        "input.api_key": "API key",
        # ----- settings: token dialog -----
        "settings.set_token_title": "Set {label}",
        "settings.set_token_sub": "Stored in the OS keyring under service '{service}'. Never written to disk in plain text.",
        "settings.token_empty": "Empty token — not saved.",
        "settings.token_saved": "Token for '{service}' saved in keyring.",
        "settings.token_store_failed": "Failed to store token: {error}",
        # ----- settings: reload prompt -----
        "settings.reload_required": "Reload required",
        "settings.reload_message": "Some changes (backend mode, paths, remote API endpoint) only take effect after a reload.",
        "settings.wizard_reload_message": "The first-run wizard will appear after reload.",
        # ----- settings: dialog chrome -----
        "settings.title": "Settings",
        "settings.tab.general": "General",
        "settings.tab.recording": "Recording",
        "settings.tab.local": "Local backend",
        "settings.tab.remote": "Remote API",
        "settings.tab.tokens": "Tokens",
        "settings.tab.about": "About",
        # ----- settings: general tab -----
        "settings.backend_mode": "Backend mode",
        "settings.backend_mode_help": "How transcription runs on this machine. Local GPU is fastest if you have a CUDA-capable NVIDIA card. Remote API offloads the work to an OpenAI-compatible endpoint.",
        "settings.whisper_model": "Whisper model",
        "settings.whisper_model_help": "Bigger models are more accurate but slower and need more memory. 'small' is a good default for most CPUs/GPUs.",
        "settings.language": "Language",
        "settings.language_help": "Pick the spoken language to transcribe. 'Auto-detect' lets the model decide on each track.",
        "settings.ui_language": "Display language",
        "settings.ui_language_help": "Language of the app's buttons and labels. Changing it reloads the app.",
        # ----- settings: recording tab -----
        "settings.sample_rate": "Sample rate",
        "settings.sample_rate_help": "Whisper and pyannote both resample to 16 kHz internally. Higher rates only matter if you also want playback-quality archives.",
        "settings.file_format": "File format",
        "settings.file_format_help": "Opus is compressed and about 10× smaller than FLAC, ideal for speech — the recommended default. FLAC is lossless (larger); use WAV only for raw uncompressed audio.",
        "settings.recordings_folder": "Recordings folder",
        "settings.recordings_folder_help": "Where audio and transcripts are stored. Leave blank to use ./recordings/ in the current working directory.",
        "settings.open_current_folder": "Open current folder",
        "settings.use_default": "Use default",
        # ----- settings: local backend tab -----
        "settings.cuda_compute": "CUDA compute type",
        "settings.cuda_compute_help": "Precision used when running WhisperX on an NVIDIA GPU. 'float16' is the fastest, 'int8_float16' uses less VRAM.",
        "settings.cpu_compute": "CPU compute type",
        "settings.cpu_compute_help": "Precision used when running WhisperX on CPU. 'int8' is the only practical choice on most machines.",
        "settings.detected_hw": "Detected hardware",
        "hw.platform": "Platform",
        "hw.cpu_cores": "CPU cores",
        "hw.gpu": "GPU",
        "hw.gpu_apple": "Apple Silicon (MPS)",
        "hw.gpu_none": "none detected",
        "hw.cuda": "CUDA",
        "hw.cuda_unknown": "unknown",
        "hw.driver": "Driver",
        "hw.python": "Python",
        # ----- settings: remote tab -----
        "settings.endpoint": "Endpoint",
        "settings.endpoint_help": "Any OpenAI-compatible /audio/transcriptions endpoint. Pick a preset to prefill the base URL or type a custom one.",
        "settings.remote_no_key": "No API key stored under '{slot}' — add it in the Tokens tab first.",
        "settings.remote_model": "Model",
        "settings.remote_model_help": "Server-side model name, e.g. 'whisper-1' or 'whisper-large-v3'.",
        "settings.timeout": "Timeout",
        "settings.timeout_help": "How long to wait for a single transcription request (seconds).",
        "settings.upload_compression": "Upload compression",
        "settings.upload_compression_help": "Files larger than this threshold are re-encoded to mono Opus before upload to stay under provider size caps (OpenAI / Groq cap at 25 MB).",
        "settings.threshold_mb": "Threshold (MB)",
        "settings.codec": "Codec",
        "settings.bitrate": "Bitrate",
        "settings.keyring_slot": "Keyring slot",
        "settings.keyring_slot_help": "Service name under which the API key is stored. Defaults to 'remote_api'; pick a custom name to keep multiple providers side by side.",
        # ----- settings: tokens tab -----
        "settings.tokens_help": "Tokens are stored in the OS keyring (Windows Credential Manager / macOS Keychain / libsecret on Linux) — never on disk in plain text.",
        "settings.token_set": "set",
        "settings.token_not_set": "not set",
        "settings.token_custom_desc": "Custom remote API key slot '{slot}'",
        "action.test": "Test",
        "action.set": "Set…",
        "action.replace": "Replace…",
        "action.remove": "Remove",
        "settings.set_base_url_first": "Set the Remote API base URL first (Remote API tab).",
        "settings.token_removed": "Removed token for '{service}'.",
        # ----- settings: about tab -----
        "settings.paths": "Paths",
        "settings.path.config": "Config file",
        "settings.path.jobs_db": "Jobs DB",
        "settings.path.logs": "Logs",
        "settings.path.recordings": "Recordings",
        "settings.open_config_folder": "Open config folder",
        "settings.open_logs_folder": "Open logs folder",
        "settings.first_run_wizard": "First-run wizard",
        "settings.first_run_wizard_help": "Clears the backend mode so the welcome wizard is shown again on next launch. Useful if you swapped hardware.",
        "settings.rerun_wizard": "Re-run wizard…",
        "settings.wizard_next_launch": "Wizard will run on next launch.",
        # ----- settings: save / footer -----
        "settings.saved": "Settings saved.",
        "settings.save_failed": "Failed to save settings: {error}",
        "settings.path_missing": "Path does not exist: {path}",
        # ----- spoken language options -----
        "lang.auto": "Auto-detect",
        "lang.ar": "Arabic",
        "lang.zh": "Chinese",
        "lang.cs": "Czech",
        "lang.da": "Danish",
        "lang.nl": "Dutch",
        "lang.en": "English",
        "lang.fi": "Finnish",
        "lang.fr": "French",
        "lang.de": "German",
        "lang.el": "Greek",
        "lang.he": "Hebrew",
        "lang.hi": "Hindi",
        "lang.hu": "Hungarian",
        "lang.id": "Indonesian",
        "lang.it": "Italian",
        "lang.ja": "Japanese",
        "lang.ko": "Korean",
        "lang.no": "Norwegian",
        "lang.pl": "Polish",
        "lang.pt": "Portuguese",
        "lang.ro": "Romanian",
        "lang.ru": "Russian",
        "lang.es": "Spanish",
        "lang.sv": "Swedish",
        "lang.tr": "Turkish",
        "lang.uk": "Ukrainian",
        "lang.vi": "Vietnamese",
    },
    "fr": {
        # ----- relative dates -----
        "date.today": "Aujourd'hui à {time}",
        "date.yesterday": "Hier à {time}",
        "date.weekday": "{wd} à {time}",
        "date.same_year": "{day} {mon} à {time}",
        "date.other_year": "{day} {mon} {year} à {time}",
        # ----- humanized error labels -----
        "err.cuda_oom": "Mémoire de la carte graphique saturée",
        "err.oom": "Mémoire insuffisante",
        "err.file_not_found": "Fichier introuvable",
        "err.permission": "Permission refusée",
        "err.connection": "Impossible de joindre le serveur",
        "err.timeout": "Délai d'attente dépassé",
        "err.unauthorized": "Clé d'API invalide",
        "err.forbidden": "Accès refusé par le serveur",
        "err.rate_limit": "Limite de débit atteinte — réessayez plus tard",
        "err.server": "Erreur du serveur distant",
        "err.ffmpeg": "Échec de la conversion audio",
        "err.diarize": "Échec de la diarisation des locuteurs",
        "err.gpu": "Erreur GPU",
        # ----- job status -----
        "status.pending.label": "En attente",
        "status.running.label": "En cours",
        "status.done.label": "Terminé",
        "status.failed.label": "Échec",
        "status.pending.tip": "En file d'attente — le service en arrière-plan va la prendre en charge.",
        "status.running.tip": "Transcription en cours.",
        "status.done.tip": "Transcription terminée. Cliquez sur la ligne pour ouvrir le dossier.",
        "status.failed.tip": "La transcription a échoué. Cliquez sur la ligne pour voir les détails.",
        # ----- notifications -----
        "notify.loopback_unavailable": "Capture audio du système indisponible : {error}",
        "notify.recording_started": "Enregistrement démarré : {rec_id}",
        "notify.recording_stopped": "Arrêté après {elapsed}. Ajouté à la file (tâche n°{job_id}).",
        # ----- common actions -----
        "action.close": "Fermer",
        "action.retry": "Réessayer",
        "action.cancel": "Annuler",
        "action.save": "Enregistrer",
        "action.continue": "Continuer",
        "action.back": "Retour",
        "action.skip_for_now": "Ignorer pour l'instant",
        "action.save_and_finish": "Enregistrer et terminer",
        "action.test_token": "Tester le jeton",
        "action.test_connection": "Tester la connexion",
        "action.use_groq": "Utiliser Groq",
        "action.later": "Plus tard",
        "action.reload_now": "Recharger maintenant",
        "common.testing": "Test en cours…",
        # ----- job error dialog -----
        "dialog.job_title": "Tâche n°{job_id} — {rec_id}",
        "dialog.job_status": "Statut : {status}",
        "dialog.error": "Erreur",
        "dialog.no_error": "(aucun message d'erreur)",
        # ----- header -----
        "header.worker": "Service en arrière-plan",
        "header.worker_alive": "Le service en arrière-plan est actif — les nouveaux enregistrements seront transcrits automatiquement.",
        "header.worker_dead": "Le service en arrière-plan n'est pas actif. Redémarrez l'application pour le relancer.",
        "header.settings": "Paramètres",
        # ----- setup banner -----
        "banner.title": "Terminer la configuration",
        "banner.hf_missing": "Les étiquettes de locuteurs sont désactivées. Ajoutez un jeton HuggingFace gratuit pour distinguer les locuteurs sur la piste système.",
        "banner.remote_missing": "L'API distante n'est pas entièrement configurée — il manque : {missing}.",
        "banner.missing.endpoint": "l'URL du point de terminaison",
        "banner.missing.model": "le modèle",
        "banner.missing.api_key": "la clé d'API",
        "banner.open_settings": "Ouvrir les paramètres",
        # ----- recording card -----
        "card.recording": "Enregistrement",
        "rec.start": "● Démarrer",
        "rec.stop": "■ Arrêter",
        "rec.ready": "Prêt",
        "rec.ready_tip": "Appuyez sur Démarrer pour lancer un nouvel enregistrement.",
        # ----- jobs card -----
        "card.jobs": "File des tâches",
        "card.jobs_tip": "Travaux de transcription en cours, en file d'attente ou récemment terminés.",
        "table.recording": "Enregistrement",
        "table.status": "Statut",
        "table.created": "Créé le",
        "table.error": "Erreur",
        # ----- recordings card -----
        "card.recordings": "Enregistrements (récents)",
        "card.recordings_tip": "Fichiers audio capturés sur cette machine. Cliquez sur « Afficher dans le dossier » pour ouvrir leur emplacement.",
        "recs.empty": "Aucun enregistrement — appuyez sur Démarrer ci-dessus pour en créer un.",
        "recs.transcribed_tip": "Transcrit — le dossier contient le texte et les sous-titres.",
        "recs.pending_tip": "Audio capturé, transcription en attente.",
        "recs.show_in_folder": "Afficher dans le dossier",
        "recs.show_in_folder_tip": "Ouvrir le dossier contenant les fichiers audio et les transcriptions.",
        # ----- wizard -----
        "wizard.complete_notify": "Configuration terminée — chargement de l'application…",
        "wizard.welcome": "Bienvenue",
        "wizard.backend_lead": "Étape 1 sur 2 — choisissez comment la transcription doit s'exécuter sur cette machine. Vous pourrez changer cela plus tard dans les Paramètres.",
        "wizard.detected_hw": "Matériel détecté",
        "wizard.hw_platform": "• Plateforme : {value}",
        "wizard.hw_cpu": "• CPU        : {cores} cœurs",
        "wizard.hw_gpu": "• GPU        : {value}",
        "wizard.hw_gpu_none": "• GPU        : aucun détecté",
        "wizard.hw_python": "• Python     : {value}",
        "wizard.backend_mode": "Mode de traitement",
        "wizard.no_cuda_notify": "Aucun GPU CUDA détecté — choisissez plutôt Local CPU.",
        "wizard.hf_hero": "Étiquettes de locuteurs (optionnel)",
        "wizard.hf_lead": "Étape 2 sur 2 — pour distinguer les locuteurs sur la piste système, l'application utilise pyannote, qui nécessite un jeton HuggingFace gratuit. Vous pouvez ignorer cette étape : la transcription fonctionnera quand même, mais tout le monde sur la piste système sera étiqueté comme un seul locuteur.",
        "wizard.hf_get_token": "Obtenez votre jeton gratuit (2 minutes)",
        "wizard.hf_step1_pre": "Créez un compte gratuit et un jeton ",
        "wizard.hf_step1_read": "lecture",
        "wizard.hf_step1_post": " sur ",
        "wizard.hf_step2_pre": "Acceptez la licence du modèle (un clic) sur ",
        "wizard.hf_step2_link": "la page du modèle pyannote",
        "wizard.hf_step2_post": ".",
        "wizard.hf_step3": "Collez le jeton ci-dessous.",
        "wizard.hf_paste_first": "Collez d'abord un jeton, ou utilisez « Ignorer pour l'instant ».",
        "wizard.token_save_failed": "Impossible d'enregistrer le jeton : {error}",
        "wizard.remote_hero": "Connectez votre API de transcription",
        "wizard.remote_lead": "Étape 2 sur 2 — Groq est le fournisseur recommandé pour des résultats rapides et précis. Configurez-le ci-dessous en trois étapes, ou indiquez n'importe quel point de terminaison compatible OpenAI. Vous pourrez le modifier plus tard dans Paramètres → API distante.",
        "wizard.groq_get_key": "Obtenez votre clé d'API Groq gratuite (2 minutes)",
        "wizard.groq_step1_pre": "Créez un compte gratuit et une clé d'API sur ",
        "wizard.groq_step2": "Cliquez sur « Utiliser Groq » ci-dessous pour renseigner le point de terminaison et le modèle.",
        "wizard.groq_step3": "Collez votre clé dans le formulaire ci-dessous, puis enregistrez.",
        "wizard.groq_applied": "Point de terminaison et modèle Groq renseignés — il ne reste qu'à coller votre clé d'API ci-dessous.",
        "wizard.endpoint": "Point de terminaison",
        "wizard.model_hint": "p. ex. « whisper-large-v3 » (Groq) ou « whisper-1 » (OpenAI).",
        "wizard.remote_fill": "Renseignez l'URL du point de terminaison, le modèle et la clé d'API.",
        "wizard.settings_save_failed": "Impossible d'enregistrer les paramètres : {error}",
        # ----- backend modes -----
        "backend.local_gpu.title": "Local — GPU NVIDIA",
        "backend.local_gpu.sub_ok": "WhisperX s'exécute sur votre GPU. Toutes les données restent sur cette machine.",
        "backend.local_gpu.sub_none": "Aucun GPU CUDA détecté. Choisissez plutôt Local CPU.",
        "backend.local_gpu.sub_none_settings": "Aucun GPU CUDA détecté. Choisissez plutôt Local CPU ou API distante.",
        "backend.local_cpu.title": "Local — CPU uniquement",
        "backend.local_cpu.sub": "Lent (~30-60 min par heure d'audio), mais 100 % local et fonctionne sur n'importe quelle machine.",
        "backend.remote.title": "API distante",
        "backend.remote.sub_wizard": "N'importe quel point de terminaison /audio/transcriptions compatible OpenAI (OpenAI, Groq, whisper.cpp auto-hébergé). Pas de diarisation — les deux pistes sont transcrites comme un seul locuteur.",
        "backend.remote.sub_settings": "Envoie l'audio vers un point de terminaison compatible OpenAI. Configurez l'URL, le modèle et le jeton d'API dans les onglets API distante et Jetons.",
        "tag.recommended": "recommandé",
        "tag.disabled": "désactivé",
        # ----- settings: inputs / shared -----
        "input.token": "Jeton",
        "input.hf_token": "Jeton HuggingFace",
        "input.base_url": "URL de base",
        "input.quick_presets": "Préréglages rapides",
        "input.preset": "Préréglage",
        "input.model": "Modèle",
        "input.api_key": "Clé d'API",
        # ----- settings: token dialog -----
        "settings.set_token_title": "Définir {label}",
        "settings.set_token_sub": "Stocké dans le trousseau du système d'exploitation sous le service « {service} ». Jamais écrit sur le disque en clair.",
        "settings.token_empty": "Jeton vide — non enregistré.",
        "settings.token_saved": "Jeton pour « {service} » enregistré dans le trousseau.",
        "settings.token_store_failed": "Échec de l'enregistrement du jeton : {error}",
        # ----- settings: reload prompt -----
        "settings.reload_required": "Rechargement requis",
        "settings.reload_message": "Certaines modifications (mode de traitement, chemins, point de terminaison de l'API distante) ne prennent effet qu'après un rechargement.",
        "settings.wizard_reload_message": "L'assistant de première configuration apparaîtra après le rechargement.",
        # ----- settings: dialog chrome -----
        "settings.title": "Paramètres",
        "settings.tab.general": "Général",
        "settings.tab.recording": "Enregistrement",
        "settings.tab.local": "Traitement local",
        "settings.tab.remote": "API distante",
        "settings.tab.tokens": "Jetons",
        "settings.tab.about": "À propos",
        # ----- settings: general tab -----
        "settings.backend_mode": "Mode de traitement",
        "settings.backend_mode_help": "Comment la transcription s'exécute sur cette machine. Le GPU local est le plus rapide si vous avez une carte NVIDIA compatible CUDA. L'API distante délègue le travail à un point de terminaison compatible OpenAI.",
        "settings.whisper_model": "Modèle Whisper",
        "settings.whisper_model_help": "Les modèles plus grands sont plus précis mais plus lents et demandent plus de mémoire. « small » est un bon choix par défaut pour la plupart des CPU/GPU.",
        "settings.language": "Langue",
        "settings.language_help": "Choisissez la langue parlée à transcrire. « Détection automatique » laisse le modèle décider pour chaque piste.",
        "settings.ui_language": "Langue d'affichage",
        "settings.ui_language_help": "Langue des boutons et libellés de l'application. La modifier recharge l'application.",
        # ----- settings: recording tab -----
        "settings.sample_rate": "Fréquence d'échantillonnage",
        "settings.sample_rate_help": "Whisper et pyannote rééchantillonnent tous deux à 16 kHz en interne. Des fréquences plus élevées ne comptent que si vous voulez aussi des archives de qualité d'écoute.",
        "settings.file_format": "Format de fichier",
        "settings.file_format_help": "Opus est compressé et environ 10× plus léger que le FLAC, idéal pour la parole — le choix recommandé. Le FLAC est sans perte (plus volumineux) ; n'utilisez le WAV que pour de l'audio brut non compressé.",
        "settings.recordings_folder": "Dossier des enregistrements",
        "settings.recordings_folder_help": "Où sont stockés l'audio et les transcriptions. Laissez vide pour utiliser ./recordings/ dans le répertoire de travail courant.",
        "settings.open_current_folder": "Ouvrir le dossier actuel",
        "settings.use_default": "Valeur par défaut",
        # ----- settings: local backend tab -----
        "settings.cuda_compute": "Type de calcul CUDA",
        "settings.cuda_compute_help": "Précision utilisée lors de l'exécution de WhisperX sur un GPU NVIDIA. « float16 » est le plus rapide, « int8_float16 » utilise moins de VRAM.",
        "settings.cpu_compute": "Type de calcul CPU",
        "settings.cpu_compute_help": "Précision utilisée lors de l'exécution de WhisperX sur CPU. « int8 » est le seul choix pratique sur la plupart des machines.",
        "settings.detected_hw": "Matériel détecté",
        "hw.platform": "Plateforme",
        "hw.cpu_cores": "Cœurs CPU",
        "hw.gpu": "GPU",
        "hw.gpu_apple": "Apple Silicon (MPS)",
        "hw.gpu_none": "aucun détecté",
        "hw.cuda": "CUDA",
        "hw.cuda_unknown": "inconnue",
        "hw.driver": "Pilote",
        "hw.python": "Python",
        # ----- settings: remote tab -----
        "settings.endpoint": "Point de terminaison",
        "settings.endpoint_help": "N'importe quel point de terminaison /audio/transcriptions compatible OpenAI. Choisissez un préréglage pour préremplir l'URL de base ou saisissez-en une personnalisée.",
        "settings.remote_no_key": "Aucune clé d'API stockée sous « {slot} » — ajoutez-la d'abord dans l'onglet Jetons.",
        "settings.remote_model": "Modèle",
        "settings.remote_model_help": "Nom du modèle côté serveur, p. ex. « whisper-1 » ou « whisper-large-v3 ».",
        "settings.timeout": "Délai d'attente",
        "settings.timeout_help": "Temps d'attente maximal pour une requête de transcription (secondes).",
        "settings.upload_compression": "Compression à l'envoi",
        "settings.upload_compression_help": "Les fichiers plus volumineux que ce seuil sont réencodés en Opus mono avant l'envoi pour rester sous les limites de taille des fournisseurs (OpenAI / Groq plafonnent à 25 Mo).",
        "settings.threshold_mb": "Seuil (Mo)",
        "settings.codec": "Codec",
        "settings.bitrate": "Débit",
        "settings.keyring_slot": "Emplacement du trousseau",
        "settings.keyring_slot_help": "Nom de service sous lequel la clé d'API est stockée. Par défaut « remote_api » ; choisissez un nom personnalisé pour conserver plusieurs fournisseurs côte à côte.",
        # ----- settings: tokens tab -----
        "settings.tokens_help": "Les jetons sont stockés dans le trousseau du système (Gestionnaire d'identifiants Windows / Trousseau macOS / libsecret sous Linux) — jamais sur le disque en clair.",
        "settings.token_set": "défini",
        "settings.token_not_set": "non défini",
        "settings.token_custom_desc": "Emplacement personnalisé de clé d'API distante « {slot} »",
        "action.test": "Tester",
        "action.set": "Définir…",
        "action.replace": "Remplacer…",
        "action.remove": "Supprimer",
        "settings.set_base_url_first": "Définissez d'abord l'URL de base de l'API distante (onglet API distante).",
        "settings.token_removed": "Jeton supprimé pour « {service} ».",
        # ----- settings: about tab -----
        "settings.paths": "Chemins",
        "settings.path.config": "Fichier de configuration",
        "settings.path.jobs_db": "Base des tâches",
        "settings.path.logs": "Journaux",
        "settings.path.recordings": "Enregistrements",
        "settings.open_config_folder": "Ouvrir le dossier de configuration",
        "settings.open_logs_folder": "Ouvrir le dossier des journaux",
        "settings.first_run_wizard": "Assistant de première configuration",
        "settings.first_run_wizard_help": "Réinitialise le mode de traitement pour que l'assistant de bienvenue réapparaisse au prochain lancement. Utile si vous avez changé de matériel.",
        "settings.rerun_wizard": "Relancer l'assistant…",
        "settings.wizard_next_launch": "L'assistant s'exécutera au prochain lancement.",
        # ----- settings: save / footer -----
        "settings.saved": "Paramètres enregistrés.",
        "settings.save_failed": "Échec de l'enregistrement des paramètres : {error}",
        "settings.path_missing": "Le chemin n'existe pas : {path}",
        # ----- spoken language options -----
        "lang.auto": "Détection automatique",
        "lang.ar": "Arabe",
        "lang.zh": "Chinois",
        "lang.cs": "Tchèque",
        "lang.da": "Danois",
        "lang.nl": "Néerlandais",
        "lang.en": "Anglais",
        "lang.fi": "Finnois",
        "lang.fr": "Français",
        "lang.de": "Allemand",
        "lang.el": "Grec",
        "lang.he": "Hébreu",
        "lang.hi": "Hindi",
        "lang.hu": "Hongrois",
        "lang.id": "Indonésien",
        "lang.it": "Italien",
        "lang.ja": "Japonais",
        "lang.ko": "Coréen",
        "lang.no": "Norvégien",
        "lang.pl": "Polonais",
        "lang.pt": "Portugais",
        "lang.ro": "Roumain",
        "lang.ru": "Russe",
        "lang.es": "Espagnol",
        "lang.sv": "Suédois",
        "lang.tr": "Turc",
        "lang.uk": "Ukrainien",
        "lang.vi": "Vietnamien",
    },
}


def set_language(code: str | None) -> None:
    """Set the active UI language. Unknown codes fall back to English."""
    global _current
    _current = code if code in _CATALOG else _DEFAULT


def get_language() -> str:
    return _current


def resolve_and_set_language(config_value: str | None) -> str:
    """Pick the UI language from config, detecting the OS locale when unset.

    A blank/``"auto"``/``None`` config value detects the system locale (so a
    French Windows shows French out of the box); anything else is used verbatim
    if supported, else English. Returns the language that was set.
    """
    if config_value and config_value != "auto":
        set_language(config_value)
        return get_language()
    set_language(_detect_system_language())
    return get_language()


def _detect_system_language() -> str:
    # POSIX (Linux/macOS, CI) expose the preferred language via env vars; these
    # are normally absent on stock Windows.
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = _os.environ.get(var)
        if val:
            code = val.replace("-", "_").split("_")[0].split(".")[0].lower()
            if code in _CATALOG:
                return code
    # Fall back to the configured locale. Windows returns a name like
    # "French_France"; its first two letters happen to match the ISO code for
    # the languages we ship ("fr"/"en"), so a 2-char prefix check covers both
    # that and POSIX "fr_FR" forms.
    try:
        name = (_locale.getlocale()[0] or "").lower()
    except (ValueError, TypeError):
        name = ""
    prefix = name[:2]
    return prefix if prefix in _CATALOG else _DEFAULT


def t(key: str, /, **kwargs: object) -> str:
    """Translate ``key`` for the active language with ``str.format`` kwargs.

    Falls back to the English string, then to the key itself, so a missing
    translation degrades gracefully instead of raising.
    """
    msg = _CATALOG.get(_current, {}).get(key) or _CATALOG[_DEFAULT].get(key) or key
    if kwargs:
        try:
            return msg.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return msg
    return msg


def weekday_abbr(index: int) -> str:
    """Abbreviated weekday name (Mon=0) for the active language."""
    table = _WEEKDAYS.get(_current) or _WEEKDAYS[_DEFAULT]
    return table[index % 7]


def month_abbr(month: int) -> str:
    """Abbreviated month name (Jan=1) for the active language."""
    table = _MONTHS.get(_current) or _MONTHS[_DEFAULT]
    return table[(month - 1) % 12]
