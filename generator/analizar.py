"""
PASO 0 — ¿Cuál es el producto?

Las fotos de e-commerce suelen ser ambientadas: el producto está entre plantas, lámparas,
sofás y objetos decorativos, y encima puede tener cosas apoyadas o adentro. Si no se
resuelve esa ambigüedad ANTES de limpiar la imagen, el modelo de imagen elige por su
cuenta y Meshy termina reconstruyendo la planta.

Este módulo mira la foto con un modelo de visión, enumera los objetos candidatos y deja
que el usuario elija (o pase el suyo con --objeto). El objeto elegido se inyecta en el
prompt de limpieza como "esto es lo que hay que aislar, todo lo demás se borra".

Costo: unas décimas de centavo por foto (gpt-5.4-mini). Se guarda en analisis.json y se
reutiliza: no se vuelve a analizar la misma foto.
"""

import base64
import json
import mimetypes
import sys
from pathlib import Path

from . import config, net

INSTRUCCION = """Esta es la foto de una ficha de producto de e-commerce. Queremos
reconstruir UN objeto en 3D, así que necesitamos saber cuál es el producto y poder
distinguirlo del resto de la escena.

Enumerá los objetos físicos DISTINTOS que se ven y que podrían ser el producto,
ordenados del más probable al menos probable. Reglas:
- No listes paredes, piso, techo, ventanas, puertas de la habitación, cortinas, sombras
  ni el fondo.
- No listes partes de un mismo objeto por separado (una puerta de una vitrina, un cajón,
  un almohadón de un sofá NO son objetos aparte).
- Sí listá los objetos decorativos que estén apoyados sobre el producto o adentro de él,
  porque a veces el producto es uno de ellos.
- Máximo 8 objetos.

Para cada uno:
- "nombre": 2 a 5 palabras, en español, como lo llamaría una persona.
- "descripcion": una frase con color, material y forma, suficiente para que otro modelo
  sepa exactamente cuál aislar.
- "ubicacion": dónde está en la imagen (centro, izquierda, sobre la mesa, dentro del
  mueble…).
- "es_producto_probable": true solo para el candidato o los candidatos plausibles a ser
  el producto en venta (normalmente el objeto más grande, centrado y protagonista).

"ambiguo": true si hay más de un candidato razonable a ser el producto.
"resumen": una frase describiendo la escena completa."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "objetos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "ubicacion": {"type": "string"},
                    "es_producto_probable": {"type": "boolean"},
                },
                "required": ["nombre", "descripcion", "ubicacion", "es_producto_probable"],
                "additionalProperties": False,
            },
        },
        "ambiguo": {"type": "boolean"},
        "resumen": {"type": "string"},
    },
    "required": ["objetos", "ambiguo", "resumen"],
    "additionalProperties": False,
}


def _data_uri(ruta: Path) -> str:
    tipo = mimetypes.guess_type(ruta.name)[0] or "image/png"
    return f"data:{tipo};base64," + base64.b64encode(ruta.read_bytes()).decode("ascii")


def _texto_de_respuesta(resp):
    """Extrae el texto del primer mensaje de la Responses API (saltea items de razonamiento)."""
    for item in resp.get("output", []):
        if item.get("type") != "message":
            continue
        for parte in item.get("content", []):
            if parte.get("type") == "output_text":
                return parte["text"]
    raise RuntimeError(f"respuesta sin texto: {json.dumps(resp)[:400]}")


def detectar(imagen: Path, modelo=None):
    """Mira la foto y devuelve {objetos, ambiguo, resumen, usage, modelo}."""
    clave = config.exigir(config.OPENAI_API_KEY, "OPENAI_API_KEY")
    modelo = modelo or config.OPENAI_VISION_MODEL
    cuerpo = {
        "model": modelo,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": INSTRUCCION},
                {"type": "input_image", "image_url": _data_uri(imagen)},
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "objetos_de_la_escena",
                            "schema": ESQUEMA, "strict": True}},
    }
    print(f"  [visión] {modelo}: identificando objetos…")
    resp = net.post_json(f"{config.OPENAI_BASE}/responses", cuerpo,
                         {"Authorization": f"Bearer {clave}"}, timeout=300)
    datos = json.loads(_texto_de_respuesta(resp))
    datos["modelo"] = modelo
    datos["usage"] = resp.get("usage", {})
    return datos


def cargar_o_detectar(imagen: Path, carpeta: Path, forzar=False):
    """Cachea el análisis en carpeta/analisis.json."""
    cache = carpeta / "analisis.json"
    if cache.exists() and not forzar:
        print(f"  [visión] análisis previo reutilizado ({cache.name})")
        return json.loads(cache.read_text(encoding="utf-8"))
    datos = detectar(imagen)
    cache.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    return datos


def _mostrar(analisis):
    print(f"\n  Escena: {analisis.get('resumen', '')}")
    for i, o in enumerate(analisis.get("objetos", []), 1):
        marca = "  <- probable producto" if o.get("es_producto_probable") else ""
        print(f"   {i}. {o['nombre']} ({o['ubicacion']}){marca}")
        print(f"      {o['descripcion']}")


def elegir(analisis, forzado="", auto=False, confirmar=False):
    """
    Devuelve el objeto elegido: {"nombre","descripcion","ubicacion","origen"}.
      forzado:   texto libre del usuario (--objeto), gana siempre.
      auto:      sin preguntar, toma el primer candidato probable.
      confirmar: preguntar siempre, aunque el análisis no vea ambigüedad.
    Si hay que preguntar y no hay terminal interactiva, corta con un mensaje claro.
    """
    if forzado:
        return {"nombre": forzado, "descripcion": forzado, "ubicacion": "",
                "origen": "indicado por el usuario"}

    objetos = analisis.get("objetos", [])
    if not objetos:
        return None
    probables = [o for o in objetos if o.get("es_producto_probable")] or objetos[:1]

    preguntar_igual = confirmar and len(objetos) > 1 and not auto
    if not preguntar_igual:
        if len(objetos) == 1 or (auto and probables):
            elegido = dict(probables[0])
            elegido["origen"] = "único candidato" if len(objetos) == 1 else "automático (--auto)"
            print(f"  [visión] producto: {elegido['nombre']}")
            return elegido
        if not analisis.get("ambiguo") and len(probables) == 1:
            elegido = dict(probables[0])
            elegido["origen"] = "único candidato probable"
            print(f"  [visión] producto: {elegido['nombre']} "
                  f"(los otros {len(objetos) - 1} objetos se van a borrar)")
            return elegido

    _mostrar(analisis)
    if not sys.stdin.isatty():
        raise RuntimeError(
            "hay más de un producto posible y no hay terminal interactiva: "
            'volvé a correr agregando --objeto "descripción del producto" o --auto')

    print("\n  o. otro (describilo vos)")
    while True:
        r = input("  ¿Cuál es el producto? [número / o] ").strip().lower()
        if r in ("o", "otro"):
            texto = input("  Describí el producto: ").strip()
            if texto:
                return {"nombre": texto, "descripcion": texto, "ubicacion": "",
                        "origen": "escrito por el usuario"}
            continue
        if r.isdigit() and 1 <= int(r) <= len(objetos):
            elegido = dict(objetos[int(r) - 1])
            elegido["origen"] = "elegido por el usuario"
            return elegido
        print("  Respondé con un número de la lista o con 'o'.")


def frase(objeto):
    """Cómo se nombra el objeto dentro del prompt de limpieza."""
    if not objeto:
        return ""
    partes = [objeto.get("descripcion") or objeto.get("nombre", "")]
    if objeto.get("ubicacion"):
        partes.append(f"(en la imagen de referencia: {objeto['ubicacion']})")
    return " ".join(p for p in partes if p)
