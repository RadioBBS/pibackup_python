"""
pibackup_python – Gemeinsame Projektmetadaten (eine Quelle).

Projekt:     pibackup_python
Modul:       project_meta.py
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
Zentrale Konstanten fuer Dateikoepfe, CLI, GUI und Tests.
Werte nicht in den aufrufenden Dateien verdoppeln.

Historie
--------
Version 1.0.0 – 2026-08-24 – Python-Engine und Browser-GUI, Metadatenquelle.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
Wird importiert, nicht als Programm gestartet:

    python3 -c "import project_meta as m; print(m.VERSION, m.STAND)"
"""

from __future__ import annotations

PROJEKT = "pibackup_python"
VERSION = "1.0.1"
STAND = "2026-11-02"
LIZENZ = "MIT"
UPSTREAM = "https://github.com/RaspberryFpc/pibackup (RaspberryFpc)"
AUTOR = "(FFHB) / RadioBBS"
PYTHON_MIN = "3.11"
ERSTELLT_MIT = "Cursor Grok 4.6"

BESCHREIBUNG = (
    "Python-Engine und Browser-GUI fuer Raspberry-Pi-Backup, Restore "
    "und Klonen (Startpartition plus Root, Verkleinern, zstd)."
)

ABHAENGIG = (
    "Debian-Pakete e2fsprogs, util-linux, zstd, mount; "
    "Python-Standardbibliothek (Python >= 3.11)"
)
BEZUG = "requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)"

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_COMPRESS_LEVEL = 2
DEFAULT_MODE = "backup"
LOG_STAMP = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_NAME = "pibackup_python.log"
BUFFER_SIZE = 1024 * 1024
SECTOR = 512

REPOSITORY_URL = "https://github.com/RadioBBS/pibackup_python"
UPSTREAM_URL = "https://github.com/RaspberryFpc/pibackup"
