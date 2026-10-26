# Documento Soporte Automático (MyE-invoice + CADI)

Script local de Windows que, a partir del Excel de órdenes de compra de un corte,
crea el documento soporte en MyE-invoice, descarga el ZIP (PDF + XML) y registra
la factura en CADI con el PDF adjunto, dejándola "En revisión".

## Estado de las fases

| Fase | Contenido | Estado |
|---|---|---|
| 1 | App de escritorio, lector de Excel, estado SQLite, CLI, reporte | **Listo** |
| 2 | MyE-invoice hasta Previsualizar (simulación) | **Listo** (probado en vivo con 1 fila) |
| 3 | Registro / DIAN / descarga del ZIP (modo Real) | **Listo** (probado en real: DE252265, 23/10/2026) |
| 4 | CADI hasta antes de «Cargar» (simulación) | **Listo** (probado en vivo con DE252265) |
| 5 | CADI real: «Cargar» + «En revisión» + reporte | **Listo** (probado en real: CADI 234217, 23/10/2026) |
| 6 | Prueba de punta a punta con varias filas + optimización de tiempos | **Listo** (CORTE 103: 6 filas OK) |

Con **Procesar (simulación)** la app abre MyE-invoice, llena el documento soporte de cada
fila y pulsa **Previsualizar**; descarga el PDF borrador, lo valida (proveedor, NIT, concepto,
total, observación, OC) y cierra la vista previa **sin confirmar**. No se registra nada ni se
envía a la DIAN. Los borradores quedan en `downloads/<consolidado>/<ID>_<NOMBRE>/`.

Con el modo **Real** y **Procesar (REAL)**, por cada fila la app:

1. **Revisa duplicados por OC** (no por proveedor y monto): si CADI ya tiene una factura con esa
   OC, no crea nada. Los documentos de MyE del mismo proveedor y valor (últimos 60 días) se revisan
   con la OC de su factura en CADI: otra OC = otro servicio (se procesa, MyE asigna un número DE
   nuevo); misma OC = duplicado; sin factura en CADI o sin OC = se detiene para revisión manual.
2. Llena el formulario, previsualiza y valida el PDF borrador.
3. Si la vista previa pasó todas las validaciones, continúa **sin preguntar** (el modo Real no
   muestra ventanas por fila; solo se confirma una vez al iniciar el lote).
4. Pulsa **Confirmar** en MyE, captura el número DE y espera «Procesado Correctamente».
5. Descarga el ZIP, extrae `DE…pdf` y `DE…xml` en `downloads/<consolidado>/<ID>_<NOMBRE>/` y los valida.

Si algo falla después de registrar, el estado guarda el último paso OK y al reintentar continúa
desde ahí (nunca vuelve a registrar). Si no se puede leer el número DE, el lote se detiene para
verificar a mano.

### Modo Real desatendido

El modo Real no pide confirmación por fila. Cada fila se valida automáticamente antes de
cada acción irreversible; **si algo no coincide, esa fila se marca con el motivo y el lote
sigue con la siguiente** (no se crea nada para esa fila). Solo se detiene todo el lote si
no se puede confirmar el resultado de un «Confirmar» de MyE o un «Cargar» de CADI.

### Orden de trabajo (optimizado)

1. **Al inicio** se piden los inicios de sesión de MyE (suma) y CADI (Cloudflare + código); después
   el lote sigue solo.
2. **Búsqueda de duplicados una sola vez** (con 2 o más filas pendientes): lee todos los documentos
   soporte de los últimos 60 días (todas las páginas) y verifica que el total leído coincida con el
   que informa MyE; si no coincide, vuelve a buscar por fila. Los documentos creados en el lote se
   agregan a esa lista, así una fila repetida (mismo proveedor y valor) también se detecta.
3. **Pasada 1:** registra en MyE todas las filas pendientes, una tras otra (el formulario carga en
   una pestaña mientras la consulta de documentos usa otra).
4. **Pasada 2:** por cada fila registrada espera la DIAN (normalmente ya procesó mientras se
   registraban las demás), descarga el ZIP y hace CADI (Cargar + En revisión).

El mensaje final y `logs/app.log` (líneas `TIMING`) muestran el tiempo total y el promedio por etapa.

**Barras de avance:** «Proceso» muestra el % del lote y cuántas filas terminaron; «Registro actual»
muestra la fila en curso y su paso (Registro MyE → Espera DIAN → Descarga ZIP → Factura CADI →
En revisión). La columna «Avance» de la tabla indica el paso de cada fila (`2/5`, ✔ o ✖).

### Modo Real completo

Después de descargar el ZIP, en modo **Real** la app sigue en CADI:

