"""Builds docs/Instructivo_Documento_Soporte.pdf (user guide of the desktop app)."""
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

IMG = Path(__file__).resolve().parent.parent / "docs" / "img"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.error_guide import CATALOG  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "Instructivo_Documento_Soporte.pdf"
BLUE = colors.HexColor("#1F4E79")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], textColor=BLUE, fontSize=20, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=BLUE, fontSize=14, spaceBefore=12, spaceAfter=6)
P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=10.5, leading=15, spaceAfter=6)
NOTE = ParagraphStyle("NOTE", parent=P, backColor=colors.HexColor("#FFF4CE"), borderPadding=6,
                      spaceBefore=6, spaceAfter=10)
CELL = ParagraphStyle("CELL", parent=P, fontSize=9.5, leading=12, spaceAfter=0)


def bullets(items):
    return ListFlowable([ListItem(Paragraph(i, P), leftIndent=14) for i in items], bulletType="bullet",
                        start="•", leftIndent=14)


def steps(items):
    return ListFlowable([ListItem(Paragraph(i, P), leftIndent=18) for i in items], bulletType="1",
                        leftIndent=18)


def shot(name, caption, width=16.5 * cm):
    from PIL import Image as PILImage
    with PILImage.open(IMG / f"{name}.png") as im:
        ratio = im.size[1] / im.size[0]
    w = width
    return KeepTogether([Image(str(IMG / f"{name}.png"), width=w, height=w * ratio),
                         Paragraph(f"<i>{caption}</i>", ParagraphStyle("cap", parent=P, fontSize=9,
                                                                       textColor=colors.grey))])


