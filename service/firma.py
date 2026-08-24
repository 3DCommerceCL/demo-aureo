"""
Firma HMAC-SHA256 de las peticiones entre WordPress y este servicio.

Por qué HMAC y no un token en una cabecera: este servicio gasta dinero real
(créditos de Meshy, llamadas a OpenAI). Un token plano le sirve para siempre a
cualquiera que lo vea una vez. Una firma sobre el cuerpo, con marca de tiempo,
vale para esa petición y nada más.

El esquema tiene que ser idéntico al del plugin
(aureo-pro/includes/modules/generator/class-aureo-gen-client.php):

    X-Aureo-Timestamp: <segundos epoch>
    X-Aureo-Signature: sha256=<hex de hmac(clave, "<ts>\\n<cuerpo>")>
"""

import hashlib
import hmac
import os
import time

VENTANA_S = 300  # tolerancia de reloj y vida útil de una firma


class SinClave(RuntimeError):
    pass


def clave() -> bytes:
    k = os.environ.get("AUREO_SHARED_KEY", "")
    if not k:
        raise SinClave(
            "falta AUREO_SHARED_KEY: es la clave compartida con WordPress, "
            "la misma que va en AUREO_PRO_GEN_SERVICE_KEY del wp-config.php"
        )
    return k.encode("utf-8")


def calcular(cuerpo: bytes, ts: int) -> str:
    mensaje = str(ts).encode("ascii") + b"\n" + cuerpo
    return "sha256=" + hmac.new(clave(), mensaje, hashlib.sha256).hexdigest()


def firmar(cuerpo: bytes = b"") -> dict:
    """Cabeceras para una petición SALIENTE (los callbacks a WordPress)."""
    ts = int(time.time())
    return {"X-Aureo-Timestamp": str(ts), "X-Aureo-Signature": calcular(cuerpo, ts)}


def verificar(cuerpo: bytes, ts_cabecera, firma_cabecera) -> str:
    """
    Comprueba una petición ENTRANTE.

    Devuelve '' si es válida, o el motivo del rechazo. No se detalla al cliente
    cuál de las comprobaciones falló más allá de esto: el motivo se registra en
    el log del servicio, y al cliente se le responde 401 a secas.
    """
    try:
        ts = int(ts_cabecera or 0)
    except (TypeError, ValueError):
        return "marca de tiempo ilegible"

    if not ts:
        return "falta la marca de tiempo"

    # Sin esta comprobación, una petición capturada se podría reenviar tal cual
    # más adelante: la firma seguiría siendo válida para siempre.
    if abs(time.time() - ts) > VENTANA_S:
        return "marca de tiempo fuera de la ventana"

    if not firma_cabecera:
        return "falta la firma"

    # compare_digest y no ==: comparar en tiempo constante evita filtrar la
    # firma correcta midiendo cuánto tarda en fallar.
    if not hmac.compare_digest(calcular(cuerpo, ts), str(firma_cabecera)):
        return "firma incorrecta"

    return ""
