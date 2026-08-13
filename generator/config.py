"""
Configuración del generador de modelos 3D (imagen de producto + medidas -> GLB métrico).

Las claves se leen de generator/.env (NO versionado) o del entorno del sistema.
Nada de secretos en el código ni en los jobs.
"""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("GEN3D_JOBS_DIR", RAIZ / "jobs"))
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

# ---- OpenAI (paso 1: limpieza de la imagen de producto) ----
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
# gpt-image-2 es el modelo insignia (abr-2026). gpt-image-1 se retira el 23-oct-2026.
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
# Modelo de visión del paso 0 (¿cuál de los objetos de la foto es el producto?).
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-5.4-mini")
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")   # low | medium | high
OPENAI_IMAGE_SIZE = os.environ.get("OPENAI_IMAGE_SIZE", "1024x1024")
# Costo aproximado por imagen de 1024x1024 (gpt-image-2, ago-2026). Sirve para estimar
# antes de gastar; el número exacto de cada corrida sale de `usage` en la respuesta.
COSTO_IMAGEN_USD = {"low": 0.006, "medium": 0.053, "high": 0.211}


def costo_imagen(quality=None):
    return COSTO_IMAGEN_USD.get(quality or OPENAI_IMAGE_QUALITY, 0.053)

# ---- Meshy (paso 2: imagen -> malla texturizada) ----
MESHY_API_KEY = os.environ.get("MESHY_API_KEY", "")
MESHY_BASE = os.environ.get("MESHY_BASE_URL", "https://api.meshy.ai").rstrip("/")
MESHY_AI_MODEL = os.environ.get("MESHY_AI_MODEL", "meshy-6")
MESHY_TEXTURE_RES = os.environ.get("MESHY_TEXTURE_RES", "2k")             # 2k | 4k | 8k
MESHY_POLYCOUNT = int(os.environ.get("MESHY_POLYCOUNT", "30000"))
MESHY_TIMEOUT_S = int(os.environ.get("MESHY_TIMEOUT_S", "1800"))

# ---- Blender (paso 4, opcional: verificación independiente + USDZ + previews) ----
BLENDER_EXE = os.environ.get(
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
