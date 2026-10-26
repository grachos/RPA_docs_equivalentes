"""Reusable widgets for the desktop UI."""
from __future__ import annotations

import customtkinter as ctk

from app.progress import BatchProgress


class ProgressPanel(ctk.CTkFrame):
    """Two determinate bars: the whole process (batch) and the current record (row)."""

    def __init__(self, master):
        super().__init__(master)
        self.grid_columnconfigure(1, weight=1)
        self.batch_bar, self.batch_text = self._line(0, "Proceso")
        self.row_bar, self.row_text = self._line(1, "Registro actual")
        self.model: BatchProgress | None = None

    def _line(self, row: int, title: str) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
        ctk.CTkLabel(self, text=title, width=120, anchor="w",
                     font=ctk.CTkFont(weight="bold")).grid(row=row, column=0, padx=(12, 6), pady=6, sticky="w")
        bar = ctk.CTkProgressBar(self, mode="determinate", height=14)
        bar.grid(row=row, column=1, sticky="ew", pady=6)
        bar.set(0)
        text = ctk.CTkLabel(self, text="—", width=380, anchor="w", text_color="gray")
        text.grid(row=row, column=2, padx=12, pady=6, sticky="w")
        return bar, text

    def start(self, ocs: list[str], real: bool) -> BatchProgress:
        self.model = BatchProgress(ocs, real)
        self.batch_bar.set(0)
        self.row_bar.set(0)
        self.batch_text.configure(text=self.model.summary(), text_color=("black", "white"))
        self.row_text.configure(text="Iniciando…", text_color=("black", "white"))
        return self.model

    def on_status(self, oc: str, status: str) -> str:
        """Update both bars for a row status; returns the text for the table's "Avance" column."""
        if self.model is None:
            return ""
        row = self.model.update(oc, status)
        self.batch_bar.set(self.model.fraction)
        self.batch_text.configure(text=self.model.summary())
        self.row_bar.set(self.model.row_fraction(oc))
        color = "#067647" if row.final and row.ok else "#B42318" if row.final else ("black", "white")
        self.row_text.configure(text=self.model.row_label(oc, status), text_color=color)
        return self.model.row_text(oc)

    def finish(self) -> None:
        if self.model is not None:
            self.batch_text.configure(text="Terminado · " + self.model.summary())
