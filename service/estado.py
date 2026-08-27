"""
Estado de un trabajo, del lado del servicio.

REPARTO DE RESPONSABILIDADES

El pipeline ya tiene su propio estado en `jobs/<slug>/job.json`, y sabe reanudar
a partir de él: si un paso ya se hizo, no se repite ni se vuelve a pagar. Ese
archivo es del pipeline y este módulo NO lo escribe.

Lo que falta es lo que el pipeline no conoce porque no le corresponde: en qué
punto de la conversación con WordPress está el trabajo, a qué URL avisar, y cuál
de los objetos de la foto eligió la persona. Eso vive aparte, en
`jobs/<slug>/service.json`.

La instantánea que consume la API (`snapshot()`) se arma leyendo los tres
lugares: este archivo, el `job.json` del pipeline y los `NNN.json` de las
variantes de imagen.
"""

import json
import re
import unicodedata
from pathlib import Path

from generator import config

# Los dos estados donde el trabajo espera a una persona. Son la razón de ser del
# asistente de tres pasos: la imagen se itera por centavos, la malla cuesta 30
# créditos, y entre las dos cosas tiene que haber alguien mirando.
PAUSAS = ("choosing", "reviewing")

ESTADOS = (
    "draft", "analyzing", "choosing", "imaging",
    "reviewing", "meshing", "finishing", "done", "failed",
)


