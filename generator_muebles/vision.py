"""
Paso 0 para muebles: en UNA sola llamada de visión se resuelven las dos preguntas.

  1. ¿cuál de los objetos de la foto es el producto?  (igual que en la base)
  2. ¿qué categoría de mueble es y cómo se coloca?     (esto es lo específico)

Se apoya en generator.analizar.consultar_vision(), así que reutiliza el cliente HTTP, la
clave y el modelo de la base; y el resultado sigue siendo compatible con
generator.analizar.elegir(), que es quien pregunta al usuario si hay ambigüedad.
"""

import json
from pathlib import Path

from generator import analizar

from . import categorias

INSTRUCCION = analizar.INSTRUCCION + """

Además, para el objeto que sea el producto principal, indicá:
- "categoria": una de esta lista exacta: {lista}. Usá "otro" si ninguna encaja.
- "colocacion": "piso" si el mueble se apoya en el suelo, "pared" si va colgado o
  amurado, "superficie" si va apoyado sobre otro mueble (una lámpara de mesa, por ejemplo).
- "confianza_categoria": 0 a 1.
- "es_mueble": false si el producto principal no es un mueble."""

ESQUEMA = json.loads(json.dumps(analizar.ESQUEMA))       # copia profunda
ESQUEMA["properties"].update({
    "categoria": {"type": "string"},
    "colocacion": {"type": "string"},
    "confianza_categoria": {"type": "number"},
    "es_mueble": {"type": "boolean"},
})
ESQUEMA["required"] = ESQUEMA["required"] + [
    "categoria", "colocacion", "confianza_categoria", "es_mueble"]


def analizar_mueble(imagen: Path, carpeta: Path, forzar=False):
    """Cachea en carpeta/analisis_mueble.json. -> dict compatible con analizar.elegir()."""
    cache = carpeta / "analisis_mueble.json"
    if cache.exists() and not forzar:
        print(f"  [visión] análisis previo reutilizado ({cache.name})")
        return json.loads(cache.read_text(encoding="utf-8"))

    print(f"  [visión] identificando producto y categoría de mueble…")
    datos = analizar.consultar_vision(
        imagen,
        INSTRUCCION.format(lista=", ".join(categorias.lista())),
        ESQUEMA,
        "producto_y_categoria",
    )
    carpeta.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    return datos


def resolver_categoria(analisis, forzada=""):
    """Categoría efectiva + de dónde salió, sin inventar: si no está en la tabla, 'otro'."""
    if forzada:
        if forzada not in categorias.CATEGORIAS:
            raise ValueError(f"categoría desconocida: {forzada} "
                             f"(hay: {', '.join(categorias.lista())})")
        return forzada, "indicada por el usuario", 1.0

    detectada = (analisis.get("categoria") or "").strip().lower()
    confianza = float(analisis.get("confianza_categoria") or 0)
    if detectada not in categorias.CATEGORIAS:
        return "otro", f"detectada como '{detectada}', fuera de la tabla", confianza
    return detectada, "detectada por visión", confianza