1. Busca la factura por número DE. Si ya existe con el mismo NIT, **la usa** (no crea otra);
   si existe con otro NIT, se detiene.
2. Llena «Crear factura» y lee de vuelta cada campo; si todo coincide, continúa sin preguntar.
3. Pulsa **Cargar**, toma el ID de la factura (`details?i=…`).
4. Pulsa **En revisión** y llena Número de orden (OC), Centro **OPERATIVO**, Revisado por
   el revisor configurado en `CADI_REVIEWER` (`.env`) y Observaciones; pulsa **Crear**.
5. Verifica en el detalle: Estado «En revisión», Centro, Número de orden y Valor total. Fila = **DONE**.

Al terminar el lote se genera `reports/report_<consolidado>_<fecha>.xlsx/.csv`. Cada paso queda en
el estado: si algo falla, al reintentar se continúa desde el último paso OK sin duplicar.
Botones de CADI que la app nunca usa: «Anular factura», «Enviar a Incidencias», «Editar».

### CADI (simulación)

En **Simulación**, las filas con el ZIP ya descargado se procesan en CADI (en una segunda
pestaña del mismo navegador): la app busca en Facturas si ya existe una factura con ese número
DE (si existe, no crea otra), llena **Crear factura** — Factura · DE… · NIT · DA25 · OC · fecha de
emisión tomada del PDF · FAC ELEC - TERCERIZACION · IVA 0 · valores · CP/Importación/Bolsa = No ·
PDF adjunto —, lee de vuelta cada campo y guarda una captura `cadi_formulario_DE….png` junto al PDF.
**No pulsa «Cargar».** CADI calcula solo «Valor total»; la app lo verifica.

Login de CADI: la app llena documento y contraseña; usted resuelve la verificación de Cloudflare,
pulsa INICIAR SESIÓN y escribe el código del correo.

### Particularidades de MyE-invoice confirmadas en vivo (22–23/10/2026)

- Tipo de documento: `Documento Soporte - DE25 - 8759` (existe también `Factura - DE25 - 8951`: no usarla).
- Representación gráfica: `DOCUMENTO SOPORTE DS - factura_myenvoice_DocumentoSoporte.jrxml` (existe otra `_fe1`).
- **Silogtran manda sobre los datos del proveedor (API de terceros).** Por cada fila la app consulta
  `GET {TERCEROS_API_URL}/api/terceros/{id}` (+ `/raw` para nombres separados; header `x-api-key` =
  `TERCEROS_API_KEY`, guardada en Configuración). Al iniciar el lote se consultan en segundo plano
  todos los proveedores (la primera consulta puede tardar ~1 min si el servicio estaba dormido).
  En el formulario de MyE el tipo de documento queda **siempre NIT con su DV** (la DIAN rechaza un
  documento soporte con el proveedor como CC: regla DSAJ25a). Se corrige con Silogtran: tipo de persona, nombres/apellidos (o razón social), dirección
  de residencia y fiscal (departamento/ciudad por código DANE: `11`/`11001`, `05`/`05360`…),
  email de contacto, teléfono, **email de entrega y confirmación fijos**
  (`informacionalproveedor@elogia.com.co`), datos de contacto vacíos y obligación **solo R-99-PN**.
  Antes de previsualizar se relee todo; si algo no quedó igual, la fila se detiene.
- **Proveedor que no existe en MyE:** se escribe el ID y se diligencia completo con Silogtran
  (MyE lo guarda al registrar el documento). Si tampoco está en Silogtran, la fila se detiene.
  Si está en MyE pero no en Silogtran, se usan los datos de MyE (y se exige la dirección completa).
  Si la API falla (clave, red), la fila se detiene con el mensaje.
- Si MyE muestra el mismo ID dos veces (`NIT …` y `CC …`), se usa la de NIT.
- **El proveedor lo decide el ID.** El nombre del Excel puede venir incompleto o distinto: si la
  identificación coincide, se usa el proveedor de MyE (la diferencia de nombre solo es un aviso).
- **Corrección de Nombres:** MyE pega apellidos en «Nombres». Se quitan los apellidos completos y
  también el primer apellido que quede al final (`LUIS TORRES` + `TORRES MORA` → `LUIS`).
- **Nombres, Apellidos y Razón Social tienen autocompletar**: escribir en ellos hace que MyE cargue
  OTRO proveedor. La app corrige Nombres sin teclear, al final, y verifica que la identificación
  siga siendo la del Excel antes de previsualizar.