def slugificar(texto: str) -> str:
    """Mismo criterio que generator.pipeline.slugificar, para que las carpetas coincidan."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "job"


def carpeta_job(slug: str) -> Path:
    return config.JOBS_DIR / slug


def carpeta_pruebas(slug: str) -> Path:
    return config.PRUEBAS_DIR / slug


def _ruta(slug: str) -> Path:
    return carpeta_job(slug) / "service.json"


def leer(slug: str) -> dict:
    f = _ruta(slug)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def escribir(slug: str, datos: dict):
    carpeta_job(slug).mkdir(parents=True, exist_ok=True)
    _ruta(slug).write_text(
        json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def existe(slug: str) -> bool:
    return _ruta(slug).exists()


def crear(slug: str, nombre: str, medidas: dict, callback_url: str,
          categoria: str = "", objeto: str = "") -> dict:
    datos = {
        "job_id": slug,
        "nombre": nombre,
        "estado": "draft",
        "medidas": medidas,
        "categoria": categoria,
        "objeto": objeto,
        "callback_url": callback_url,
        "variante_aprobada": "",
        "modo": "",
        "costo_usd": 0.0,
        "error": "",
    }
    escribir(slug, datos)
    return datos


def poner_estado(slug: str, estado: str, error: str = ""):
    if estado not in ESTADOS:
        raise ValueError(f"estado desconocido: {estado}")
    d = leer(slug)
    d["estado"] = estado
    d["error"] = error
    escribir(slug, d)
    return d


def sumar_costo(slug: str, usd: float):
    d = leer(slug)
    d["costo_usd"] = round(float(d.get("costo_usd", 0)) + float(usd), 4)
    escribir(slug, d)


# ── Lectura de lo que producen los pasos ────────────────────────────────

def variantes(slug: str, base_url: str = "") -> list:
    """
    Las variantes de imagen del paso 1, leídas de los NNN.json que escribe
    imagen.py. Se devuelven en el formato del contrato de la API.

    La `url` es parte del contrato: sin ella, el plugin descarta la variante y
    la pausa de aprobación queda sin nada que mostrar. Apunta a la ruta de
    archivos del servicio, que exige la misma firma HMAC que el resto.
    """
    carpeta = carpeta_pruebas(slug)
    if not carpeta.exists():
        return []

    salida = []
    for f in sorted(carpeta.glob("[0-9][0-9][0-9].json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        archivo = d.get("archivo", "")
        if not archivo or not (carpeta / archivo).exists():
            continue

        prop = d.get("proporcion") or {}
        salida.append({
            "id": f.stem,                       # "007"
            "archivo": archivo,
            "url": f"{base_url}/files/{slug}/variantes/{archivo}",
            "vista": d.get("vista", ""),
            "fondo": d.get("fondo", ""),
            "qc": (d.get("qc") or {}).get("veredicto", ""),
            "proporcion": {
                "medida": float(prop.get("relacion_final") or prop.get("medida") or 0),
                "declarada": float(prop.get("relacion_objetivo") or prop.get("declarada") or 0),
            },
        })
    return salida


def candidatos(slug: str) -> list:
    """Los objetos que la visión encontró en la foto (paso 0)."""
    f = carpeta_pruebas(slug) / "analisis.json"
    if not f.exists():
        return []
    try:
        a = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    return [
        {
            "id": i,
            "descripcion": f"{o.get('nombre', '')} ({o.get('ubicacion', '')})".strip(),
            "probable": bool(o.get("es_producto_probable")),
        }
        for i, o in enumerate(a.get("objetos", []), 1)
    ]


def objeto_por_numero(slug: str, numero: int) -> str:
    """Traduce el número que eligió la persona al texto que entiende el pipeline."""
    f = carpeta_pruebas(slug) / "analisis.json"
    if not f.exists():
        return ""
    a = json.loads(f.read_text(encoding="utf-8"))
    objetos = a.get("objetos", [])
    if not 1 <= numero <= len(objetos):
        return ""
    o = objetos[numero - 1]
    # La descripción larga funciona mucho mejor que el nombre suelto dentro del
    # prompt de limpieza: es lo que el propio CLI le pasa a --objeto.
    return o.get("descripcion") or o.get("nombre", "")


def job_pipeline(slug: str) -> dict:
    f = carpeta_job(slug) / "job.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def entregables(slug: str, base_url: str) -> dict | None:
    """
    Los archivos finales, como URLs absolutas de este servicio.

    Devuelve None mientras no exista al menos un GLB: el plugin trata la
    ausencia como "todavía no hay nada que importar".
    """
    job = job_pipeline(slug)
    carpeta = carpeta_job(slug)
    esc = job.get("escala") or {}

    def url(nombre):
        return f"{base_url}/files/{slug}/{nombre}" if (carpeta / nombre).exists() else ""

    glb = url((job.get("archivo") or {}).get("glb", "model.glb"))
    web = url((job.get("web") or {}).get("archivo", "model_web.glb"))
    if not glb and not web:
        return None

    salida = {"model_glb": glb, "model_web": web}

    usdz = url((job.get("blender") or {}).get("usdz", "model.usdz"))
    if usdz:
        salida["model_usdz"] = usdz
    if url("preview.html"):
        salida["preview"] = url("preview.html")

    # medido_m viene en metros y con las claves alto/ancho/largo.
    if esc.get("medido_m"):
        salida["medidas_medidas"] = {
            k: float(v) for k, v in esc["medido_m"].items()
        }
    if esc.get("error_pct"):
        # El pipeline reporta el desvío con signo; al usuario le importa la
        # magnitud, y el signo no cambia ninguna decisión.
        salida["error_pct"] = {k: abs(float(v)) for k, v in esc["error_pct"].items()}

    return salida


def snapshot(slug: str, base_url: str) -> dict:
    """La instantánea completa que define el contrato de la API."""
    d = leer(slug)
    job = job_pipeline(slug)
    estado = d.get("estado", "draft")

    return {
        "job_id": slug,
        "estado": estado,
        "candidatos": candidatos(slug) if estado == "choosing" else [],
        "variantes": variantes(slug, base_url) if estado in ("reviewing", "imaging") else [],
        "categoria": d.get("categoria", ""),
        "colocacion": d.get("colocacion", "piso"),
        "costo_usd": round(float(d.get("costo_usd", 0)), 4),
        "creditos_gastados": int((job.get("meshy") or {}).get("creditos", 0)),
        # Lo que costaría el siguiente paso. Solo es distinto de cero en la
        # pausa donde hay que aprobar el gasto, que es donde el asistente lo
        # muestra.
        "creditos_siguiente_paso": 30 if estado == "reviewing" else 0,
        "medidas_reales": d.get("medidas", {}),
        "entregables": entregables(slug, base_url) if estado == "done" else None,
        "error": d.get("error") or None,
    }
