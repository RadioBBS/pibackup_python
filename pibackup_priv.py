"""
pibackup_python – Rechteanhebung nur fuer die Schreibaktion.

Projekt:     pibackup_python
Modul:       pibackup_priv.py
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
Baut argv fuer pkexec (bevorzugt) oder sudo. Die GUI laeuft als
normaler User und startet die Engine nur fuer Backup/Restore/Clone
mit erhoehten Rechten.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
Wird von der Browser-GUI importiert. CLI-User rufen sudo selbst auf.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from pibackup_common import PibackupError, is_root

WRITE_COMMANDS = frozenset({"backup", "restore", "clone"})


def engine_script() -> Path:
    """Absoluter Pfad zu pibackup_python.py.

    Parameter:
        keine
    Rueckgabewert:
        Path
    Fehlerfaelle:
        PibackupError wenn die Datei fehlt.
    Beispiel:
        engine_script()
    """
    path = Path(__file__).resolve().parent / "pibackup_python.py"
    if not path.is_file():
        raise PibackupError(f"Engine nicht gefunden: {path}")
    return path


def privilege_prefix() -> list[str]:
    """pkexec oder sudo, leer wenn schon root.

    Parameter:
        keine
    Rueckgabewert:
        Argumentliste-Prefix.
    Fehlerfaelle:
        PibackupError wenn keines vorhanden und nicht root.
    Beispiel:
        privilege_prefix()
    """
    if is_root():
        return []
    pkexec = shutil.which("pkexec")
    if pkexec:
        return [pkexec]
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo]
    raise PibackupError(
        "Weder pkexec noch sudo gefunden. Schreibaktionen brauchen root."
    )


def wrap_engine(args: list[str]) -> list[str]:
    """Vollstaendiges argv: [pkexec] python pibackup_python.py ...

    Parameter:
        args: Subcommand und Flags der Engine, ohne Interpreter.
    Rueckgabewert:
        argv fuer subprocess.
    Fehlerfaelle:
        PibackupError
    Beispiel:
        wrap_engine(["backup", "--yes", "--json"])
    """
    python = sys.executable or "python3"
    cmd = privilege_prefix() + [python, str(engine_script())] + args
    return cmd


def needs_privilege(command: str) -> bool:
    """True fuer backup/restore/clone.

    Parameter:
        command: Subcommand.
    Rueckgabewert:
        bool
    Fehlerfaelle:
        keine
    Beispiel:
        needs_privilege("backup")
    """
    return command in WRITE_COMMANDS
