"""CLI entry point (the desktop UI is `python -m app.ui`).

    python -m app.main run --excel "ordenes.xlsx" --consolidado "CORTE 103" --corte-date 09/10/2026 \
        [--dry-run | --live] [--only-row 23] [--limit 5] [--auto-confirm] [--attach-xml]
    python -m app.main status [--consolidado "CORTE 103"]
    python -m app.main retry --failed
    python -m app.main report [--consolidado "CORTE 103"]
    python -m app.main template plantilla.xlsx
    python -m app.main set-credential MYE_PASSWORD
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app import config
from app.excel_reader import TemplateError, write_template
from app.models import DocState, DocStatus, InvalidRow, Row
from app.report import write_report
from app.service import fmt_money, load_batch
from app.state import StateStore
from app.text_utils import mask_id, parse_user_date

console = Console()
log = logging.getLogger("doc_soporte")


def setup_logging() -> None:
    if log.handlers:
        return
    config.LOGS_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        config.LOGS_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)


def _parse_date(text: str) -> date:
    try:
        return parse_user_date(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _print_invalid(invalid: list[InvalidRow]) -> None:
    if not invalid:
        return
    table = Table(title=f"Filas inválidas (omitidas): {len(invalid)}", style="red")
    for col in ("Fila", "ID", "Nombre", "OC", "Total", "Errores"):
        table.add_column(col)
    for r in invalid:
        table.add_row(str(r.excel_row), r.raw["id"], r.raw["name"], r.raw["oc"],
                      r.raw["total"], "; ".join(r.errors))
    console.print(table)


def _print_states(title: str, items: list[tuple[Row | None, DocState]], mask: bool) -> None:
    table = Table(title=title)
    for col in ("Fila", "Consolidado", "Proveedor", "ID", "OC", "Total", "Estado", "DE", "CADI", "Error"):
        table.add_column(col)
    for row, st in items:
        table.add_row(
            str(row.excel_row) if row else "-",
            st.consolidado,
            st.provider_name,
            mask_id(st.provider_id) if mask else st.provider_id,
            st.oc,
            fmt_money(st.total),
            st.status.value if st.status != DocStatus.FAILED
            else f"FAILED (último OK: {st.last_ok_status.value})",
            st.de_number or "",
            st.cadi_id or "",
            st.error or "",
        )
    console.print(table)


def cmd_run(args: argparse.Namespace) -> int:
    mode = "LIVE" if args.live else "DRY-RUN"
    console.rule(f"[bold]Consolidado {args.consolidado!r} — corte {args.corte_date:%d/%m/%Y} — modo {mode}")

    result = load_batch(Path(args.excel), args.consolidado, args.corte_date,
                        only_row=args.only_row, limit=args.limit)
    _print_invalid(result.invalid)
    for w in result.warnings:
        console.print(f"[yellow]{w}")
    if not result.items:
        console.print("[yellow]No hay filas válidas para procesar.")
        return 0

    _print_states(f"Filas a procesar: {len(result.items)}", result.items, args.mask_ids)
    console.print(f"Observación MyE:  [cyan]{result.mye_observation}")
    console.print(f"Observación CADI: [cyan]{result.cadi_observation}")

    # Phase 1 stops here: browser automation (MyE-invoice / CADI) arrives in phase 2+.
    console.print("\n[bold yellow]Fase 1:[/] lectura, validación y estado listos. "
                  "La automatización de MyE-invoice y CADI aún no está implementada; "
                  "no se ha abierto ningún navegador ni se ha creado ningún documento.")
    return 2 if args.live else 0


def cmd_status(args: argparse.Namespace) -> int:
    with StateStore(config.STATE_DB) as store:
        states = store.list(consolidado=args.consolidado)
    if not states:
        console.print("[yellow]Sin registros.")
        return 0
    _print_states(f"Estado ({len(states)})", [(None, s) for s in states], args.mask_ids)
    summary: dict[str, int] = {}
    for s in states:
        summary[s.status.value] = summary.get(s.status.value, 0) + 1
    console.print(" | ".join(f"{k}: {v}" for k, v in summary.items()))
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    with StateStore(config.STATE_DB) as store:
        failed = store.list(status=DocStatus.FAILED)
    if not failed:
        console.print("[green]No hay filas fallidas.")
        return 0
    _print_states(f"Filas fallidas ({len(failed)})", [(None, s) for s in failed], args.mask_ids)
    console.print("[yellow]El reintento se reanudará desde el último estado OK "
                  "cuando la automatización esté implementada (fase 2+).")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with StateStore(config.STATE_DB) as store:
        states = store.list(consolidado=args.consolidado)
    csv_path, xlsx_path = write_report(states, config.REPORTS_DIR, args.consolidado or "todos")
    console.print(f"Reporte: {csv_path}\n         {xlsx_path}")
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    console.print(f"Plantilla creada: {write_template(Path(args.path))}")
    return 0


def cmd_set_credential(args: argparse.Namespace) -> int:
    value = getpass.getpass(f"Valor para {args.name} (no se mostrará): ")
    if not value:
        console.print("[red]Valor vacío; no se guardó nada.")
        return 1
    if args.keyring:
        import keyring

        keyring.set_password(config.KEYRING_SERVICE, args.name, value)
        console.print(f"[green]{args.name} guardada en el Administrador de credenciales de Windows.")
    else:
        path = config.save_credential(args.name, value)
        console.print(f"[green]{args.name} guardada en {path}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.main", description="Documento Soporte Automático")
    parser.add_argument("--mask-ids", action="store_true", help="Enmascarar cédulas en consola")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="Procesar un consolidado")
    p.add_argument("--excel", required=True)
    p.add_argument("--consolidado", required=True, help="Nombre del consolidado, ej. 'CORTE 103'")
    p.add_argument("--corte-date", required=True, type=_parse_date, help="DD/MM/AAAA")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="(por defecto) llena formularios sin pulsar botones irreversibles")
    mode.add_argument("--live", action="store_true", help="Ejecuta acciones irreversibles con confirmación")
    p.add_argument("--only-row", type=int, help="Procesar solo esta fila del Excel")
    p.add_argument("--limit", type=int, help="Máximo de filas a procesar")
    p.add_argument("--auto-confirm", action="store_true",
                   help="No preguntar s/n en filas que pasaron todas las validaciones (solo con --live)")
    p.add_argument("--attach-xml", action="store_true", help="Adjuntar también el XML en CADI")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="Ver estado")
    p.add_argument("--consolidado")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("retry", help="Reintentar filas fallidas")
    p.add_argument("--failed", action="store_true", required=True)
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("report", help="Generar reporte CSV/XLSX")
    p.add_argument("--consolidado")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("template", help="Crear la plantilla Excel vacía")
    p.add_argument("path")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("set-credential", help="Guardar una credencial en .env")
    p.add_argument("name", choices=config.CREDENTIAL_NAMES)
    p.add_argument("--keyring", action="store_true",
                   help="Guardar en el Administrador de credenciales de Windows en vez de .env")
    p.set_defaults(func=cmd_set_credential)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    if getattr(args, "auto_confirm", False) and not getattr(args, "live", False):
        console.print("[yellow]--auto-confirm se ignora en modo dry-run.")
    try:
        return args.func(args)
    except (FileNotFoundError, KeyError, TemplateError, ValueError) as exc:
        console.print(f"[red]{exc}")
        return 1
    except PermissionError:
        console.print("[red]No se pudo leer el archivo. Ciérrelo en Excel e intente de nuevo.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
