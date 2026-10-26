"""Desktop UI (CustomTkinter). Run with:  python -m app.ui

Long tasks run in a worker thread; results are pushed back to the Tk thread
with `after`, so the window never freezes (needed for the browser phases).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

import customtkinter as ctk

from app import config
from app.automation import BrowserWorker, UiBridge
from app.excel_reader import TEMPLATE_HEADERS, TemplateError, write_template
from app.instance import acquire_single_instance
from app.service import LoadResult, fmt_money, load_batch
from app.text_utils import parse_user_date
from app.ui_dialogs import ConfirmRegisterDialog
from app.ui_settings import SettingsTabMixin
from app.ui_tracking import TrackingView, make_tree, status_text
from app.ui_widgets import ProgressPanel

log = logging.getLogger("doc_soporte")

APP_TITLE = "Documento Soporte Automático"
MODE_SIM, MODE_REAL = "Simulación", "Real"
MODE_NOTES = {
    MODE_SIM: "Simulación: pendientes → MyE hasta «Previsualizar»; con ZIP descargado → CADI hasta antes de "
              "«Cargar». No registra nada.",
    MODE_REAL: "REAL: registra en MyE (DIAN), descarga el ZIP, crea la factura en CADI y la pasa a "
               "«En revisión».",
}
class App(SettingsTabMixin, ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x700")
        self.minsize(980, 600)
        self.after(0, lambda: self.state("zoomed"))
        self.excel_path: Path | None = None
        self.last_result: LoadResult | None = None
        self.row_iids: dict[str, str] = {}  # OC -> tree item id
        self.worker = BrowserWorker()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ttk widgets are not scaled by CustomTkinter; match the window DPI scaling.
        scaling = self._get_window_scaling()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=int(26 * scaling), font=("Segoe UI", 11))
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))

        header = ctk.CTkFrame(self, corner_radius=0, fg_color="#1F4E78")
        header.pack(fill="x")
        ctk.CTkLabel(header, text=APP_TITLE, text_color="white",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(header, text="ELOGIA SOLUCIONES LOGÍSTICAS · MyE-invoice + CADI",
                     text_color="#D0E1F2").pack(side="left", pady=12)

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=14, pady=(6, 14))
        self._build_new_tab(tabs.add("Nuevo consolidado"))
        self.tracking = TrackingView(tabs.add("Seguimiento"), "Solo el consolidado en curso (el último "
                                     "validado o procesado). Los anteriores están en «Registro».", history=False)
        self.history = TrackingView(tabs.add("Registro"), "Historial acumulado de todos los consolidados, "
                                    "para revisar cortes anteriores.", history=True)
        self._build_settings_tab(tabs.add("Configuración"))
        views = {"Seguimiento": self.tracking, "Registro": self.history}
        tabs.configure(command=lambda: tabs.get() in views and views[tabs.get()].refresh())

    # ------------------------------------------------------------------ helpers
    def run_async(self, work: Callable[[], object], on_done: Callable[[object], None]) -> None:
        """Run `work` in a thread; call on_done(result_or_exception) in the UI thread."""
        self.progress.pack(side="left", padx=8)
        self.progress.start()

        def finish(result: object) -> None:
            self.progress.stop()
            self.progress.pack_forget()
            on_done(result)

        def target() -> None:
            try:
                result: object = work()
            except (TemplateError, ValueError, PermissionError, FileNotFoundError) as exc:
                log.warning("Task rejected: %s", exc)
                result = exc
            except Exception as exc:  # reported to the user in the UI thread
                log.exception("Background task failed")
                result = exc
            self.after(0, lambda: finish(result))

        threading.Thread(target=target, daemon=True).start()

    def log_msg(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------- new batch tab
    def _build_new_tab(self, tab) -> None:
        form = ctk.CTkFrame(tab)
        form.pack(fill="x", pady=(4, 0))
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Archivo Excel").grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self.file_label = ctk.CTkLabel(form, text="Ningún archivo seleccionado", anchor="w",
                                       text_color="gray")
        self.file_label.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(form, text="Examinar…", width=110, command=self.pick_file).grid(row=0, column=2, padx=6)
        ctk.CTkButton(form, text="Descargar plantilla", width=150, fg_color="gray40",
                      command=self.save_template).grid(row=0, column=3, padx=(0, 12))

        ctk.CTkLabel(form, text="Nombre del consolidado").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        self.consolidado_entry = ctk.CTkEntry(form, placeholder_text="Ej: CORTE 103")
        self.consolidado_entry.grid(row=1, column=1, sticky="ew")

        ctk.CTkLabel(form, text="Fecha del corte").grid(row=2, column=0, padx=12, pady=8, sticky="w")
        self.date_entry = ctk.CTkEntry(form, placeholder_text="DD/MM/AAAA", width=160)
        self.date_entry.grid(row=2, column=1, sticky="w")

        ctk.CTkLabel(form, text="Modo").grid(row=3, column=0, padx=12, pady=8, sticky="w")
        self.mode = ctk.CTkSegmentedButton(form, values=[MODE_SIM, MODE_REAL], command=self._on_mode)
        self.mode.set(MODE_SIM)
        self.mode.grid(row=3, column=1, sticky="w")
        self.mode_note = ctk.CTkLabel(form, text=MODE_NOTES[MODE_SIM], text_color="gray")
        self.mode_note.grid(row=4, column=1, columnspan=3, pady=(0, 8), sticky="w")

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.pack(fill="x", pady=8)
        self.validate_btn = ctk.CTkButton(actions, text="Validar Excel", width=160, command=self.validate)
        self.validate_btn.pack(side="left")
        self.process_btn = ctk.CTkButton(actions, text="Procesar (simulación)", width=180,
                                         state="disabled", command=self.process)
        self.process_btn.pack(side="left", padx=8)
        self.stop_btn = ctk.CTkButton(actions, text="Detener", width=100, state="disabled",
                                      fg_color="#B42318", hover_color="#912018", command=self.stop)
        self.stop_btn.pack(side="left")
        self.progress = ctk.CTkProgressBar(actions, mode="indeterminate", width=160)  # shown while busy

        cards = ctk.CTkFrame(tab, fg_color="transparent")
        cards.pack(fill="x")
        self.card_values = {}
        for key, title in (("valid", "Filas válidas"), ("invalid", "Filas con error"),
                           ("total", "Total a pagar"), ("obs", "Observación MyE / CADI")):
            card = ctk.CTkFrame(cards)
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ctk.CTkLabel(card, text=title, text_color="gray").pack(anchor="w", padx=12, pady=(8, 0))
            value = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=16, weight="bold"), anchor="w")
            value.pack(anchor="w", padx=12, pady=(0, 8))
            self.card_values[key] = value

        self.progress_panel = ProgressPanel(tab)
        self.progress_panel.pack(fill="x", pady=(8, 0))

        self.rows_tree = make_tree(tab, [
            ("Fila", 50, "center"), ("ID", 110, "w"), ("Proveedor", 280, "w"),
            ("OC", 110, "center"), ("Total", 110, "e"), ("Estado", 150, "w"), ("Avance", 70, "center"),
            ("Detalle", 300, "w"),
        ])

        self.log_box = ctk.CTkTextbox(tab, height=90, state="disabled")
        self.log_box.pack(fill="x", pady=(8, 0))

    def pick_file(self) -> None:
        path = filedialog.askopenfilename(title="Seleccione el Excel de órdenes de compra",
                                          filetypes=[("Excel", "*.xlsx")])
        if path:
            self.excel_path = Path(path)
            self.file_label.configure(text=self.excel_path.name, text_color=("black", "white"))
            self.log_msg(f"Archivo: {self.excel_path}")

    def save_template(self) -> None:
        path = filedialog.asksaveasfilename(title="Guardar plantilla", defaultextension=".xlsx",
                                            initialfile="Plantilla ordenes de compra.xlsx",
                                            filetypes=[("Excel", "*.xlsx")])
        if path:
            write_template(Path(path))
            self.log_msg(f"Plantilla guardada en {path}")
            os.startfile(path)  # opens it in Excel (Windows only)

    def validate(self) -> None:
        if not self.excel_path:
            messagebox.showwarning(APP_TITLE, "Seleccione el archivo Excel.")
            return
        consolidado = self.consolidado_entry.get().strip()
        if not consolidado:
            messagebox.showwarning(APP_TITLE, "Escriba el nombre del consolidado.")
            return
        try:
            corte_date = parse_user_date(self.date_entry.get())
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        if corte_date > date.today():
            if not messagebox.askyesno(APP_TITLE, "La fecha del corte es futura. ¿Continuar?"):
                return

        self.validate_btn.configure(state="disabled")
        excel = self.excel_path
        self.run_async(lambda: load_batch(excel, consolidado, corte_date), self._show_result)

    def _show_result(self, result: object) -> None:
        self.validate_btn.configure(state="normal")
        self.rows_tree.delete(*self.rows_tree.get_children())
        if isinstance(result, Exception):
            msg = str(result)
            if isinstance(result, TemplateError):
                msg += "\n\nColumnas esperadas: " + " | ".join(TEMPLATE_HEADERS)
            elif isinstance(result, PermissionError):
                msg = "No se pudo leer el archivo. Ciérrelo en Excel e intente de nuevo."
            messagebox.showerror(APP_TITLE, msg)
            self.log_msg(f"Error: {msg}")
            return

        assert isinstance(result, LoadResult)
        self.last_result = result
        self.row_iids = {}
        self.tracking.show(result.consolidado)  # a new corte replaces the previous one in Seguimiento
        for row, st in result.items:
            self.row_iids[row.oc] = self.rows_tree.insert("", "end", values=(
                row.excel_row, row.provider_id, row.provider_name, row.oc,
                fmt_money(row.total), status_text(st), "", "OK"))
        self.process_btn.configure(state="normal" if result.items else "disabled")
        for inv in result.invalid:
            self.rows_tree.insert("", "end", tags=("invalid",), values=(
                inv.excel_row, inv.raw.get("id", ""), inv.raw.get("name", ""), inv.raw.get("oc", ""),
                inv.raw.get("total", ""), "Con error (se omite)", "", "; ".join(inv.errors)))

        total = sum(row.total for row, _ in result.items)
        self.card_values["valid"].configure(text=str(len(result.items)))
        self.card_values["invalid"].configure(text=str(len(result.invalid)),
                                              text_color="#B42318" if result.invalid else ("black", "white"))
        self.card_values["total"].configure(text=f"$ {fmt_money(total)}")
        self.card_values["obs"].configure(text=f"{result.mye_observation}  /  {result.cadi_observation}",
                                          font=ctk.CTkFont(size=13, weight="bold"))
        for w in result.warnings:
            self.log_msg(f"Aviso: {w}")
        self.log_msg(f"Consolidado {result.consolidado!r}: {len(result.items)} fila(s) válidas "
                     f"registradas como pendientes, {len(result.invalid)} con error.")

    # --------------------------------------------------------- browser processing
    def _on_mode(self, mode: str) -> None:
        real = mode == MODE_REAL
        self.mode_note.configure(text=MODE_NOTES[mode], text_color="#B42318" if real else "gray")
        self.process_btn.configure(text="Procesar (REAL)" if real else "Procesar (simulación)",
                                   fg_color="#B42318" if real else ctk.ThemeManager.theme["CTkButton"]["fg_color"])

    def process(self) -> None:
        result = self.last_result
        if not result or not result.items:
            return
        real = self.mode.get() == MODE_REAL
        # REAL mode runs unattended (user decision 2026-10-23): no per-row dialog. Each row
        # is validated automatically and any inconsistency skips that row only.
        auto = real
        if real:
            question = (f"MODO REAL para {len(result.items)} fila(s): se crearán documentos soporte en "
                        "MyE-invoice (se envían a la DIAN) y las facturas en CADI, que quedarán «En revisión». "
                        "No se pueden deshacer.\n\n"
                        "No se pedirá confirmación por fila. Cada fila se valida automáticamente "
                        "(duplicados en MyE y CADI, PDF borrador y final, totales, proveedor y formulario "
                        "de CADI); si algo no coincide, esa fila se detiene y se sigue con la siguiente.\n\n"
                        "¿Continuar?")
        else:
            question = (f"Se abrirá MyE-invoice y se llenará el documento soporte de {len(result.items)} "
                        "fila(s) hasta «Previsualizar», validando el PDF borrador.\n\n"
                        "Modo simulación: NO se registra ni se envía nada a la DIAN.\n\n¿Continuar?")
        if not messagebox.askyesno(APP_TITLE, question, icon="warning" if real else "question"):
            return
        self._set_busy(True)
        model = self.progress_panel.start([row.oc for row, _ in result.items], real)
        for oc, iid in self.row_iids.items():
            self.rows_tree.set(iid, "Avance", model.row_text(oc))
        bridge = UiBridge(
            log=lambda m: self.after(0, self.log_msg, m),
            row_update=lambda oc, st, detail, tag: self.after(0, self._update_row, oc, st, detail, tag),
            confirm=self._confirm_blocking,
            finished=lambda m: self.after(0, self._finished, m),
        )
        if real:
            self.worker.live_batch(result.items, result.consolidado, result.mye_observation,
                                   result.cadi_observation, result.corte_date, auto, bridge)
        else:
            self.worker.preview_batch(result.items, result.consolidado, result.mye_observation, bridge)

    def stop(self) -> None:
        self.worker.cancel.set()
        self.log_msg("Deteniendo después de la fila actual…")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.validate_btn.configure(state=state)
        self.process_btn.configure(state=state)
        self.stop_btn.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.pack(side="left", padx=8)
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _update_row(self, oc: str, status: str, detail: str, tag: str) -> None:
        iid = self.row_iids.get(oc)
        if iid:
            self.rows_tree.set(iid, "Estado", status)
            self.rows_tree.set(iid, "Avance", self.progress_panel.on_status(oc, status))
            self.rows_tree.set(iid, "Detalle", detail)
            self.rows_tree.item(iid, tags=(tag,) if tag else ())
        self.tracking.refresh()

    def _confirm_blocking(self, text: str, pdf: Path | None, label: str) -> bool:
        """Called from the browser thread: ask the user and wait for the answer."""
        done = threading.Event()
        answer = {"value": False}

        def on_answer(value: bool) -> None:
            answer["value"] = value
            done.set()

        self.after(0, lambda: ConfirmRegisterDialog(self, APP_TITLE, text, pdf, on_answer, label))
        done.wait()
        return answer["value"]

    def _finished(self, message: str) -> None:
        self._set_busy(False)
        self.progress_panel.finish()
        self.tracking.refresh()
        self.log_msg(message)
        messagebox.showinfo(APP_TITLE, message)

    def on_close(self) -> None:
        if self.worker.busy.is_set() and not messagebox.askyesno(
                APP_TITLE, "Hay un proceso en curso. ¿Cerrar de todas formas?"):
            return
        self.configure(cursor="watch")
        self.update()
        self.worker.shutdown(wait_seconds=15)  # let the browser close before exiting
        self.destroy()


def main() -> None:
    from app.main import setup_logging

    setup_logging()
    if not acquire_single_instance():
        messagebox.showwarning(APP_TITLE, "La app ya está abierta en otra ventana. "
                                          "Use esa ventana o ciérrela antes de abrir otra.")
        return
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()
