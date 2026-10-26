"""Map of known failures: where each one is fixed and what to do (shown in the app, report and PDF).

`advise(message)` matches the raw error text against CATALOG (first match wins) and `annotate`
appends the action to the message that is stored in state.db / shown in the table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SILOGTRAN, EXCEL, MYE, CADI, DIAN, APP, CONFIG = ("Silogtran", "Excel", "MyE-invoice", "CADI", "DIAN",
                                                  "App / reintento", "Configuración")


@dataclass(frozen=True)
class Entry:
    pattern: str   # regex searched (case-insensitive) in the error message
    where: str     # where it is fixed
    title: str     # short name of the problem
    action: str    # what to do before retrying
    retry: str = "Corrija y vuelva a procesar el consolidado: la fila se reintenta sola."


CATALOG: tuple[Entry, ...] = (
    # ---- data of the provider (master data: Silogtran) -------------------------------------------
    Entry(r"correo electr[oó]nico de contacto .* no es v[aá]lido|correo de contacto inv[aá]lido", SILOGTRAN,
          "Correo de contacto inválido",
          "Corrija el correo del tercero en Silogtran (típico: «.con» en vez de «.com», espacios, falta @)."),
    Entry(r"direcci[oó]n.*caracteres inv[aá]lidos", SILOGTRAN, "Dirección con caracteres inválidos",
          "El app ya quita signos como «:». Si aún aparece, deje la dirección solo con letras, números, "
          "espacios y # - . , / en Silogtran."),
    Entry(r"tel[eé]fono.*(inv[aá]lid|no es v[aá]lid)", SILOGTRAN, "Teléfono inválido",
          "Corrija el teléfono del tercero en Silogtran (solo números)."),
    Entry(r"Silogtran no tiene direcci[oó]n", SILOGTRAN, "Tercero sin dirección",
          "Complete la dirección de residencia del tercero en Silogtran."),
    Entry(r"c[oó]digo DANE|MyE no tiene (departamento|ciudad)", SILOGTRAN, "Ciudad/DANE faltante o inválido",
          "Corrija ciudad y código DANE del tercero en Silogtran."),
    Entry(r"nombres y apellidos separados|Tipo de persona de Silogtran", SILOGTRAN,
          "Nombres/tipo de persona incompletos",
          "Complete nombre1/apellido1 (o razón social) y el tipo de persona del tercero en Silogtran."),
    Entry(r"Documento inv[aá]lido|Varios terceros|devolvi[oó] el documento", SILOGTRAN,
          "Tercero ambiguo o documento inválido", "Revise que el tercero exista una sola vez en Silogtran."),
    Entry(r"No se pudieron aplicar los datos de Silogtran|El formulario no qued[oó] con los datos de Silogtran",
          SILOGTRAN, "MyE no aceptó los datos del tercero",
          "Mire el detalle (campo indicado) y corríjalo en Silogtran; si el campo ya está bien, reintente."),
    Entry(r"Tampoco est[aá] en Silogtran|no existe en MyE-invoice", SILOGTRAN, "Proveedor no existe",
          "Cree el tercero en Silogtran (el app lo crea en MyE con esos datos) o corrija el ID en el Excel."),
    Entry(r"Faltan datos obligatorios del proveedor en MyE", SILOGTRAN, "Proveedor de MyE incompleto",
          "Cree/complete el tercero en Silogtran para que el app llene MyE, o complete el proveedor en MyE."),
    Entry(r"API KEY|API de terceros|consultar el proveedor en Silogtran", CONFIG, "API de terceros",
          "Revise la API KEY en Configuración y la conexión (el servicio puede tardar ~1 min en despertar).",
          "Reintente la fila."),
    # ---- Excel -----------------------------------------------------------------------------------
    Entry(r"total (vac[ií]o|no num[eé]rico)|formato de total|Identificaci[oó]n inv[aá]lida|OC duplicad", EXCEL,
          "Dato del Excel inválido", "Corrija ID, OC (10 dígitos) o Total en el Excel y valide de nuevo."),
    Entry(r"Fecha inv[aá]lida", EXCEL, "Fecha del corte", "Escriba la fecha como DD/MM/AAAA."),
    # ---- duplicates / manual review --------------------------------------------------------------
    Entry(r"Posible duplicado|ya est[aá] registrada en CADI|Ya existe en CADI|ya tiene el documento", CADI,
          "Posible duplicado", "No se crea otro documento. Verifique en CADI/MyE; si es el mismo servicio, "
          "déjela así; si es otro, avise para procesarla a mano.", "No se reintenta sola."),
    Entry(r"Varias sugerencias", MYE, "Proveedor repetido en MyE",
          "El ID está dos veces en MyE (NIT y CC). El app usa el NIT; si persiste, revise el proveedor en MyE."),
    # ---- DIAN / MyE ------------------------------------------------------------------------------
    Entry(r"La DIAN no acept[oó]", DIAN, "Rechazo de la DIAN",
          "En MyE, menú Consultar Documentos, abra «(N Observaciones)» del DE y lea el motivo; corrija el dato "
          "de origen (casi siempre el tercero en Silogtran) y cree un documento nuevo.",
          "Requiere acción manual: el DE rechazado no se reenvía solo."),
    Entry(r"sigue en .*despu[eé]s de", DIAN, "DIAN aún no responde",
          "Espere a que el monitor de MyE envíe a la DIAN y reintente; no cree otro documento.",
          "Reintente: continúa desde «Registrado en MyE»."),
    Entry(r"MyE rechaz[oó] la previsualizaci[oó]n", MYE, "MyE rechazó los datos",
          "Lea el motivo entre paréntesis (dirección, correo, NIT…) y corrija el dato de origen en Silogtran."),
    Entry(r"vista previa no coincide|PDF final .* no coincide|Totales en pantalla", MYE,
          "El PDF no coincide con el Excel", "No se registró nada. Revise OC, total y proveedor del Excel; "
          "reintente. Si repite, avise."),
    Entry(r"formulario cambi[oó] de proveedor|No se pudo corregir Nombres|Tipo de documento qued[oó]", MYE,
          "El formulario de MyE cambió solo", "Reintente la fila (MyE autocompletó otro proveedor)."),
    Entry(r"inicio de sesi[oó]n|sesi[oó]n .* expir", APP, "Sesión de MyE/CADI",
          "Inicie sesión en el navegador abierto (suma de MyE; Cloudflare y código del correo en CADI) y "
          "reintente."),
    # ---- CADI ------------------------------------------------------------------------------------
    Entry(r"CADI|factura \d+", CADI, "Error en CADI",
          "Revise la factura en CADI (¿ya existe? ¿estado?). Si no se creó, reintente; si quedó a medias, "
          "el app continúa desde el último paso correcto."),
    # ---- unexpected ------------------------------------------------------------------------------
    Entry(r"inesperado|Timeout", APP, "Error inesperado / tiempo agotado",
          "Revise la carpeta debug (captura y HTML) y logs/app.log; reintente. Si MyE mostró un aviso, "
          "corrija el dato indicado."),
)


def advise(message: str) -> Entry | None:
    for entry in CATALOG:
        if re.search(entry.pattern, message or "", re.IGNORECASE):
            return entry
    return None


def annotate(message: str) -> str:
    """Message + «→ [dónde] qué hacer» when the failure is a known one."""
    if "→ [" in message:
        return message
    entry = advise(message)
    return f"{message}  → [{entry.where}] {entry.action}" if entry else message
