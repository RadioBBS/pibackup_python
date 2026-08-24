# Changelog

```
pibackup_python – Release-Historie.

Projekt:     pibackup_python
Modul:       CHANGELOG.md
Version:     1.0.0
Stand:       2026-08-24
Abhaengig:   Debian-Pakete e2fsprogs, util-linux, zstd, mount; Python-Standardbibliothek (Python >= 3.10)
Bezug:       requirements.txt (leer – Stdlib only; Linux-Werkzeuge siehe Datei)
Lizenz:      MIT
Upstream:    https://github.com/RaspberryFpc/pibackup (RaspberryFpc)
Erstellt mit: Cursor Grok 4.6
Autor:       (FFHB) / RadioBBS
```

## [1.0.0] – 2026-08-24

### Added

- Python-Engine mit CLI: Backup, Restore, Clone
- Browser-GUI als normaler User, `pkexec`/`sudo` nur fuer die Schreibaktion
- Defaults: Backup, Startdisk, externe Disk, Hostname-Datum, Shrink und zstd
- Keine Dateien des Pascal-Originals im Repo; Upstream nur als Herkunftsangabe
