"""Subida a YouTube Studio con Playwright (Chromium) usando un perfil persistente por canal.

AVISO: esta automatización depende de la interfaz web de YouTube Studio. Si YouTube cambia su
interfaz, los selectores pueden dejar de funcionar y habrá que actualizarlos. No se usa la YouTube
Data API, no se guardan contraseñas y no se intenta ocultar la automatización: el inicio de sesión
se hace a mano, una vez, en el perfil del canal.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

STUDIO_URL = "https://studio.youtube.com"
AUTOMATION_NOTICE = (
    "La subida automática depende de la interfaz de YouTube Studio y puede romperse cuando YouTube la cambie. "
    "Revisa la carpeta de diagnóstico si una subida falla."
)

# Textos visibles por idioma de Studio (los selectores por atributo name/id se prueban primero).
TEXTS = {
    "es": {
        "create": r"Crear", "upload": r"Subir vídeos", "show_more": r"Mostrar más|Mostrar todo",
        "next": r"Siguiente", "save": r"Guardar|Programar|Publicar", "done": r"Listo|Hecho|Cerrar",
        "title": r"título", "description": r"descripción|Cuéntales", "tags": r"Etiquetas",
        "processing_done": r"Comprobaciones completadas|Procesamiento finalizado|No se han encontrado problemas|"
                           r"Vídeo procesado|Se ha completado el procesamiento|Subida completada",
        "uploading": r"Subiendo|Uploading", "schedule": r"Programar",
        "months": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"],
        "date_fmt": "{d} {mon} {y}", "time_fmt": "%H:%M",
        "subtitles": r"Subtítulos", "upload_file": r"Subir archivo", "with_timing": r"Con sincronización",
        "continue": r"Continuar", "playlist_done": r"Listo|Hecho",
    },
    "en": {
        "create": r"Create", "upload": r"Upload videos", "show_more": r"Show more|Show all",
        "next": r"Next", "save": r"Save|Schedule|Publish", "done": r"Done|Close",
        "title": r"title", "description": r"description|Tell viewers", "tags": r"Tags",
        "processing_done": r"Checks complete|Processing complete|No issues found|Upload complete|Processing finished",
        "uploading": r"Uploading", "schedule": r"Schedule",
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "date_fmt": "{mon} {d}, {y}", "time_fmt": "%I:%M %p",
        "subtitles": r"Subtitles", "upload_file": r"Upload file", "with_timing": r"With timing",
        "continue": r"Continue", "playlist_done": r"Done",
    },
}


class SessionExpired(Exception):
    """La sesión de Studio ha caducado: hay que iniciar sesión a mano."""


class UploadError(Exception):
    def __init__(self, message: str, retryable: bool = True, diagnostic: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.diagnostic = diagnostic


@dataclass
class UploadRequest:
    video_path: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    publish_at: datetime | None = None
    visibility: str = "PUBLIC"  # PUBLIC | UNLISTED | PRIVATE
    made_for_kids: bool = False
    thumbnail: str = ""
    playlist: str = ""
    subtitles: str = ""
    existing_video_id: str = ""  # para retomar una subida interrumpida


@dataclass
class UploadResult:
    video_id: str
    url: str
    warnings: list[str] = field(default_factory=list)


def extract_video_id(href: str) -> str:
    m = re.search(r"(?:youtu\.be/|/shorts/|v=|/video/)([\w-]{11})", href or "")
    return m.group(1) if m else ""


def format_date(dt: datetime, lang: str) -> tuple[str, str]:
    t = TEXTS.get(lang, TEXTS["en"])
    date = t["date_fmt"].format(d=dt.day, mon=t["months"][dt.month - 1], y=dt.year)
    tm = dt.strftime(t["time_fmt"]).lstrip("0") if lang == "en" else dt.strftime(t["time_fmt"])
    return date, tm


class StudioUploader:
    def __init__(self, profile_dir: str, studio_lang: str = "es", diag_dir: str | None = None,
                 headless: bool = False, processing_timeout_s: int = 1800, step_timeout_s: int = 60):
        self.profile_dir = profile_dir
        self.lang = studio_lang if studio_lang in TEXTS else "en"
        self.t = TEXTS[self.lang]
        self.diag_dir = Path(diag_dir) if diag_dir else Path(profile_dir).parent / "diagnostico"
        self.headless = headless
        self.processing_timeout = processing_timeout_s * 1000
        self.step_timeout = step_timeout_s * 1000

    # --- navegador -------------------------------------------------------------
    def _launch(self, pw):
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        locale = "es-ES" if self.lang == "es" else "en-US"
        ctx = pw.chromium.launch_persistent_context(
            self.profile_dir, headless=self.headless, locale=locale, viewport={"width": 1366, "height": 900},
            args=[f"--lang={locale}"],
        )
        ctx.set_default_timeout(self.step_timeout)
        return ctx

    def open_login(self, wait_closed: bool = True) -> None:
        """Abre Studio en el perfil del canal para que el usuario inicie sesión a mano."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._launch(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(STUDIO_URL)
            if wait_closed:
                page.wait_for_event("close", timeout=0)
            ctx.close()

    def check_session(self) -> bool:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._launch(pw)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(STUDIO_URL, wait_until="domcontentloaded")
                return not self._login_required(page)
            finally:
                ctx.close()

    @staticmethod
    def _login_required(page) -> bool:
        try:
            page.wait_for_url(re.compile(r"(studio\.youtube\.com/channel|accounts\.google\.com)"), timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        return "accounts.google.com" in page.url or "ServiceLogin" in page.url

    # --- utilidades de selectores --------------------------------------------
    def _first_visible(self, page, candidates: list, timeout: int | None = None):
        """Devuelve el primer localizador visible de la lista, esperando explícitamente."""
        timeout = timeout or self.step_timeout
        combined = None
        for c in candidates:
            loc = page.locator(c) if isinstance(c, str) else c
            combined = loc if combined is None else combined.or_(loc)
        combined.first.wait_for(state="visible", timeout=timeout)
        for c in candidates:
            loc = (page.locator(c) if isinstance(c, str) else c).first
            if loc.is_visible():
                return loc
        return combined.first

    def _diagnose(self, page, name: str) -> str:
        self.diag_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.diag_dir / f"{stamp}_{re.sub(r'[^\w-]+', '_', name)[:40]}"
        try:
            page.screenshot(path=str(base) + ".png", full_page=True)
            Path(str(base) + ".html").write_text(page.content(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo guardar el diagnóstico: %s", exc)
        return str(base)

    # --- flujo de subida ----------------------------------------------------
    def upload(self, req: UploadRequest, on_stage: Callable[[str, dict], None] | None = None) -> UploadResult:
        from playwright.sync_api import sync_playwright

        stage = on_stage or (lambda s, d: None)
        with sync_playwright() as pw:
            ctx = self._launch(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(STUDIO_URL, wait_until="domcontentloaded")
                if self._login_required(page):
                    raise SessionExpired("La sesión de YouTube Studio ha caducado. Inicia sesión a mano.")
                warnings: list[str] = []
                video_id = req.existing_video_id
                if video_id:
                    # Retomar: Studio permite editar un vídeo ya subido desde su página de detalles
                    stage("retomando", {"video_id": video_id})
                    page.goto(f"{STUDIO_URL}/video/{video_id}/edit", wait_until="domcontentloaded")
                    self._fill_details(page, req, warnings)
                    self._save_edit_page(page, req)
                else:
                    video_id = self._start_upload(page, req, stage)
                    self._fill_details(page, req, warnings)
                    stage("detalles", {"video_id": video_id})
                    self._go_to_visibility(page, req, warnings)
                    self._set_visibility(page, req)
                    stage("visibilidad", {"video_id": video_id})
                    self._wait_processing(page)
                    self._click_text_button(page, "#done-button", self.t["save"])
                    self._close_dialog(page)
                if not video_id:
                    raise UploadError("no se obtuvo el ID del vídeo", retryable=True,
                                      diagnostic=self._diagnose(page, "sin_id"))
                url = f"https://youtube.com/shorts/{video_id}"
                stage("hecho", {"video_id": video_id, "url": url})
                return UploadResult(video_id, url, warnings)
            except SessionExpired:
                self._diagnose(page, "sesion_caducada")
                raise
            except UploadError:
                raise
            except Exception as exc:  # noqa: BLE001
                diag = self._diagnose(page, "error_subida")
                raise UploadError(f"{type(exc).__name__}: {exc}", retryable=True, diagnostic=diag) from exc
            finally:
                ctx.close()

    def _start_upload(self, page, req: UploadRequest, stage) -> str:
        page.goto(f"{STUDIO_URL}/channel/UC/videos/upload?d=ud", wait_until="domcontentloaded")
        file_input = page.locator("input[type='file']").first
        try:
            file_input.wait_for(state="attached", timeout=15000)
        except Exception:  # noqa: BLE001 — la URL directa puede no abrir el diálogo: usar el botón Crear
            self._first_visible(page, ["#create-icon", page.get_by_role("button", name=re.compile(self.t["create"]))]).click()
            self._first_visible(page, ["#text-item-0", page.get_by_role("menuitem", name=re.compile(self.t["upload"]))]).click()
            file_input = page.locator("input[type='file']").first
            file_input.wait_for(state="attached")
        file_input.set_input_files(req.video_path)
        stage("archivo", {})
        # El enlace del vídeo aparece en cuanto Studio asigna un ID
        link = page.locator("a.ytcp-video-info, .video-url-fadeable a, ytcp-video-info a").first
        link.wait_for(state="visible", timeout=self.processing_timeout)
        video_id = extract_video_id(link.get_attribute("href") or link.inner_text())
        stage("archivo", {"video_id": video_id})
        return video_id

    def _fill_textbox(self, page, css: str, aria: str, text: str) -> None:
        box = self._first_visible(page, [css, page.get_by_role("textbox", name=re.compile(aria, re.I))])
        box.click()
        box.press("Control+A")
        box.press("Delete")
        if text:
            box.fill(text)

    def _fill_details(self, page, req: UploadRequest, warnings: list[str]) -> None:
        self._fill_textbox(page, "#title-textarea #textbox", self.t["title"], req.title)
        self._fill_textbox(page, "#description-textarea #textbox", self.t["description"], req.description)
        if req.thumbnail and Path(req.thumbnail).is_file():
            try:
                page.locator("input#file-loader, ytcp-thumbnails-compact-editor-uploader input[type='file']") \
                    .first.set_input_files(req.thumbnail)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"miniatura no subida: {exc}")
        if req.playlist:
            try:
                self._select_playlist(page, req.playlist)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"lista de reproducción no asignada: {exc}")
        kids = "VIDEO_MADE_FOR_KIDS_MFK" if req.made_for_kids else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
        radio = page.locator(f"tp-yt-paper-radio-button[name='{kids}'], [name='{kids}']").first
        radio.scroll_into_view_if_needed()
        radio.click()
        if req.tags:
            try:
                self._first_visible(page, ["#toggle-button", page.get_by_role(
                    "button", name=re.compile(self.t["show_more"], re.I))]).click()
                tag_input = self._first_visible(page, ["#tags-container input", "input[aria-label*='Tags']",
                                                      f"input[aria-label*='{self.t['tags']}']"])
                tag_input.fill(",".join(req.tags) + ",")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"etiquetas no añadidas: {exc}")

    def _select_playlist(self, page, name: str) -> None:
        self._first_visible(page, ["ytcp-video-metadata-playlists ytcp-dropdown-trigger",
                                   "ytcp-video-metadata-playlists #basics"]).click()
        page.get_by_text(name, exact=True).first.click()
        self._click_text_button(page, "ytcp-playlist-dialog #done-button, .done-button", self.t["playlist_done"])

    def _go_to_visibility(self, page, req: UploadRequest | None = None, warnings: list[str] | None = None) -> None:
        """Detalles -> Elementos del vídeo (subtítulos) -> Comprobaciones -> Visibilidad."""
        visibility = page.locator("tp-yt-paper-radio-button[name='PUBLIC'], #privacy-radios").first
        for step in range(3):
            self._click_text_button(page, "#next-button", self.t["next"])
            if step == 0 and req is not None and req.subtitles and Path(req.subtitles).is_file():
                try:
                    self.upload_subtitles(page, req.subtitles)
                except Exception as exc:  # noqa: BLE001
                    if warnings is not None:
                        warnings.append(f"subtítulos no subidos: {exc}")
            if visibility.is_visible():
                return
        visibility.wait_for(state="visible")

    def upload_subtitles(self, page, path: str) -> None:
        """Sube un SRT en el paso «Elementos del vídeo» si Studio lo permite (mejor esfuerzo)."""
        page.locator("#subtitles-button, ytcp-subtitles-button").first.click()
        page.get_by_role("button", name=re.compile(self.t["upload_file"], re.I)).first.click()
        page.get_by_text(re.compile(self.t["with_timing"], re.I)).first.click()
        page.get_by_role("button", name=re.compile(self.t["continue"], re.I)).first.click()
        page.locator("input[type='file']").last.set_input_files(path)
        self._click_text_button(page, "#done-button", self.t["done"])

    def _set_visibility(self, page, req: UploadRequest) -> None:
        if req.publish_at and req.publish_at > datetime.now() and req.visibility == "PUBLIC":
            page.locator("#second-container-expand-button, #schedule-radio-button").first.click()
            date, tm = format_date(req.publish_at, self.lang)
            page.locator("#datepicker-trigger, ytcp-text-dropdown-trigger#datepicker-trigger").first.click()
            date_input = self._first_visible(page, ["ytcp-date-picker tp-yt-paper-input input",
                                                    "ytcp-date-picker input"])
            date_input.fill(date)
            date_input.press("Enter")
            time_input = self._first_visible(page, ["#time-of-day-container input",
                                                    "ytcp-form-input-container#time-of-day-container input"])
            time_input.fill(tm)
            time_input.press("Enter")
        else:
            page.locator(f"tp-yt-paper-radio-button[name='{req.visibility}']").first.click()

    def _wait_processing(self, page) -> None:
        """Espera explícitamente a que termine la subida y el procesamiento (sin tiempos fijos)."""
        pattern = self.t["processing_done"]
        page.wait_for_function(
            """(pat) => {
                const re = new RegExp(pat, 'i');
                const els = document.querySelectorAll('ytcp-video-upload-progress, .progress-label, #dialog-title');
                return Array.from(els).some(e => re.test(e.textContent || ''));
            }""",
            arg=pattern, timeout=self.processing_timeout,
        )

    def _click_text_button(self, page, css: str, text_re: str) -> None:
        btn = self._first_visible(page, [css, page.get_by_role("button", name=re.compile(text_re, re.I))])
        btn.wait_for(state="visible")
        btn.click()

    def _close_dialog(self, page) -> None:
        try:
            self._click_text_button(page, "ytcp-video-share-dialog #close-button, #close-button", self.t["done"])
        except Exception:  # noqa: BLE001
            pass

    def _save_edit_page(self, page, req: UploadRequest) -> None:
        self._click_text_button(page, "#save, ytcp-button#save", self.t["save"])
        page.locator("#save[disabled], ytcp-button#save[disabled]").first.wait_for(state="attached")
