"""Modal dialogs used by the desktop UI."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import customtkinter as ctk


class SecretDialog(ctk.CTkToplevel):
    """Small modal to type a secret without echoing it."""

    def __init__(self, master, name: str, on_save: Callable[[str], None]):
        super().__init__(master)
        self.title(f"Guardar {name}")
        self.geometry("380x150")
        self.resizable(False, False)
        self.transient(master)
        self.on_save = on_save
        ctk.CTkLabel(self, text=f"Valor para {name}:").pack(padx=20, pady=(18, 6), anchor="w")
        self.entry = ctk.CTkEntry(self, show="•", width=340)
        self.entry.pack(padx=20)
        self.entry.bind("<Return>", lambda _e: self._save())
        ctk.CTkButton(self, text="Guardar", command=self._save).pack(pady=14)
        self.after(100, self._focus)

    def _focus(self) -> None:
        self.grab_set()
        self.entry.focus_set()

    def _save(self) -> None:
        value = self.entry.get()
        self.destroy()
        if value:
            self.on_save(value)


class ConfirmRegisterDialog(ctk.CTkToplevel):
    """Per-row confirmation before an IRREVERSIBLE action (MyE "Confirmar", CADI "Cargar").

    Calls on_answer(True) only when the user presses the confirm button. Closing
    the window or pressing "No" answers False.
    """

    def __init__(self, master, title: str, text: str, pdf: Path | None,
                 on_answer: Callable[[bool], None], confirm_label: str = "Registrar y enviar a la DIAN"):
        super().__init__(master)
        self.title(title)
        self.geometry("560x430")
        self.resizable(False, False)
        self.transient(master)
        self._on_answer = on_answer
        self._answered = False
        self.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))

        ctk.CTkLabel(self, text="Confirmar acción irreversible", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#B42318").pack(anchor="w", padx=20, pady=(16, 6))
        box = ctk.CTkTextbox(self, height=260, wrap="word")
        box.insert("end", text)
        box.configure(state="disabled")
        box.pack(fill="x", padx=20)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(buttons, text="No, omitir esta fila", fg_color="gray40", width=150,
                      command=lambda: self._answer(False)).pack(side="left")
        if pdf and pdf.exists():
            ctk.CTkButton(buttons, text="Ver PDF", width=110,
                          command=lambda: os.startfile(pdf)).pack(side="left", padx=10)
        ctk.CTkButton(buttons, text=confirm_label, fg_color="#067647",
                      hover_color="#05603A", width=240,
                      command=lambda: self._answer(True)).pack(side="right")
        self.after(100, self.grab_set)

    def _answer(self, value: bool) -> None:
        if self._answered:
            return
        self._answered = True
        self.grab_release()
        self.destroy()
        self._on_answer(value)
