"""Settings tab of the desktop UI: credentials (.env) and business parameters."""
from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from app import config
from app.ui_dialogs import SecretDialog

APP_TITLE = "Documento Soporte Automático"


class SettingsTabMixin:
    """Mixed into the App window; needs `self.log_msg`."""

    def _build_settings_tab(self, tab) -> None:
        creds = ctk.CTkFrame(tab)
        creds.pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(creds, text=f"Credenciales (se guardan en {config.ENV_PATH.name}, en la carpeta de la app)",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, padx=12,
                                                         pady=(10, 4), sticky="w")
        labels = {"MYE_USER": "Usuario MyE-invoice", "MYE_PASSWORD": "Contraseña MyE-invoice",
                  "CADI_DOCUMENT": "Documento CADI", "CADI_PASSWORD": "Contraseña CADI",
                  "TERCEROS_API_KEY": "API KEY de terceros"}
        self.cred_status: dict[str, ctk.CTkLabel] = {}
        for i, name in enumerate(config.CREDENTIAL_NAMES, start=1):
            ctk.CTkLabel(creds, text=labels[name], width=200, anchor="w").grid(row=i, column=0, padx=12, pady=4)
            status = ctk.CTkLabel(creds, text="", width=160, anchor="w")
            status.grid(row=i, column=1)
            self.cred_status[name] = status
            ctk.CTkButton(creds, text="Guardar…", width=100,
                          command=lambda n=name: SecretDialog(self, labels[n],
                                                              lambda v, n=n: self.save_credential(n, v))
                          ).grid(row=i, column=2, padx=12, pady=4)
        ctk.CTkLabel(creds, text="").grid(row=len(config.CREDENTIAL_NAMES) + 1, column=0)
        self.refresh_credentials()

        consts = ctk.CTkFrame(tab)
        consts.pack(fill="x")
        ctk.CTkLabel(consts, text="Parámetros de negocio (app/config.py)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=12, pady=(10, 4))
        for label, value in (
            ("Concepto MyE", f"{config.MYE_CONCEPT_CODE} - {config.MYE_CONCEPT_DESCRIPTION}"),
            ("Medio de pago", config.MYE_PAYMENT_MEANS),
            ("Empresa CADI", config.CADI_COMPANY),
            ("Tipo CADI", config.CADI_INVOICE_TYPE),
            ("Centro", config.CADI_CENTER),
            ("Revisado por", config.CADI_REVIEWER),
            ("Plantilla observación MyE", config.MYE_OBSERVATION_TEMPLATE),
            ("Plantilla observación CADI", config.CADI_OBSERVATION_TEMPLATE),
        ):
            ctk.CTkLabel(consts, text=f"{label}:  {value}", anchor="w").pack(anchor="w", padx=24)
        ctk.CTkLabel(consts, text="").pack()

    def refresh_credentials(self) -> None:
        for name, label in self.cred_status.items():
            try:
                config.get_credential(name)
                label.configure(text="✔ Configurada", text_color="#067647")
            except RuntimeError:
                label.configure(text="✖ No configurada", text_color="#B42318")

    def save_credential(self, name: str, value: str) -> None:
        try:
            path = config.save_credential(name, value)
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.refresh_credentials()
        self.log_msg(f"Credencial {name} guardada en {path}.")