- La página bloquea `eval` (CSP): las esperas se hacen por sondeo.
- Vista previa = `#modalPrevisualizacionDocumento`: `#confirmarPrevisualizacion` (registra y envía a la DIAN) y `#cerrarPrevisualizacion` (Cancelar). Previsualizar no consume numeración.
- Consultar Documentos: el navegador puede autocompletar «Identificación» con el usuario de MyE; la app lo borra. La barra tiene iconos peligrosos («Eliminar Selección», «Enviar a la DIAN», «Regenerar UBL»…): la app solo usa «Descargar AttachedDocument (Zip)».
- El ZIP viene anidado: `DocumentosElectronicosZipAdq.zip` → `DE25_2264.zip` → PDF + XML.
- Herramientas de reconocimiento: `scripts/mye_browser.py` + `scripts/probe_mye*.py` (solo lectura).

## Abrir la app

Doble clic en **`Iniciar app.bat`** (o `python -m app.ui`). La ventana tiene tres pestañas:

- **Nuevo consolidado:** seleccione el Excel, escriba el *nombre del consolidado*
  (ej. "CORTE 103") y la *fecha del corte* (DD/MM/AAAA), y pulse **Validar Excel**.
  Verá las filas válidas, las filas con error (en rojo, con el motivo), el total a
  pagar y las observaciones que se usarán en MyE y CADI.
  El botón **Descargar plantilla** genera el Excel vacío con las columnas correctas.
- **Seguimiento:** solo el consolidado en curso (el último validado o procesado); al validar un
  corte nuevo cambia a ese corte y se actualiza fila a fila mientras procesa. **Exportar reporte**.
- **Registro:** historial acumulado de todos los cortes (selector «Todos» o uno en particular)
  para revisar cortes anteriores, con **Exportar reporte**.
- **Configuración:** guardar credenciales (sin mostrarlas) y ver los parámetros de negocio.

## Plantilla del Excel

Un solo archivo con **una sola hoja**; encabezados en la fila 1, exactamente:

| A | B | C | D |
|---|---|---|---|
| ID POSEEDOR | Nombre del proveedor | orden de compra | Total |

(no importan mayúsculas ni tildes). Si los encabezados no coinciden, la app lo indica y no procesa nada.

## Instalación (Windows)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Credenciales

Se guardan en el archivo **`.env`** de la carpeta de la app (está en `.gitignore`):

- Desde la app: pestaña **Configuración** → **Guardar…** en cada credencial (no se muestran en pantalla).
- O editando `.env` a partir de `.env.example` (`MYE_USER`, `MYE_PASSWORD`, `CADI_DOCUMENT`, `CADI_PASSWORD`).
- Por consola: `python -m app.main set-credential MYE_PASSWORD` (agregue `--keyring` para usar el
  Administrador de credenciales de Windows en lugar de `.env`).

> ⚠️ `.env` queda en texto plano. Si la carpeta está en OneDrive, se sincroniza a la nube:
> no la comparta y considere mover la app a una carpeta local o usar `--keyring`.

El captcha de MyE-invoice, Cloudflare Turnstile y el código por correo de CADI
**no se automatizan**: la app llena usuario y contraseña y deja el cursor en el captcha: usted solo escribe la suma y pulsa Enter; la app detecta la sesión sola. El navegador queda abierto, así que el login es una vez por sesión de la app.

## Uso por consola (opcional)

```powershell
python -m app.main run --excel "ordenes.xlsx" --consolidado "CORTE 103" --corte-date 09/10/2026
# Opciones: --live, --only-row 23, --limit 5, --auto-confirm, --attach-xml, --mask-ids (antes del subcomando)

python -m app.main status --consolidado "CORTE 103"
python -m app.main retry --failed
python -m app.main report --consolidado "CORTE 103"
python -m app.main template plantilla.xlsx
```

## Validaciones del Excel

- Se leen **todas** las filas (también las ocultas por filtro), columnas A–D.
- ID numérico, OC de 10 dígitos, total > 0 (acepta `3.250.000`, `3,250,000.00`, `$ 3.250.000`).
- OC duplicadas en el archivo: se rechazan todas sus filas.
- El Excel se copia a una carpeta temporal y se abre en solo lectura.

## Estado e idempotencia

`state.db` (SQLite) guarda por (OC, ID proveedor), junto con el nombre del consolidado y la fecha del corte:
`PENDING → MYE_REGISTERED → DIAN_OK → ZIP_DOWNLOADED → CADI_CREATED → CADI_REVIEWED → DONE`
o `FAILED` conservando el último estado OK para reanudar. Solo se permite avanzar
al estado inmediatamente siguiente.

## Configuración

Constantes de negocio (concepto 4220, medio de pago 45, empresa DA25, tipo
"FAC ELEC - TERCERIZACION", centro OPERATIVO, revisor, plantillas de observación)
en `app/config.py`; algunas se pueden sobrescribir desde `.env`.

## Pruebas

```powershell
python -m pytest -q
```
