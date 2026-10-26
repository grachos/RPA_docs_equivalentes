"""Tracking views: "Seguimiento" (only the consolidado in progress) and "Registro" (history).

User decision 2026-10-26: a new CORTE must not mix with the previous ones in Seguimiento; the
one being run is shown right away, and every corte accumulates in the Registro tab for review.
"""
from __future__ import annotations

import os
from tkinter import messagebox, ttk

import customtkinter as ctk

from app import config
from app.models import DocState, DocStatus
from app.report import write_report
from app.service import fmt_money
from app.state import StateStore

ALL_CONSOLIDADOS = "Todos"
STATUS_LABELS = {
    DocStatus.PENDING: "Pendiente",
    DocStatus.MYE_REGISTERED: "Registrado en MyE",
    DocStatus.DIAN_OK: "Aceptado DIAN",
    DocStatus.ZIP_DOWNLOADED: "ZIP descargado",
    DocStatus.CADI_CREATED: "Creado en CADI",
    DocStatus.CADI_REVIEWED: "En revisión CADI",
    DocStatus.DONE: "Terminado",
    DocStatus.FAILED: "Falló",
}
COLUMNS = [
    ("Consolidado", 120, "w"), ("Fecha corte", 90, "center"), ("Proveedor", 240, "w"),
    ("ID", 100, "w"), ("OC", 100, "center"), ("Total", 100, "e"), ("Estado", 170, "w"),
    ("DE", 90, "center"), ("CADI", 70, "center"), ("Error", 200, "w"),
]


def status_text(st: DocState) -> str:
    if st.status == DocStatus.FAILED:
        return f"Falló (último OK: {STATUS_LABELS[st.last_ok_status]})"
    return STATUS_LABELS[st.status]


def make_tree(parent, columns: list[tuple[str, int, str]]) -> ttk.Treeview:
    """Treeview + scrollbar. columns: (heading, width, anchor)."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="both", expand=True, pady=(8, 0))
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=12)
    for heading, width, anchor in columns:
        tree.heading(heading, text=heading)
        tree.column(heading, width=width, anchor=anchor, stretch=heading in ("Proveedor", "Detalle", "Error"))
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("invalid", foreground="#B42318", background="#FEF3F2")
    tree.tag_configure("failed", foreground="#B42318")
    tree.tag_configure("done", foreground="#067647")
    return tree


class TrackingView:
    """One tracking table. history=False: fixed to the consolidado in progress;
    history=True: a selector over every consolidado ever loaded (accumulated)."""

    def __init__(self, tab, title: str, history: bool) -> None:
        self.history = history
        self.consolidado: str | None = None  # the one in progress (Seguimiento only)
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(bar, text="Consolidado").pack(side="left", padx=(0, 8))
        if history:
            self.menu = ctk.CTkOptionMenu(bar, values=[ALL_CONSOLIDADOS], width=220,
                                          command=lambda _v: self.refresh())
            self.menu.pack(side="left")
        else:
            self.current_label = ctk.CTkLabel(bar, text="(ninguno en curso)",
                                              font=ctk.CTkFont(size=14, weight="bold"))
            self.current_label.pack(side="left")
        ctk.CTkButton(bar, text="Refrescar", width=100, command=self.refresh).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Exportar reporte", width=140, fg_color="gray40",
                      command=self.export_report).pack(side="left")
        self.summary_label = ctk.CTkLabel(bar, text="", text_color="gray")
        self.summary_label.pack(side="right")
        ctk.CTkLabel(tab, text=title, text_color="gray", anchor="w").pack(fill="x", pady=(4, 0))
        self.tree = make_tree(tab, COLUMNS)

    def show(self, consolidado: str | None) -> None:
        """Seguimiento: switch to the consolidado being loaded/run and show it at once."""
        self.consolidado = consolidado
        self.refresh()

    def selected(self) -> str | None:
        if self.history:
            value = self.menu.get()
            return None if value == ALL_CONSOLIDADOS else value
        return self.consolidado

    def refresh(self) -> None:
        with StateStore(config.STATE_DB) as store:
            names = store.consolidados()
            if self.history:
                self.menu.configure(values=[ALL_CONSOLIDADOS, *names])
                if self.menu.get() not in (ALL_CONSOLIDADOS, *names):
                    self.menu.set(ALL_CONSOLIDADOS)
            elif self.consolidado is None and names:
                self.consolidado = names[0]  # app just opened: the most recent one
            consolidado = self.selected()
            states = store.list(consolidado=consolidado) if (self.history or consolidado) else []
        if not self.history:
            self.current_label.configure(text=consolidado or "(ninguno en curso)")

        self.tree.delete(*self.tree.get_children())
        counts: dict[str, int] = {}
        for st in states:
            tag = "failed" if st.status == DocStatus.FAILED else "done" if st.status == DocStatus.DONE else ""
            self.tree.insert("", "end", tags=(tag,), values=(
                st.consolidado, st.corte, st.provider_name, st.provider_id, st.oc,
                fmt_money(st.total), status_text(st), st.de_number or "", st.cadi_id or "",
                st.error or ""))
            label = STATUS_LABELS[st.status]
            counts[label] = counts.get(label, 0) + 1
        self.summary_label.configure(
            text=" · ".join(f"{k}: {v}" for k, v in counts.items()) or "Sin registros")

    def export_report(self) -> None:
        consolidado = self.selected()
        with StateStore(config.STATE_DB) as store:
            states = store.list(consolidado=consolidado) if (self.history or consolidado) else []
        if not states:
            messagebox.showinfo("Reporte", "No hay registros para exportar.")
            return
        _, xlsx = write_report(states, config.REPORTS_DIR, consolidado or "todos")
        if messagebox.askyesno("Reporte", f"Reporte generado:\n{xlsx}\n\n¿Abrirlo ahora?"):
            os.startfile(xlsx)
