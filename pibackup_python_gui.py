#!/usr/bin/env python3
"""
pibackup_python – Browser-GUI als normaler User.

Projekt:     pibackup_python
Modul:       pibackup_python_gui.py
Version:     1.0.1
Stand:       2026-11-02
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.11)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Beschreibung
------------
Lokaler HTTP-Dienst (nur 127.0.0.1). Lesen von Geraeten ohne root.
Schreibaktionen starten pibackup_python.py per pkexec/sudo.

Historie
--------
Version 1.0.0 – 2026-08-24 – Web-GUI mit Defaults Backup/Shrink/Pack.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
  python3 pibackup_python_gui.py --help
  python3 pibackup_python_gui.py --version
  python3 pibackup_python_gui.py
  python3 pibackup_python_gui.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import project_meta as meta
except ImportError:
    sys.stderr.write("Fehler: project_meta.py fehlt im gleichen Verzeichnis.\n")
    raise SystemExit(2)

from pibackup_common import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    PibackupError,
    is_linux,
    is_root,
    validate_disk,
)
from pibackup_engine import list_disks_payload
from pibackup_log import Logger
from pibackup_priv import wrap_engine

WEB_DIR = Path(__file__).resolve().parent / "share" / "web"
CANCEL_DIR = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".ico": "image/x-icon",
}

HELP_TEXT = f"""
{meta.PROJEKT} GUI {meta.VERSION} – Browser-Oberflaeche (normaler User).

Stand: {meta.STAND}
{meta.BESCHREIBUNG}

Verwendung:
  python3 pibackup_python_gui.py --help
  python3 pibackup_python_gui.py --version
  python3 pibackup_python_gui.py
  python3 pibackup_python_gui.py --bind {meta.DEFAULT_BIND} --port {meta.DEFAULT_PORT}

Parameter:

  --help / -h
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Diese Hilfe anzeigen und beenden.
      Beispiel:     python3 pibackup_python_gui.py --help

  --version
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Versionsnummer, Datum und Programmbeschreibung.
      Beispiel:     python3 pibackup_python_gui.py --version

  --bind
      Typ:          IP
      Standardwert: {meta.DEFAULT_BIND}
      Beschreibung: Nur Loopback. Andere Adressen werden abgelehnt.
      Beispiel:     python3 pibackup_python_gui.py --bind 127.0.0.1

  --port
      Typ:          int
      Standardwert: {meta.DEFAULT_PORT}
      Beschreibung: TCP-Port. 0 waehlt einen freien Port (Tests).
      Beispiel:     python3 pibackup_python_gui.py --port 8787

  --log / --log-file
      Typ:          Flag / Pfad
      Standardwert: aus / {meta.DEFAULT_LOG_NAME}
      Beschreibung: UTF-8-Log der GUI.
      Beispiel:     python3 pibackup_python_gui.py --log

  --Ende / -E
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Am Ende auf Taste/Enter warten.
      Beispiel:     python3 pibackup_python_gui.py --version --Ende

Beispiele:
  python3 pibackup_python_gui.py --help
  python3 pibackup_python_gui.py --version
  python3 pibackup_python_gui.py
""".strip()


class JobState:
    """Ein laufender Engine-Prozess."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen[str] | None = None
        self.lines: list[str] = []
        self.percent: float = 0.0
        self.running: bool = False
        self.ok: bool | None = None
        self.message: str = ""
        self.cancel_file: Path | None = None


STATE = JobState()
TOKEN = secrets.token_hex(16)


