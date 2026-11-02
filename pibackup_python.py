#!/usr/bin/env python3
"""
pibackup_python – CLI-Engine fuer Backup, Restore und Clone.

Projekt:     pibackup_python
Modul:       pibackup_python.py
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
Root-Engine: sichert Startpartition plus Root, stellt Images wieder
her und klont Datentraeger. Die Browser-GUI startet diese Engine
per pkexec/sudo nur fuer die Schreibaktion.

Historie
--------
Version 1.0.0 – 2026-08-24 – Python-CLI, Defaults Shrink+Pack, Hostname-Datum.
Version 1.0.1 – 2026-11-02 – Startausgabe und Python 3.11 nach Styleguide 2.1.0.

Aufruf / Nutzung
----------------
  python3 pibackup_python.py --help
  python3 pibackup_python.py --version
  python3 pibackup_python.py devices
  python3 pibackup_python.py defaults
  python3 pibackup_python.py backup --dry-run
  sudo python3 pibackup_python.py backup --yes
  sudo python3 pibackup_python.py restore --image DATEI --dest /dev/sdX --yes
  sudo python3 pibackup_python.py clone --source /dev/mmcblk0 --dest /dev/sdX --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import project_meta as meta
except ImportError:
    sys.stderr.write("Fehler: project_meta.py fehlt im gleichen Verzeichnis.\n")
    raise SystemExit(2)

from pibackup_common import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    CancelFlag,
    PibackupError,
    Reporter,
)
from pibackup_engine import (
    BackupOptions,
    CloneOptions,
    RestoreOptions,
    fill_backup_defaults,
    inspect_path,
    list_disks_payload,
    plan_backup,
    plan_clone,
    plan_restore,
    run_backup,
    run_clone,
    run_restore,
)
from pibackup_log import Logger

HELP_TEXT = f"""
{meta.PROJEKT} {meta.VERSION} – {meta.BESCHREIBUNG}

Stand: {meta.STAND}
Lizenz: {meta.LIZENZ}

Verwendung:
  python3 pibackup_python.py --help
  python3 pibackup_python.py --version
  python3 pibackup_python.py devices
  python3 pibackup_python.py defaults
  python3 pibackup_python.py backup --dry-run
  sudo python3 pibackup_python.py backup --yes
  sudo python3 pibackup_python.py restore --image DATEI --dest GERAET --yes
  sudo python3 pibackup_python.py clone --source GERAET --dest GERAET --yes

Befehle:
  devices    Blockgeraete listen (ohne root)
  defaults   Voreinstellungen (Backup, Startdisk, externe Disk)
  backup     Image der Partitionen 1+2 erzeugen
  restore    Image auf ein Geraet schreiben
  clone      Geraet auf Geraet klonen
  inspect    MBR eines Images oder Geraets anzeigen

Globale Parameter:

  --help / -h
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Diese Hilfe anzeigen und beenden.
      Beispiel:     python3 pibackup_python.py --help

  --version
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Versionsnummer, Datum und Programmbeschreibung.
      Beispiel:     python3 pibackup_python.py --version

  --json
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Ereignisse als JSON-Zeilen (fuer die GUI).
      Beispiel:     sudo python3 pibackup_python.py backup --yes --json

  --log
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Zusaetzlich UTF-8-Logdatei schreiben.
      Beispiel:     python3 pibackup_python.py devices --log

  --log-file
      Typ:          Pfad
      Standardwert: {meta.DEFAULT_LOG_NAME}
      Beschreibung: Logdatei (ASCII-Name). Schaltet --log ein.
      Beispiel:     python3 pibackup_python.py devices --log-file pibackup_python.log

  --dry-run
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Nur Schritte zeigen, nichts schreiben.
      Beispiel:     python3 pibackup_python.py backup --dry-run

  --yes
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Schreibaktion bestaetigen (Backup/Restore/Clone).
      Beispiel:     sudo python3 pibackup_python.py backup --yes

  --cancel-file
      Typ:          Pfad
      Standardwert: keiner
      Beschreibung: Datei, in die die GUI eine 1 schreibt zum Abbruch.
      Beispiel:     sudo python3 pibackup_python.py backup --yes --cancel-file /tmp/pb.cancel

  --Ende / -E
      Typ:          Flag
      Standardwert: aus
      Beschreibung: Am Ende auf Taste/Enter warten.
      Beispiel:     python3 pibackup_python.py --version --Ende

