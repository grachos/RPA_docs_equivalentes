from app.progress import BatchProgress

OCS = ["4600485719", "4600485720", "4600485721"]


def test_real_batch_progress():
    p = BatchProgress(OCS, real=True)
    assert p.fraction == 0 and p.row_text(OCS[0]) == "0/5"
    p.update(OCS[0], "Procesando…")
    assert p.row_label(OCS[0], "Procesando…") == "OC 4600485719 · paso 1/5: Registro MyE"
    p.update(OCS[0], "Registrado")
    p.update(OCS[1], "Error")
    p.update(OCS[2], "Registrado")
    p.update(OCS[0], "Esperando DIAN")
    assert p.row_label(OCS[0], "Esperando DIAN") == "OC 4600485719 · paso 2/5: Espera DIAN"
    for status in ("Descargando ZIP", "ZIP descargado", "Factura CADI…", "Creada en CADI", "En revisión…"):
        p.update(OCS[0], status)
    assert p.row_text(OCS[0]) == "4/5"
    p.update(OCS[0], "Terminada")
    assert p.row_text(OCS[0]) == "✔" and p.row_text(OCS[1]) == "✖" and p.row_text(OCS[2]) == "1/5"
    assert abs(p.fraction - (1 + 1 + 0.2) / 3) < 1e-9
    assert p.summary().startswith("73% · 2 de 3 filas finalizadas (1 OK, 1 con error u omitidas)")


def test_progress_never_goes_back_and_ignores_unknown_status():
    p = BatchProgress(OCS[:1], real=True)
    p.update(OCS[0], "ZIP descargado")
    p.update(OCS[0], "Esperando DIAN")  # e.g. a late message: never moves backwards
    p.update(OCS[0], "Esperando confirmación")
    assert p.row_text(OCS[0]) == "3/5"


def test_simulation_has_one_step():
    p = BatchProgress(OCS[:2], real=False)
    p.update(OCS[0], "Procesando…")
    assert p.row_label(OCS[0], "Procesando…") == "OC 4600485719 · paso 1/1: Simulación"
    p.update(OCS[0], "Simulación OK")
    p.update(OCS[1], "CADI simulación OK")
    assert p.fraction == 1 and "2 OK" in p.summary()
