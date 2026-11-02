"""
pibackup_python – Exclude-Dateien ohne Shell.

Projekt:     pibackup_python
Modul:       pibackup_exclude.py
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
Parst COMMAND = PFAD und loescht nur innerhalb des Mountpoints.
Kein system()/Shell. Platzhalter §USER aus SUDO_USER/USER.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
Wird von der Backup-Engine nach dem Mounten von Partition 2 genutzt.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from pibackup_common import PibackupError

COMMANDS = ("file", "fileintree", "allintree", "dir")


@dataclass
class ExcludeEntry:
    """Eine Exclude-Zeile."""

    command: str
    path: str
    line_no: int


def real_user() -> str:
    """Loginname des urspruenglichen Users.

    Parameter:
        keine
    Rueckgabewert:
        Benutzername oder leer.
    Fehlerfaelle:
        keine
    Beispiel:
        real_user()
    """
    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("PKEXEC_UID")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or ""
    )


def expand_user_token(value: str) -> str:
    """Ersetzt §USER / §user.

    Parameter:
        value: Pfad aus der Exclude-Datei.
    Rueckgabewert:
        Pfad mit Loginname.
    Fehlerfaelle:
        keine
    Beispiel:
        expand_user_token("/home/§USER/.cache")
    """
    user = real_user()
    result = value
    lower = result.lower()
    token = "§user"
    while token in lower:
        idx = lower.index(token)
        result = result[:idx] + user + result[idx + len(token) :]
        lower = result.lower()
    return result


def parse_exclude_text(text: str) -> list[ExcludeEntry]:
    """Parst den Dateiinhalt.

    Parameter:
        text: komplette Exclude-Datei.
    Rueckgabewert:
        Eintraege, Kommentare entfallen.
    Fehlerfaelle:
        PibackupError bei unbekannter Syntax.
    Beispiel:
        parse_exclude_text("file = /var/swap\\n")
    """
    entries: list[ExcludeEntry] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise PibackupError(f"Zeile {line_no}: '=' fehlt.")
        key, value = stripped.split("=", 1)
        command = key.strip().lower()
        path = expand_user_token(value.strip())
        if command not in COMMANDS:
            raise PibackupError(f"Zeile {line_no}: unbekanntes Kommando {key!r}.")
        if ".." in Path(path).parts:
            raise PibackupError(f"Zeile {line_no}: Pfad darf keine '..' enthalten.")
        entries.append(ExcludeEntry(command, path, line_no))
    return entries


def resolve_inside(mount: Path, rel: str) -> Path:
    """Loest einen Exclude-Pfad innerhalb des Mounts auf.

    Parameter:
        mount: Image-Root.
        rel: Pfad beginnend mit /.
    Rueckgabewert:
        Absoluter Pfad unter mount.
    Fehlerfaelle:
        PibackupError bei Escape.
    Beispiel:
        resolve_inside(Path("/mnt/p2"), "/var/tmp")
    """
    relative = rel.lstrip("/\\")
    target = (mount / relative).resolve()
    root = mount.resolve()
    if target != root and not target.is_relative_to(root):
        raise PibackupError(f"SECURITY: Pfad verlaesst den Mountpoint: {rel}")
    return target


def _delete_file(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    return False


def _apply_file(target: Path) -> int:
    directory = target.parent
    mask = target.name
    if not directory.is_dir():
        return 0
    count = 0
    for item in directory.iterdir():
        if item.is_dir():
            continue
        if fnmatch.fnmatch(item.name, mask):
            if _delete_file(item):
                count += 1
    return count


def _apply_fileintree(target: Path) -> int:
    directory = target.parent
    mask = target.name
    if not directory.exists():
        return 0
    count = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if fnmatch.fnmatch(name, mask):
                if _delete_file(Path(root) / name):
                    count += 1
    return count


def _apply_allintree(target: Path, mount: Path) -> int:
    if target.resolve() == mount.resolve():
        raise PibackupError("SECURITY: Root des Mounts wird nicht geleert.")
    if not target.is_dir():
        return 0
    count = 0
    for child in list(target.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
            count += 1
        elif _delete_file(child) or (child.is_dir() and not child.exists()):
            count += 1
        else:
            shutil.rmtree(child, ignore_errors=True)
            count += 1
    return count


def _apply_dir(target: Path, mount: Path) -> int:
    if target.resolve() == mount.resolve():
        raise PibackupError("SECURITY: Root des Mounts wird nicht geloescht.")
    if not target.exists():
        return 0
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
        return 1
    if _delete_file(target):
        return 1
    return 0


def apply_exclude(file_path: Path, mount: Path) -> int:
    """Wendet eine Exclude-Datei unter mount an.

    Parameter:
        file_path: .exclude-Datei.
        mount: gemountete Root-Partition.
    Rueckgabewert:
        Anzahl Loeschaktionen (grob).
    Fehlerfaelle:
        PibackupError, OSError
    Beispiel:
        apply_exclude(Path("share/exclude/default.exclude"), Path("/mnt"))
    """
    if not file_path.is_file():
        raise PibackupError(f"Exclude-Datei fehlt: {file_path}")
    if not mount.is_dir():
        raise PibackupError(f"Mountpoint fehlt: {mount}")
    entries = parse_exclude_text(file_path.read_text(encoding="utf-8", errors="replace"))
    total = 0
    for entry in entries:
        target = resolve_inside(mount, entry.path)
        if entry.command == "file":
            total += _apply_file(target)
        elif entry.command == "fileintree":
            total += _apply_fileintree(target)
        elif entry.command == "allintree":
            total += _apply_allintree(target, mount)
        else:
            total += _apply_dir(target, mount)
    return total


def bundled_exclude(name: str) -> Path:
    """Pfad zu share/exclude/<name>.

    Parameter:
        name: Dateiname.
    Rueckgabewert:
        Path
    Fehlerfaelle:
        keine
    Beispiel:
        bundled_exclude("default.exclude")
    """
    return Path(__file__).resolve().parent / "share" / "exclude" / name