Backup (Defaults: Startdisk, externe Disk, Hostname-Datum, Shrink+Pack):

  --source
      Typ:          Blockgeraet
      Standardwert: Disk mit der Startpartition (/)
      Beschreibung: Quelldatentraeger, nicht die Partition.
      Beispiel:     --source /dev/mmcblk0

  --dest
      Typ:          Pfad
      Standardwert: <externe Disk>/<Hostname>-<Datum>.img.zst
      Beschreibung: Zieldatei (.img oder .img.zst).
      Beispiel:     --dest /mnt/usb/pi-2026-08-24.img.zst

  --no-shrink / --no-compress
      Typ:          Flag
      Standardwert: Shrink und Pack an
      Beschreibung: Verkleinern bzw. zstd abschalten.
      Beispiel:     sudo python3 pibackup_python.py backup --yes --no-compress

  --compress-level
      Typ:          int 1-19
      Standardwert: {meta.DEFAULT_COMPRESS_LEVEL}
      Beschreibung: zstd-Stufe.
      Beispiel:     --compress-level 3

  --exclude
      Typ:          Pfad
      Standardwert: keiner (optional share/exclude/raspberry.exclude)
      Beschreibung: Exclude-Datei im Image anwenden.
      Beispiel:     --exclude share/exclude/raspberry.exclude

Restore / Clone:

  --image
      Typ:          Pfad
      Standardwert: keiner (Pflicht bei restore)
      Beschreibung: .img oder .img.zst
      Beispiel:     --image /mnt/usb/pi-2026-08-24.img.zst

  --dest / --source
      Typ:          Blockgeraet
      Beschreibung: Ziel- bzw. Quelldatentraeger.
      Beispiel:     --dest /dev/sdb

Beispiele:
  python3 pibackup_python.py --help
  python3 pibackup_python.py --version
  python3 pibackup_python.py defaults
  python3 pibackup_python.py backup --dry-run
  sudo python3 pibackup_python.py backup --yes
  sudo python3 pibackup_python.py restore --image /mnt/usb/host-2026-08-24.img.zst --dest /dev/sdb --yes
  sudo python3 pibackup_python.py clone --source /dev/mmcblk0 --dest /dev/sdb --yes
""".strip()


def version_text() -> str:
    """--version-Block.

    Parameter:
        keine
    Rueckgabewert:
        Text
    Fehlerfaelle:
        keine
    Beispiel:
        print(version_text())
    """
    return f"{meta.PROJEKT} {meta.VERSION}\nStand: {meta.STAND}\n{meta.BESCHREIBUNG}"


class EngineParser(argparse.ArgumentParser):
    """ArgumentParser mit deutscher Fehlerausgabe."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"Fehler: {message}\n")
        raise SystemExit(EXIT_USAGE)


def build_parser() -> EngineParser:
    """CLI-Parser.

    Parameter:
        keine
    Rueckgabewert:
        EngineParser
    Fehlerfaelle:
        keine
    Beispiel:
        build_parser().parse_args(["--version"])
    """
    parser = EngineParser(
        prog="pibackup_python.py",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--log-file", dest="log_file", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--cancel-file", dest="cancel_file", default="")
    parser.add_argument("--Ende", "-E", dest="ende", action="store_true")
    parser.add_argument("command", nargs="?", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--dest", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--no-shrink", action="store_true")
    parser.add_argument("--no-compress", action="store_true")
    parser.add_argument(
        "--compress-level",
        type=int,
        default=meta.DEFAULT_COMPRESS_LEVEL,
    )
    parser.add_argument("--exclude", default="")
    parser.add_argument("--remove-ssh", action="store_true")
    parser.add_argument("--remove-dhcp", action="store_true")
    parser.add_argument("--keep-img", action="store_true")
    parser.add_argument("--no-fill", action="store_true")
    parser.add_argument("--change-id", action="store_true", default=True)
    parser.add_argument("--no-change-id", action="store_true")
    parser.add_argument("--enable-ssh", action="store_true")
    parser.add_argument("--inspect-path", dest="inspect_path", default="")
    return parser