def table(rows, widths):
    data = [[Paragraph(str(c), CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def footer(canvas, doc):
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(2 * cm, 1.2 * cm, "Documento Soporte Automático · Instructivo de usuario")
    canvas.drawRightString(letter[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")


def build():
    s = []
    s += [Spacer(1, 3 * cm), Paragraph("Documento Soporte Automático", H1),
          Paragraph("Instructivo de usuario", ParagraphStyle("sub", parent=H2, fontSize=16)),
          Paragraph("ELOGIA SOLUCIONES LOGÍSTICAS · MyE-invoice + CADI", P), Spacer(1, 1 * cm),
          Paragraph("Este instructivo explica cómo usar el app para crear, por cada fila de un Excel de órdenes "
                    "de compra (un corte), el documento soporte en MyE-invoice, enviarlo a la DIAN, descargar su "
                    "ZIP (PDF + XML) y registrar la factura en CADI dejándola «En revisión».", P),
          Paragraph("Elaborado el 26/10/2026.", P), PageBreak()]

    s += [Paragraph("1. ¿Qué hace el app?", H2),
          steps(["Lee el Excel del corte y valida cada fila (ID, orden de compra, total).",
                 "Revisa duplicados por orden de compra (OC) para no crear dos veces el mismo servicio.",
                 "Consulta al proveedor en Silogtran (API de terceros) y completa/corrige sus datos en MyE-invoice.",
                 "Crea el documento soporte en MyE-invoice y espera «Procesado Correctamente» de la DIAN.",
                 "Descarga el ZIP y extrae el PDF y el XML en la carpeta <i>downloads</i>.",
                 "Crea la factura en CADI (FAC ELEC - TERCERIZACION, DA25, PDF adjunto) y la pasa a «En revisión».",
                 "Genera un reporte en Excel/CSV con el resultado de cada fila."]),
          Paragraph("2. Antes de empezar", H2),
          bullets(["Abra el app con doble clic en <b>Iniciar app.bat</b> (carpeta del proyecto).",
                   "Tenga a mano el Excel del corte con una sola hoja y los encabezados exactos: "
                   "<b>ID POSEEDOR · Nombre del proveedor · orden de compra · Total</b>.",
                   "En <b>Configuración</b> deben estar guardadas las credenciales de MyE-invoice y CADI y la "
                   "clave de la API de terceros.",
                   "Para el login necesita estar presente: MyE pide una suma (captcha), CADI pide Cloudflare y un "
                   "código por correo. Esto <b>no se automatiza</b>: lo escribe usted y el app continúa."]),
          Paragraph("3. La ventana principal", H2),
          Paragraph("La ventana tiene cuatro pestañas:", P),
          table([["Pestaña", "Para qué sirve"],
                 ["Nuevo consolidado", "Cargar el Excel, validar y procesar."],
                 ["Seguimiento", "Muestra solo el corte en curso, fila por fila."],
                 ["Registro", "Historial acumulado de todos los cortes, con selector."],
                 ["Configuración", "Guardar credenciales y ver parámetros de negocio."]],
                [4.5 * cm, 12.5 * cm]), PageBreak()]

    s += [Paragraph("4. Pestaña «Nuevo consolidado»", H2),
          shot("nuevo", "Figura 1. Pestaña «Nuevo consolidado»."),
          table([["Campo / botón", "Qué hacer"],
                 ["Archivo Excel", "Pulse <b>Examinar…</b> y elija el Excel del corte."],
                 ["Descargar plantilla", "Genera un Excel vacío con las columnas correctas."],
                 ["Nombre del consolidado", "Ej.: <i>CORTE 104</i>. Identifica el corte en Seguimiento y Registro."],
                 ["Fecha del corte", "Formato DD/MM/AAAA. Se usa en la observación de MyE y CADI "
                  "(ej.: «CORTE 103 09 octubre 2026»)."],
                 ["Modo", "<b>Simulación</b>: llena y previsualiza, no registra nada. <b>Real</b>: registra de verdad."],
                 ["Validar Excel", "Revisa el archivo y muestra filas válidas, filas con error (en rojo, con el "
                  "motivo), el total a pagar y las observaciones."],
                 ["Procesar", "Ejecuta el lote (el botón dice «Procesar (simulación)» o «Procesar (REAL)»)."],
                 ["Detener", "Interrumpe el lote; lo ya hecho queda guardado."]],
                [4.5 * cm, 12.5 * cm]),
          Paragraph("Paso a paso para un corte nuevo", H2),
          steps(["Pulse <b>Examinar…</b> y seleccione el Excel.",
                 "Escriba el <b>Nombre del consolidado</b> y la <b>Fecha del corte</b>.",
                 "Pulse <b>Validar Excel</b> y revise filas válidas, errores y total.",
                 "Se recomienda probar primero en <b>Simulación</b> (no crea nada).",
                 "Cambie el modo a <b>Real</b> y pulse <b>Procesar (REAL)</b>. Confirme el mensaje inicial "
                 "(es la única confirmación del lote).",
                 "Al pedirlo, inicie sesión: en MyE escriba la suma del captcha y pulse Enter; en CADI resuelva "
                 "Cloudflare, pulse INICIAR SESIÓN y escriba el código del correo. Deje el navegador abierto.",
                 "Mire el avance en las barras «Proceso» y «Registro actual» y en la pestaña Seguimiento."]),
          shot("validado", "Figura 2. Excel validado: 12 filas válidas, 0 con error, total a pagar y observación "
                           "(nombres e IDs difuminados)."),
          Paragraph("Seguir el avance", H2),
          Paragraph("Mientras procesa, el botón <b>Detener</b> queda activo y aparece una barrita de actividad. "
                    "<b>Proceso</b> muestra el porcentaje del lote y cuántas filas terminaron (OK, con error u "
                    "omitidas); <b>Registro actual</b> muestra la OC en curso y su paso (1/5 Registro MyE · 2/5 "
                    "Espera DIAN · 3/5 Descarga ZIP · 4/5 Factura CADI · 5/5 En revisión). En la tabla, la "
                    "columna <b>Avance</b> indica el paso de cada fila (0/5, 2/5… y una marca de terminada o de error) y <b>Detalle</b> el "
                    "resultado. El app primero registra todas las filas en MyE y después, fila por fila, espera "
                    "la DIAN y pasa a CADI.", P),
          shot("progreso", "Figura 3. Lote en proceso: 29 filas, barras de avance y estado por fila "
                           "(nombres e IDs difuminados)."),
          Paragraph("Cuando una fila termina bien, su texto se pone en <b>verde</b>: el Estado dice "
                    "«Terminada», Avance muestra la marca de terminada y Detalle trae el número del documento "
                    "soporte, el ID de la factura en CADI y «En revisión» (ej.: DE252315 · CADI 234995 · En "
                    "revisión). Las filas aún en curso muestran su paso (3/5, «buscando factura»…) y las "
                    "fallidas aparecen en rojo con el motivo.", P),
          shot("terminadas", "Figura 4. Filas terminadas en verde (4 OK) mientras otra sigue en el paso 4/5 "
                             "(nombres e IDs difuminados)."),
          Paragraph("Al terminar, el app muestra un resumen: cuántas filas quedaron terminadas (MyE + DIAN + CADI "
                    "en revisión), cuántas con error y cuántas omitidas por validación, el tiempo total, el "
                    "promedio de cada etapa y el tiempo de cada OC. También indica la ruta del reporte en Excel "
                    "(carpeta <i>reports</i>). Las filas con error se corrigen y se reprocesan en otra corrida.", P),
          shot("fin_lote", "Figura 5. Resumen al terminar el lote (27 terminadas, 2 con error)."),
          Paragraph("Importante: la fecha del corte se toma siempre de la que usted escribe en el app.", NOTE),
          Paragraph("Modo Real desatendido", H2),
          Paragraph("En modo Real no se pregunta por cada fila. Cada fila se valida sola antes de cada acción "
                    "irreversible: si algo no coincide, esa fila se marca con el motivo y el lote sigue con la "
                    "siguiente. Solo se detiene todo si no se puede confirmar el resultado de un «Confirmar» de "
                    "MyE o un «Cargar» de CADI.", P),
          Paragraph("Inicio de sesión en MyE-invoice y CADI", H2),
          Paragraph("Al iniciar el lote real el app abre un navegador y llena los datos de acceso guardados en "
                    "Configuración. Usted solo hace lo que el app no puede automatizar:", P),
          bullets(["<b>MyE-invoice:</b> el app escribe usuario y contraseña. Usted únicamente escribe el "
                   "<b>resultado de la suma</b> (captcha «¡Comprueba que eres humano!», p. ej. 7 + 6) y pulsa Enter. "
                   "El app detecta la sesión sola.",
                   "<b>CADI:</b> el app escribe documento y contraseña. Usted resuelve la verificación de "
                   "Cloudflare y pulsa <b>INICIAR SESIÓN</b>. <b>A veces</b> CADI pide además un <b>código que llega "
                   "al correo registrado</b>: escríbalo en la pantalla de CADI.",
                   "Deje el navegador abierto: el inicio de sesión se hace una vez por sesión del app."]),
          Table([[Image(str(IMG / "login_mye.png"), width=8 * cm, height=8 * cm * 739 / 811),
                  Image(str(IMG / "login_cadi.png"), width=8 * cm, height=8 * cm * 835 / 846)]],
                colWidths=[8.5 * cm, 8.5 * cm]),
          Paragraph("<i>Figura 6. Pantallas de acceso de MyE-invoice (izquierda, con la suma del captcha) y de CADI "
                    "(derecha). En el app los campos de usuario y contraseña se llenan solos.</i>",
                    ParagraphStyle("cap2", parent=P, fontSize=9, textColor=colors.grey)),
          PageBreak()]


    s += [Paragraph("5. Pestañas «Seguimiento» y «Registro»", H2),
          Paragraph("<b>Seguimiento</b> muestra únicamente el consolidado en curso. Al abrir el app aparece el más "
                    "reciente; al validar un corte nuevo cambia a ese corte y se actualiza fila a fila mientras se "
                    "procesa. <b>Registro</b> acumula todos los cortes: use el selector («Todos» o un corte "
                    "específico) para revisar cortes anteriores.", P),
          Paragraph("Botones: <b>Refrescar</b> recarga la tabla y <b>Exportar reporte</b> genera el Excel del corte "
                    "mostrado (en Registro, del corte elegido o de todos).", P),
          shot("seguimiento", "Figura 7. Seguimiento: corte en curso (nombres e IDs difuminados)."),
          shot("registro", "Figura 8. Registro: historial con selector de consolidado."),
          Paragraph("Estados de una fila", H2),
          table([["Estado", "Significado"],
                 ["Pendiente", "Validada, aún sin procesar."],
                 ["Registrado en MyE", "Documento soporte creado; falta la respuesta de la DIAN."],
                 ["Aceptado DIAN", "La DIAN lo procesó correctamente."],
                 ["ZIP descargado", "PDF y XML guardados en <i>downloads</i>."],
                 ["Creado en CADI", "Factura creada en CADI."],
                 ["En revisión CADI", "Factura pasada a «En revisión»."],
                 ["Terminado", "Fila completa y verificada."],
                 ["Falló (último OK: …)", "Se detuvo; la columna Error indica el motivo y el último paso correcto."]],
                [4.5 * cm, 12.5 * cm]),
          Paragraph("Columnas: Consolidado, Fecha corte, Proveedor, ID, OC, Total, Estado, DE (número del documento "
                    "soporte), CADI (ID de la factura) y Error.", P),
          Paragraph("6. Pestaña «Configuración»", H2),
          shot("configuracion", "Figura 9. Configuración: credenciales y parámetros de negocio."),
          bullets(["Cada credencial (MyE usuario/contraseña, CADI documento/contraseña, clave de la API de "
                   "terceros) se guarda con <b>Guardar…</b>; no se muestran en pantalla y quedan en el archivo "
                   "<i>.env</i>.",
                   "También se ven los parámetros de negocio (concepto, empresa DA25, tipo de factura, centro "
                   "OPERATIVO, revisor)."]),
          Paragraph("El archivo .env está en texto plano. Si la carpeta está en OneDrive se sincroniza a la nube: "
                    "no la comparta.", NOTE), PageBreak()]

    s += [Paragraph("7. Reglas que aplica el app", H2),
          bullets(["<b>Duplicados por OC:</b> si CADI ya tiene una factura con esa OC, no crea nada. Si hay un "
                   "documento de MyE del mismo proveedor y valor, compara la OC de su factura: otra OC = otro "
                   "servicio (se procesa); misma OC = duplicado; sin factura o sin OC = se detiene para revisión "
                   "manual.",
                   "<b>Silogtran manda sobre el proveedor:</b> el tipo de documento queda siempre NIT con su "
                   "dígito de verificación (la DIAN rechaza un documento soporte con el proveedor como cédula), "
                   "más tipo de persona, nombres o razón social, direcciones, teléfono y correos.",
                   "<b>Proveedor nuevo:</b> si no existe en MyE se crea con los datos de Silogtran; si tampoco "
                   "está en Silogtran, la fila se detiene.",
                   "<b>El proveedor lo decide el ID</b>; si el nombre del Excel difiere, solo es un aviso.",
                   "<b>Reanudación segura:</b> si algo falla después de registrar, al reintentar continúa desde el "
                   "último paso correcto y nunca registra dos veces."]),
          Paragraph("8. Resultados y archivos", H2),
          table([["Qué", "Dónde"],
                 ["PDF y XML de cada documento", "downloads\\&lt;consolidado&gt;\\&lt;ID&gt;_&lt;NOMBRE&gt;\\"],
                 ["Reporte del lote (xlsx y csv)", "reports\\report_&lt;consolidado&gt;_&lt;fecha&gt;"],
                 ["Registro de actividad y tiempos", "logs\\app.log (líneas TIMING)"],
                 ["Estado de todas las filas", "state.db (no editar a mano)"]],
                [6 * cm, 11 * cm]),
          Paragraph("9. Problemas frecuentes", H2),
          table([["Mensaje o situación", "Qué hacer"],
                 ["Encabezados no coinciden", "Use <b>Descargar plantilla</b> y copie sus datos."],
                 ["Fila en rojo al validar", "Lea el motivo (ID no numérico, OC sin 10 dígitos, total en cero, OC "
                  "repetida) y corrija el Excel."],
                 ["La DIAN no aceptó el DE…", "Entre a MyE, menú Consultar Documentos, y abra «(N Observaciones)» para "
                  "ver el motivo. No reintente a ciegas: avise a soporte."],
                 ["Proveedor no está en Silogtran", "Créelo en Silogtran y reintente la fila."],
                 ["Error de la API de terceros", "Revise la clave en Configuración y la conexión; reintente."],
                 ["Fila «Falló»", "Corrija la causa y relance el lote: continúa desde el último paso correcto."],
                 ["Captcha o código de CADI", "Se escriben a mano; el app espera."]],
                [5.5 * cm, 11.5 * cm]),
          Paragraph("Avisos al validar", H2),
          Paragraph("Si falta la fecha del corte (o tiene otro formato), el app avisa «Fecha inválida» y no "
                    "valida: escríbala como DD/MM/AAAA.", P),
          shot("error_fecha", "Figura 10. Aviso por fecha del corte vacía o inválida.", 12 * cm),
          Paragraph("Si pulsa Validar sin elegir el Excel, aparece «Seleccione el archivo Excel»: use "
                    "<b>Examinar…</b> primero.", P),
          shot("error_archivo", "Figura 11. Aviso por falta de archivo Excel.", 12 * cm),
          PageBreak(),
          Paragraph("10. Mapa de errores: dónde se corrige cada uno", H2),
          Paragraph("Cuando una fila falla, la columna <b>Error</b> (Seguimiento) y el reporte muestran el "
                    "motivo seguido de «[dónde] qué hacer». La regla general: <b>los datos del proveedor se "
                    "corrigen en Silogtran</b> (el app los vuelve a leer en el siguiente intento), los datos de "
                    "la orden en el Excel, y lo demás según la tabla. El lote nunca se detiene por una fila con "
                    "error: pasa a la siguiente, y al final se reintentan las que fallaron.", P),
          table([["Problema", "Dónde", "Qué hacer"]]
                + [[e.title, e.where, e.action + " <i>" + e.retry + "</i>"] for e in CATALOG],
                [4 * cm, 2.6 * cm, 10.4 * cm]),
          Paragraph("Ejemplo real: si MyE dice «El correo electrónico de contacto persona@ejemplo.con no es válido», "
                    "se corrige el correo en Silogtran y se reprocesa el consolidado; las filas ya terminadas "
                    "se omiten y la fila con error se reintenta desde el principio.", NOTE),
          Paragraph("Lo que el app nunca hace: eliminar, anular ni editar documentos, ni pulsar «Enviar a "
                    "incidencias». Cualquier ajuste de ese tipo se hace manualmente.", NOTE)]

    OUT.parent.mkdir(exist_ok=True)
    SimpleDocTemplate(str(OUT), pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                      bottomMargin=2 * cm, title="Instructivo - Documento Soporte Automático").build(
        s, onFirstPage=footer, onLaterPages=footer)
    print(OUT)


if __name__ == "__main__":
    build()
