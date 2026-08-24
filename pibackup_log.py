"""
pibackup_python – UTF-8-Protokoll ohne BOM.

Projekt:     pibackup_python
Modul:       pibackup_log.py
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
Schreibt Zeitstempel-Zeilen nach stdout und optional in eine Logdatei.

Historie
--------
Version 1.0.0 – 2026-08-24 – Modul angelegt.

Aufruf / Nutzung
----------------
Wird von Engine und GUI importiert.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TextIO

import project_meta as meta


class Logger:
    """Protokoll mit optionaler Datei.

    Parameter:
        enabled: Datei schreiben.
        log_file: Zielpfad oder None.
        stream: Konsolenausgabe.
    """

    def __init__(
        self,
        enabled: bool = False,
        log_file: Path | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.enabled = enabled
        self.log_file = log_file
        self.stream = stream
        self._handle: TextIO | None = None
        if enabled:
            target = log_file or Path(meta.DEFAULT_LOG_NAME)
            self.log_file = target
            self._handle = target.open("a", encoding="utf-8", newline="\n")

    def write(self, message: str) -> None:
        """Eine Zeile mit Zeitstempel ausgeben.

        Parameter:
            message: Text ohne erzwungenen Umbruch.
        Rueckgabewert:
            None
        Fehlerfaelle:
            OSError beim Dateischreiben.
        Beispiel:
            Logger(True).write("Start")
        """
        line = f"{datetime.now().strftime(meta.LOG_STAMP)} {message}"
        if self.stream is not None:
            self.stream.write(line + "\n")
            self.stream.flush()
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        """Schliesst die Logdatei.

        Parameter:
            keine
        Rueckgabewert:
            None
        Fehlerfaelle:
            keine
        Beispiel:
            logger.close()
        """
        if self._handle is not None:
            self._handle.close()
            self._handle = None
