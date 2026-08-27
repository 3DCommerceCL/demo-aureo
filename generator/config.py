"""
Configuración del generador de modelos 3D (imagen de producto + medidas -> GLB métrico).

Las claves se leen de generator/.env (NO versionado) o del entorno del sistema.
Nada de secretos en el código ni en los jobs.
"""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ENV_FILE = RAIZ / ".env"


def _cargar_env():
    if not ENV_FILE.exists():
        return
    for linea in ENV_FILE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()


def var(nombre: str, defecto: str = "") -> str:
    """
    Lee una variable de entorno y la normaliza antes de usarla.

    En los paneles de las plataformas (Railway, cPanel) es facilísimo pegar la
    línea entera de un .env —`OPENAI_API_KEY=sk-proj-…`— dentro del campo del
    VALOR. La variable queda «puesta», cualquier chequeo de presencia dice que
    sí, y la API responde 401 con una clave que a simple vista se ve bien. Se
    limpia acá, una sola vez, en vez de perseguirlo en cada llamada.
    """
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto

    valor = valor.strip()
    for prefijo in (f"export {nombre}=", f"{nombre}="):
        if valor.startswith(prefijo):
            valor = valor[len(prefijo):].strip()
            break

    # Comillas de un .env que sobrevivieron al copiar y pegar.
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
        valor = valor[1:-1].strip()

    return valor or defecto


# Prefijo con el que empieza la clave de cada proveedor. Sirve para descubrir un
# valor mal pegado ANTES de gastar una llamada, y para que /health diga algo
# útil en vez de un "presente: sí" que no significa nada.
PREFIJOS_CLAVE = {
    "OPENAI_API_KEY": ("sk-",),
    "MESHY_API_KEY": ("msy_",),
}


def revisar_clave(nombre: str, valor: str) -> dict:
    """Diagnóstico de una clave, sin revelarla."""
    if not valor:
        return {"presente": False, "formato": "falta"}

    esperados = PREFIJOS_CLAVE.get(nombre, ())
    if esperados and not valor.startswith(esperados):
        if nombre in valor:
            formato = (
                "el valor contiene el nombre de la variable: se pegó la línea "
                "entera del .env en vez de solo la clave"
            )
        else:
            formato = "no empieza con " + " ni ".join(esperados)
    else:
        formato = "ok"

    return {"presente": True, "formato": formato, "largo": len(valor)}


JOBS_DIR = Path(var("GEN3D_JOBS_DIR") or RAIZ / "jobs")
# Las variantes de imagen del paso 1 también salen del paquete cuando el
# generador corre en un contenedor: ahí el disco es efímero y perder una
# variante ya pagada por un redespliegue no tiene sentido.
PRUEBAS_DIR = Path(var("GEN3D_PRUEBAS_DIR") or RAIZ / "pruebas")

# ---- OpenAI (paso 1: limpieza de la imagen de producto) ----
OPENAI_API_KEY = var("OPENAI_API_KEY", "")
OPENAI_BASE = var("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
# gpt-image-2 es el modelo insignia (abr-2026). gpt-image-1 se retira el 23-oct-2026.
OPENAI_IMAGE_MODEL = var("OPENAI_IMAGE_MODEL", "gpt-image-2")
# Modelo de visión del paso 0 (¿cuál de los objetos de la foto es el producto?).
OPENAI_VISION_MODEL = var("OPENAI_VISION_MODEL", "gpt-5.4-mini")
OPENAI_IMAGE_QUALITY = var("OPENAI_IMAGE_QUALITY", "medium")   # low | medium | high
OPENAI_IMAGE_SIZE = var("OPENAI_IMAGE_SIZE", "1024x1024")
# Costo aproximado por imagen de 1024x1024 (gpt-image-2, ago-2026). Sirve para estimar
# antes de gastar; el número exacto de cada corrida sale de `usage` en la respuesta.
COSTO_IMAGEN_USD = {"low": 0.006, "medium": 0.053, "high": 0.211}


def costo_imagen(quality=None):
    return COSTO_IMAGEN_USD.get(quality or OPENAI_IMAGE_QUALITY, 0.053)

# ---- Meshy (paso 2: imagen -> malla texturizada) ----
MESHY_API_KEY = var("MESHY_API_KEY", "")
MESHY_BASE = var("MESHY_BASE_URL", "https://api.meshy.ai").rstrip("/")
MESHY_AI_MODEL = var("MESHY_AI_MODEL", "meshy-6")
MESHY_TEXTURE_RES = var("MESHY_TEXTURE_RES", "2k")             # 2k | 4k | 8k
MESHY_POLYCOUNT = int(var("MESHY_POLYCOUNT", "30000"))
MESHY_TIMEOUT_S = int(var("MESHY_TIMEOUT_S", "1800"))

# ---- Blender (paso 4, opcional: verificación independiente + USDZ + previews) ----
BLENDER_EXE = var(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
)


class FaltaClave(RuntimeError):
    pass


def exigir(valor: str, nombre: str) -> str:
    if not valor:
        raise FaltaClave(
            f"Falta {nombre}. Copiá generator/.env.example a generator/.env y completalo "
            f"(o exportá {nombre} en el entorno)."
        )
    return valor
