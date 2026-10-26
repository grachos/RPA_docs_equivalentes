from app.error_guide import SILOGTRAN, DIAN, advise, annotate


def test_known_errors_are_mapped_to_where_to_fix():
    cases = {
        "MyE rechazó la previsualización (Datos inconsistentes): El correo electronico de contacto "
        "'a@hotmail.con' no es valido": SILOGTRAN,
        "MyE rechazó la previsualización: La dirección del adquiriente tiene caracteres inválidos": SILOGTRAN,
        "La DIAN no aceptó DE252300: Validaciones contienen errores": DIAN,
    }
    for message, where in cases.items():
        assert advise(message).where == where
    assert "→ [Silogtran]" in annotate("Correo de contacto inválido en Silogtran para 1: 'x@y.con'.")


def test_annotate_is_idempotent_and_ignores_unknown():
    once = annotate("La DIAN no aceptó DE1: x")
    assert annotate(once) == once
    assert annotate("algo totalmente nuevo") == "algo totalmente nuevo"
