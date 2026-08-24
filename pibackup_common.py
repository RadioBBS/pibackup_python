"""
pibackup_python – Gemeinsame Typen, Fehler und Hilfen.

Projekt:     pibackup_python
Modul:       pibackup_common.py
Version:     1.0.0
Stand:       2026-08-24
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.10)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS

Beschreibung
------------
Fehlerklasse, Reporter (Text/JSON), Abbruchflag, Werkzeugsuche
und Pfadpruefung fuer Blockgeraete.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.

Aufruf / Nutzung
----------------
Wird von Engine, CLI und GUI importiert.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import project_meta as meta

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
DEVICE_RE = re.compile(
    r"^/dev/(?:sd[a-z]+|mmcblk[0-9]+|nvme[0-9]+n[0-9]+|vd[a-z]+|xvd[a-z]+)$"
)
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class PibackupError(Exception):
    """Fachfehler der Engine oder GUI."""


class CancelledError(PibackupError):
    """Lauf durch Nutzer oder Cancel-Datei abgebrochen."""


@dataclass
class CmdResult:
    """Ergebnis eines Prozesses ohne Shell.

    Parameter:
        returncode, stdout, stderr: wie subprocess.
    """

    returncode: int
    stdout: str
    stderr: str


@dataclass
class CancelFlag:
    """Abbruch per Event oder durch die GUI beschreibbare Datei."""

    path: Path | None = None
    _event: threading.Event = field(default_factory=threading.Event)

    def request(self) -> None:
        """Abbruch anfordern.

        Parameter:
            keine
        Rueckgabewert:
            None
        Fehlerfaelle:
            OSError falls die Datei nicht geschrieben werden kann.
        Beispiel:
            flag.request()
        """
        self._event.set()
        if self.path is not None:
            self.path.write_text("1", encoding="utf-8")

    def is_set(self) -> bool:
        """True, wenn abgebrochen werden soll.

        Parameter:
            keine
        Rueckgabewert:
            True bei Abbruch.
        Fehlerfaelle:
            keine
        Beispiel:
            if flag.is_set(): raise CancelledError("Abbruch")
        """
        if self._event.is_set():
            return True
        if self.path is None or not self.path.exists():
            return False
        return self.path.read_text(encoding="utf-8").strip() == "1"

    def raise_if_set(self) -> None:
        """Wirft CancelledError bei Abbruch.

        Parameter:
            keine
        Rueckgabewert:
            None
        Fehlerfaelle:
            CancelledError
        Beispiel:
            flag.raise_if_set()
        """
        if self.is_set():
            raise CancelledError("Lauf vom Nutzer abgebrochen.")


@dataclass
class Reporter:
    """Fortschritt und Meldungen als Text oder JSON-Zeilen."""

    json_mode: bool = False
    stream: TextIO = field(default_factory=lambda: sys.stdout)
    logger: Any = None

    def emit(self, event: str, **fields: Any) -> None:
        """Ein Ereignis ausgeben.

        Parameter:
            event: log, progress, start, result.
            fields: zusaetzliche JSON-Felder.
        Rueckgabewert:
            None
        Fehlerfaelle:
            keine
        Beispiel:
            reporter.emit("log", message="Start")
        """
        payload = {"event": event, **fields}
        if self.json_mode:
            self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.stream.flush()
        elif event in ("log", "progress") and fields.get("message"):
            self.stream.write(str(fields["message"]) + "\n")
            self.stream.flush()
        if self.logger is not None and event == "log":
            self.logger.write(str(fields.get("message", "")))

    def log(self, message: str) -> None:
        """Textmeldung.

        Parameter:
            message: sichtbarer Text.
        Rueckgabewert:
            None
        Fehlerfaelle:
            keine
        Beispiel:
            reporter.log("Kopiere ...")
        """
        self.emit("log", message=message)

    def progress(
        self,
        percent: float,
        message: str = "",
        bytes_done: int = 0,
        bytes_total: int = 0,
    ) -> None:
        """Fortschritt 0..100.

        Parameter:
            percent: Anteil.
            message: optionale Zeile.
            bytes_done, bytes_total: Kopierstand.
        Rueckgabewert:
            None
        Fehlerfaelle:
            keine
        Beispiel:
            reporter.progress(50, "Haelfte")
        """
        self.emit(
            "progress",
            percent=round(percent, 1),
            message=message,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
        )


def is_linux() -> bool:
    """True unter Linux.

    Parameter:
        keine
    Rueckgabewert:
        bool
    Fehlerfaelle:
        keine
    Beispiel:
        if not is_linux(): raise PibackupError("Nur Linux")
    """
    return platform.system() == "Linux"


def is_root() -> bool:
    """True, wenn effektive UID 0.

    Parameter:
        keine
    Rueckgabewert:
        bool
    Fehlerfaelle:
        keine
    Beispiel:
        if not is_root(): ...
    """
    getter = getattr(os, "geteuid", None)
    if getter is None:
        return False
    return getter() == 0


def find_tool(name: str) -> str:
    """Sucht ein Programm im PATH und in sbin.

    Parameter:
        name: z. B. e2fsck.
    Rueckgabewert:
        Absoluter Pfad.
    Fehlerfaelle:
        PibackupError wenn fehlend.
    Beispiel:
        find_tool("lsblk")
    """
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/sbin", "/usr/sbin", "/usr/bin", "/bin"):
        candidate = Path(folder) / name
        if candidate.is_file():
            return str(candidate)
    raise PibackupError(f"Werkzeug fehlt: {name}")


def run_cmd(argv: list[str], timeout: int | None = None) -> CmdResult:
    """Prozess ohne Shell.

    Parameter:
        argv: Argumentliste.
        timeout: optional Sekunden.
    Rueckgabewert:
        CmdResult
    Fehlerfaelle:
        PibackupError bei Startfehler oder Timeout.
    Beispiel:
        run_cmd(["lsblk", "-J"])
    """
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PibackupError(f"Befehl nicht gefunden: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PibackupError(f"Zeitueberschreitung: {argv[0]}") from exc
    return CmdResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def require_linux() -> None:
    """Bricht ab, wenn nicht Linux.

    Parameter:
        keine
    Rueckgabewert:
        None
    Fehlerfaelle:
        PibackupError
    Beispiel:
        require_linux()
    """
    if not is_linux():
        raise PibackupError(
            "Schreibaktionen brauchen Linux mit Blockgeraeten "
            "(Raspberry Pi OS / Debian)."
        )


def validate_disk(path: str) -> str:
    """Prueft einen Datentraegerpfad /dev/... ohne Partition.

    Parameter:
        path: Geraetepfad.
    Rueckgabewert:
        Normalisierter Pfad.
    Fehlerfaelle:
        PibackupError bei ungueltigem Muster.
    Beispiel:
        validate_disk("/dev/mmcblk0")
    """
    text = path.strip()
    if not DEVICE_RE.match(text):
        raise PibackupError(
            f"Ungueltiges Blockgeraet: {path!r}. "
            "Erlaubt sind z. B. /dev/mmcblk0 oder /dev/sda."
        )
    return text


def partition_path(disk: str, number: int) -> str:
    """Partitionsnamen fuer mmcblk/nvme vs. sd*.

    Parameter:
        disk: z. B. /dev/sda.
        number: 1-basiert.
    Rueckgabewert:
        z. B. /dev/sda1 oder /dev/mmcblk0p1.
    Fehlerfaelle:
        keine
    Beispiel:
        partition_path("/dev/mmcblk0", 1)
    """
    if disk[-1].isdigit():
        return f"{disk}p{number}"
    return f"{disk}{number}"


def safe_backup_stem(hostname: str, day: str) -> str:
    """Dateiname Hostname-Datum ohne ungueltige Zeichen.

    Parameter:
        hostname: Rechnername.
        day: YYYY-MM-DD.
    Rueckgabewert:
        Stem ohne Endung.
    Fehlerfaelle:
        keine
    Beispiel:
        safe_backup_stem("pi", "2026-08-24")
    """
    host = SAFE_NAME_RE.sub("-", hostname.strip()) or "host"
    return f"{host}-{day}"


def invoke_user_ids() -> tuple[int | None, int | None]:
    """UID/GID des aufrufenden Users unter pkexec/sudo.

    Parameter:
        keine
    Rueckgabewert:
        (uid, gid) oder (None, None).
    Fehlerfaelle:
        keine
    Beispiel:
        uid, gid = invoke_user_ids()
    """
    uid_raw = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    gid_raw = os.environ.get("SUDO_GID")
    if not uid_raw:
        return None, None
    try:
        uid = int(uid_raw)
        gid = int(gid_raw) if gid_raw else uid
    except ValueError:
        return None, None
    return uid, gid


def chown_to_invoker(path: Path) -> None:
    """Gibt die Ausgabedatei an den GUI-User zurueck.

    Parameter:
        path: Datei oder Verzeichnis.
    Rueckgabewert:
        None
    Fehlerfaelle:
        OSError wird geschluckt, wenn chown fehlt.
    Beispiel:
        chown_to_invoker(Path("backup.img.zst"))
    """
    uid, gid = invoke_user_ids()
    if uid is None or gid is None or not hasattr(os, "chown"):
        return
    try:
        os.chown(path, uid, gid)
    except OSError:
        return