class GuiParser(argparse.ArgumentParser):
    """Deutsche Fehlertexte."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"Fehler: {message}\n")
        raise SystemExit(EXIT_USAGE)


def version_text() -> str:
    """Versionsblock.

    Parameter:
        keine
    Rueckgabewert:
        Text
    Fehlerfaelle:
        keine
    Beispiel:
        print(version_text())
    """
    return f"{meta.PROJEKT}-gui {meta.VERSION}\nStand: {meta.STAND}\n{meta.BESCHREIBUNG}"


def _json_bytes(data: object, status: int = 200) -> tuple[int, bytes, str]:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return status, payload, "application/json; charset=utf-8"


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0 or length > 1_000_000:
        raise PibackupError("Leerer oder zu grosser JSON-Body.")
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise PibackupError("JSON ungueltig.") from exc
    if not isinstance(data, dict):
        raise PibackupError("JSON-Objekt erwartet.")
    if data.get("token") != TOKEN:
        raise PibackupError("Ungueltiges Sitzungstoken.")
    return data


def _safe_dest_file(path: str) -> str:
    text = path.strip().replace("\\", "/")
    if not text or ".." in Path(text).parts:
        raise PibackupError("Ungueltiger Zielpfad.")
    if not (text.endswith(".img") or text.endswith(".img.zst")):
        raise PibackupError("Ziel muss auf .img oder .img.zst enden.")
    return text


def _engine_args(body: dict[str, Any]) -> list[str]:
    mode = str(body.get("mode") or "backup")
    argv: list[str] = []
    if mode == "backup":
        source = validate_disk(str(body.get("source") or ""))
        dest = _safe_dest_file(str(body.get("dest") or ""))
        argv = ["backup", "--source", source, "--dest", dest, "--yes", "--json"]
        if not body.get("shrink", True):
            argv.append("--no-shrink")
        if not body.get("compress", True):
            argv.append("--no-compress")
        level = int(body.get("compress_level") or meta.DEFAULT_COMPRESS_LEVEL)
        argv.extend(["--compress-level", str(level)])
        exclude = str(body.get("exclude") or "").strip()
        if exclude:
            argv.extend(["--exclude", exclude])
        if body.get("remove_ssh"):
            argv.append("--remove-ssh")
        if body.get("remove_dhcp"):
            argv.append("--remove-dhcp")
        return argv
    if mode == "restore":
        image = _safe_dest_file(str(body.get("image") or ""))
        dest = validate_disk(str(body.get("dest") or ""))
        argv = ["restore", "--image", image, "--dest", dest, "--yes", "--json"]
        if body.get("enable_ssh"):
            argv.append("--enable-ssh")
        return argv
    if mode == "clone":
        source = validate_disk(str(body.get("source") or ""))
        dest = validate_disk(str(body.get("dest") or ""))
        return ["clone", "--source", source, "--dest", dest, "--yes", "--json"]
    raise PibackupError(f"Unbekannter Modus {mode!r}.")


def _reader(proc: subprocess.Popen[str], state: JobState) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip("\n")
        with state.lock:
            state.lines.append(text)
            if len(state.lines) > 400:
                state.lines = state.lines[-400:]
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if payload.get("event") == "progress":
                state.percent = float(payload.get("percent") or 0)
            if payload.get("event") == "result":
                state.ok = bool(payload.get("ok"))
                state.message = str(payload.get("message") or "")
    rc = proc.wait()
    with state.lock:
        state.running = False
        if state.ok is None:
            state.ok = rc == 0
            if rc != 0 and not state.message:
                state.message = f"Engine Exit {rc}"


def start_job(body: dict[str, Any]) -> dict[str, Any]:
    """Startet die Engine mit pkexec/sudo.

    Parameter:
        body: JSON der GUI.
    Rueckgabewert:
        Status-dict
    Fehlerfaelle:
        PibackupError
    Beispiel:
        start_job({"token": TOKEN, "mode": "backup", ...})
    """
    with STATE.lock:
        if STATE.running:
            raise PibackupError("Es laeuft bereits eine Schreibaktion.")
        argv = _engine_args(body)
        cancel = CANCEL_DIR / f"pibackup-cancel-{os.getpid()}.flag"
        cancel.write_text("0", encoding="utf-8")
        argv.extend(["--cancel-file", str(cancel)])
        cmd = wrap_engine(argv)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        STATE.proc = proc
        STATE.lines = []
        STATE.percent = 0.0
        STATE.running = True
        STATE.ok = None
        STATE.message = ""
        STATE.cancel_file = cancel
    thread = threading.Thread(target=_reader, args=(proc, STATE), daemon=True)
    thread.start()
    return {"ok": True, "started": True, "cmd_preview": " ".join(cmd[:3] + argv[:1])}


def job_status() -> dict[str, Any]:
    """Aktueller Job.

    Parameter:
        keine
    Rueckgabewert:
        dict
    Fehlerfaelle:
        keine
    Beispiel:
        job_status()
    """
    with STATE.lock:
        logs: list[str] = []
        for line in STATE.lines:
            try:
                payload = json.loads(line)
                msg = payload.get("message")
                if msg:
                    logs.append(str(msg))
            except json.JSONDecodeError:
                logs.append(line)
        return {
            "running": STATE.running,
            "percent": STATE.percent,
            "ok": STATE.ok,
            "message": STATE.message,
            "log": logs[-80:],
        }


def cancel_job() -> dict[str, Any]:
    """Schreibt die Cancel-Datei (ohne extra root).

    Parameter:
        keine
    Rueckgabewert:
        dict
    Fehlerfaelle:
        keine
    Beispiel:
        cancel_job()
    """
    with STATE.lock:
        if STATE.cancel_file is not None:
            STATE.cancel_file.write_text("1", encoding="utf-8")
        return {"ok": True, "cancelled": True}


def handle_get(path: str) -> tuple[int, bytes, str]:
    """GET-Routen.

    Parameter:
        path: URL-Pfad
    Rueckgabewert:
        status, body, content-type
    Fehlerfaelle:
        PibackupError
    Beispiel:
        handle_get("/api/meta")
    """
    if path in ("/", "/index.html"):
        page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace("{{TOKEN}}", TOKEN)
        page = page.replace("{{VERSION}}", meta.VERSION)
        page = page.replace("{{STAND}}", meta.STAND)
        return 200, page.encode("utf-8"), "text/html; charset=utf-8"
    if path == "/api/meta":
        return _json_bytes(
            {
                "name": meta.PROJEKT,
                "version": meta.VERSION,
                "stand": meta.STAND,
                "linux": is_linux(),
                "gui_root": is_root(),
                "token": TOKEN,
            }
        )
    if path in ("/api/defaults", "/api/devices"):
        try:
            payload = list_disks_payload()
        except PibackupError as exc:
            return _json_bytes({"ok": False, "error": str(exc), "linux": is_linux()}, 200)
        payload["ok"] = True
        payload["linux"] = is_linux()
        payload["token"] = TOKEN
        return _json_bytes(payload)
    if path == "/api/status":
        return _json_bytes(job_status())
    rel = path.lstrip("/")
    candidate = (WEB_DIR / rel).resolve()
    if candidate.is_file() and candidate.is_relative_to(WEB_DIR.resolve()):
        data = candidate.read_bytes()
        ctype = MIME.get(candidate.suffix, "application/octet-stream")
        return 200, data, ctype
    return 404, b'{"error":"not found"}', "application/json; charset=utf-8"


def handle_post(path: str, body: dict[str, Any]) -> tuple[int, bytes, str]:
    """POST-Routen.

    Parameter:
        path: URL
        body: JSON
    Rueckgabewert:
        status, body, type
    Fehlerfaelle:
        PibackupError
    Beispiel:
        handle_post("/api/start", body)
    """
    if path == "/api/start":
        return _json_bytes(start_job(body))
    if path == "/api/cancel":
        return _json_bytes(cancel_job())
    if path == "/api/preview":
        from pibackup_engine import (
            BackupOptions,
            CloneOptions,
            RestoreOptions,
            fill_backup_defaults,
            plan_backup,
            plan_clone,
            plan_restore,
        )

        mode = str(body.get("mode") or "backup")
        if mode == "backup":
            opts = fill_backup_defaults(
                BackupOptions(
                    source=str(body.get("source") or ""),
                    dest=str(body.get("dest") or ""),
                    shrink=bool(body.get("shrink", True)),
                    compress=bool(body.get("compress", True)),
                    dry_run=True,
                )
            )
            plan = plan_backup(opts)
            return _json_bytes({"ok": True, "steps": plan.steps, "warnings": plan.warnings})
        if mode == "restore":
            plan = plan_restore(
                RestoreOptions(
                    image=str(body.get("image") or ""),
                    dest=str(body.get("dest") or ""),
                    dry_run=True,
                )
            )
            return _json_bytes({"ok": True, "steps": plan.steps, "warnings": plan.warnings})
        plan = plan_clone(
            CloneOptions(
                source=str(body.get("source") or ""),
                dest=str(body.get("dest") or ""),
                dry_run=True,
            )
        )
        return _json_bytes({"ok": True, "steps": plan.steps, "warnings": plan.warnings})
    raise PibackupError("Unbekannte POST-Route.")


class Handler(BaseHTTPRequestHandler):
    """HTTP-Handler nur fuer Loopback-GUI."""

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            status, body, ctype = handle_get(parsed.path)
        except PibackupError as exc:
            status, body, ctype = _json_bytes({"ok": False, "error": str(exc)}, 400)
        except OSError as exc:
            status, body, ctype = _json_bytes({"ok": False, "error": str(exc)}, 500)
        self._send(status, body, ctype)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = _read_json(self)
            status, raw, ctype = handle_post(parsed.path, body)
        except PibackupError as exc:
            status, raw, ctype = _json_bytes({"ok": False, "error": str(exc)}, 400)
        self._send(status, raw, ctype)


def make_server(bind: str, port: int) -> ThreadingHTTPServer:
    """HTTP-Server auf Loopback.

    Parameter:
        bind: IP
        port: TCP-Port, 0 = frei
    Rueckgabewert:
        ThreadingHTTPServer
    Fehlerfaelle:
        PibackupError bei nicht-lokalem Bind.
    Beispiel:
        make_server("127.0.0.1", 8787)
    """
    if bind not in ("127.0.0.1", "localhost", "::1"):
        raise PibackupError("Die GUI darf nur an 127.0.0.1 gebunden werden.")
    return ThreadingHTTPServer((bind, port), Handler)


def wait_ende() -> None:
    """Enter abwarten.

    Parameter:
        keine
    Rueckgabewert:
        None
    Fehlerfaelle:
        keine
    Beispiel:
        wait_ende()
    """
    sys.stdout.write('Programmende: "Hit any Key or Enter"\n')
    sys.stdout.flush()
    try:
        sys.stdin.read(1)
    except Exception:
        return


def build_parser() -> GuiParser:
    """CLI der GUI.

    Parameter:
        keine
    Rueckgabewert:
        GuiParser
    Fehlerfaelle:
        keine
    Beispiel:
        build_parser()
    """
    parser = GuiParser(prog="pibackup_python_gui.py", add_help=False)
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--bind", default=meta.DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=meta.DEFAULT_PORT)
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--log-file", dest="log_file", default="")
    parser.add_argument("--Ende", "-E", dest="ende", action="store_true")
    parser.add_argument("--no-banner", action="store_true")
    return parser


def serve_until_interrupt(httpd: ThreadingHTTPServer, logger: Logger | None) -> None:
    """Server-Schleife.

    Parameter:
        httpd: Server
        logger: optional
    Rueckgabewert:
        None
    Fehlerfaelle:
        KeyboardInterrupt
    Beispiel:
        serve_until_interrupt(httpd, None)
    """
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/"
    sys.stdout.write(f"pibackup GUI {meta.VERSION} - {url}\n")
    sys.stdout.write("Als normaler User belassen. Schreibaktionen fragen pkexec/sudo.\n")
    if is_root():
        sys.stdout.write("Hinweis: GUI laeuft als root - besser als Desktop-User starten.\n")
    sys.stdout.flush()
    if logger:
        logger.write(f"GUI lauscht auf {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\nGUI beendet.\n")
    finally:
        httpd.shutdown()


def main(argv: list[str] | None = None) -> int:
    """Einstieg GUI.

    Parameter:
        argv: optional
    Rueckgabewert:
        Exitcode
    Fehlerfaelle:
        SystemExit
    Beispiel:
        raise SystemExit(main(["--version"]))
    """
    args = build_parser().parse_args(argv)
    if args.help:
        sys.stdout.write(HELP_TEXT + "\n")
        if args.ende:
            wait_ende()
        return EXIT_OK
    if args.version:
        sys.stdout.write(version_text() + "\n")
        if args.ende:
            wait_ende()
        return EXIT_OK
    logger = Logger(
        enabled=args.log or bool(args.log_file),
        log_file=Path(args.log_file) if args.log_file else None,
    )
    try:
        httpd = make_server(args.bind, args.port)
    except PibackupError as exc:
        sys.stderr.write(f"Fehler: {exc}\n")
        logger.close()
        return EXIT_ERROR
    if args.no_banner:
        return EXIT_OK
    try:
        serve_until_interrupt(httpd, logger)
    finally:
        logger.close()
        if args.ende:
            wait_ende()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