def wait_ende() -> None:
    """Wartet auf Enter.

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


def dump(data: object, as_json: bool) -> None:
    """Gibt dict/list als JSON oder lesbaren Text aus.

    Parameter:
        data: serialisierbar.
        as_json: JSON erzwingen.
    Rueckgabewert:
        None
    Fehlerfaelle:
        keine
    Beispiel:
        dump({"a": 1}, True)
    """
    text = json.dumps(data, ensure_ascii=False, indent=2)
    sys.stdout.write(text + "\n")


def run_command(args: argparse.Namespace, reporter: Reporter, cancel: CancelFlag) -> int:
    """Fuehrt den Subcommand aus.

    Parameter:
        args: Namespace
        reporter: Ausgabe
        cancel: Abbruchdatei
    Rueckgabewert:
        Exitcode
    Fehlerfaelle:
        PibackupError wird vom Aufrufer gefangen.
    Beispiel:
        run_command(ns, reporter, CancelFlag())
    """
    command = args.command
    change_id = not args.no_change_id
    if command in ("devices", "defaults"):
        payload = list_disks_payload()
        if command == "devices":
            dump(payload["devices"] if args.json else payload, True)
        else:
            dump(payload["defaults"], True)
        return EXIT_OK
    if command == "inspect":
        target = args.inspect_path or args.source or args.image or args.dest
        if not target:
            raise PibackupError("inspect braucht --inspect-path, --image oder --source.")
        dump(inspect_path(target), True)
        return EXIT_OK
    if command == "backup":
        opts = fill_backup_defaults(
            BackupOptions(
                source=args.source,
                dest=args.dest,
                shrink=not args.no_shrink,
                compress=not args.no_compress,
                compress_level=args.compress_level,
                exclude=args.exclude,
                remove_ssh=args.remove_ssh,
                remove_dhcp=args.remove_dhcp,
                keep_img=args.keep_img,
                fill_empty=not args.no_fill,
                dry_run=args.dry_run,
                yes=args.yes,
            )
        )
        if args.dry_run:
            dump({"action": "backup", "steps": plan_backup(opts).steps, "dest": opts.dest}, args.json)
            if not args.json:
                for step in plan_backup(opts).steps:
                    reporter.log(step)
            return EXIT_OK
        run_backup(opts, reporter, cancel)
        return EXIT_OK
    if command == "restore":
        if not args.image or not args.dest:
            raise PibackupError("restore braucht --image und --dest.")
        opts = RestoreOptions(
            image=args.image,
            dest=args.dest,
            change_id=change_id,
            enable_ssh=args.enable_ssh,
            dry_run=args.dry_run,
            yes=args.yes,
        )
        if args.dry_run:
            dump({"action": "restore", "steps": plan_restore(opts).steps}, args.json)
            return EXIT_OK
        run_restore(opts, reporter, cancel)
        return EXIT_OK
    if command == "clone":
        if not args.source or not args.dest:
            raise PibackupError("clone braucht --source und --dest.")
        opts = CloneOptions(
            source=args.source,
            dest=args.dest,
            change_id=change_id,
            dry_run=args.dry_run,
            yes=args.yes,
        )
        if args.dry_run:
            dump({"action": "clone", "steps": plan_clone(opts).steps}, args.json)
            return EXIT_OK
        run_clone(opts, reporter, cancel)
        return EXIT_OK
    raise PibackupError(
        f"Unbekannter Befehl {command!r}. Erlaubt: devices, defaults, "
        "backup, restore, clone, inspect."
    )


def main(argv: list[str] | None = None) -> int:
    """Einstieg.

    Parameter:
        argv: optional, sonst sys.argv[1:]
    Rueckgabewert:
        Exitcode
    Fehlerfaelle:
        argparse SystemExit
    Beispiel:
        raise SystemExit(main(["--version"]))
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help or (not args.command and not args.version):
        sys.stdout.write(HELP_TEXT + "\n")
        if args.ende:
            wait_ende()
        return EXIT_OK
    if args.version:
        sys.stdout.write(version_text() + "\n")
        if args.ende:
            wait_ende()
        return EXIT_OK
    if args.compress_level < 1 or args.compress_level > 19:
        sys.stderr.write("Fehler: --compress-level muss zwischen 1 und 19 liegen.\n")
        return EXIT_USAGE
    log_on = args.log or bool(args.log_file)
    logger = Logger(
        enabled=log_on,
        log_file=Path(args.log_file) if args.log_file else None,
        stream=None,
    )
    reporter = Reporter(json_mode=args.json, logger=logger)
    cancel = CancelFlag(Path(args.cancel_file) if args.cancel_file else None)
    code = EXIT_OK
    try:
        if args.json:
            reporter.emit("start", pid=os_getpid(), command=args.command)
        code = run_command(args, reporter, cancel)
        if args.json:
            reporter.emit("result", ok=True, exit_code=code)
    except PibackupError as exc:
        sys.stderr.write(f"Fehler: {exc}\n")
        if args.json:
            reporter.emit("result", ok=False, message=str(exc), exit_code=EXIT_ERROR)
        code = EXIT_ERROR
    finally:
        logger.close()
        if args.ende:
            wait_ende()
    return code


def os_getpid() -> int:
    """Prozess-ID.

    Parameter:
        keine
    Rueckgabewert:
        pid
    Fehlerfaelle:
        keine
    Beispiel:
        os_getpid()
    """
    import os

    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
