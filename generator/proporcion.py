"""
Proporción real del producto en la imagen que ve Meshy.

Meshy reconstruye a partir de la SILUETA: si en la imagen limpia el objeto sale con otra
relación alto/ancho que la real, la malla nace con esa proporción equivocada y después
hay que deformarla al escalar. Es mucho mejor arreglarlo en la imagen (gratis, sin
pérdida) que en la malla.

Dos funciones:
  relacion()  — mide alto/ancho de la silueta del objeto.
  corregir()  — reescala la imagen (sin deformar el encuadre) para que la silueta tenga
                exactamente la relación declarada por el usuario.

Solo tiene sentido en vistas ortográficas (frontal, lateral, cenital): en una vista de
tres cuartos el ancho de la silueta mezcla ancho y profundidad, y estirar la imagen
rompería la perspectiva.
"""

from pathlib import Path

# Relación alto/ancho de la silueta según la vista, a partir de (alto, ancho, largo).
RELACION_POR_VISTA = {
    "frontal": lambda al, an, la: al / an,
    "lateral": lambda al, an, la: al / la,
    "cenital": lambda al, an, la: la / an,
}


def _mascara(ruta):
    from PIL import Image
    import numpy as np
    px = np.asarray(Image.open(ruta).convert("RGBA")).astype("int16")
    alfa = px[:, :, 3]
    if (alfa < 250).any():
        return alfa > 32, None, np
    rgb = px[:, :, :3]
    k = 16
    esquinas = np.concatenate([rgb[:k, :k].reshape(-1, 3), rgb[:k, -k:].reshape(-1, 3),
                               rgb[-k:, :k].reshape(-1, 3), rgb[-k:, -k:].reshape(-1, 3)])
    color = np.median(esquinas, axis=0)
    return (np.abs(rgb - color).max(axis=2) > 24), tuple(int(c) for c in color), np


def relacion(ruta: Path):
    """-> (alto/ancho de la silueta, bbox) o (None, None) si no se pudo medir."""
    try:
        objeto, _, np = _mascara(ruta)
    except Exception:
        return None, None
    if not objeto.any():
        return None, None
    filas = np.where(objeto.any(axis=1))[0]
    cols = np.where(objeto.any(axis=0))[0]
    alto = int(filas[-1] - filas[0] + 1)
    ancho = int(cols[-1] - cols[0] + 1)
    return alto / ancho, (int(cols[0]), int(filas[0]), int(cols[-1]), int(filas[-1]))


def objetivo(vista, alto, ancho, largo):
    """Relación alto/ancho que debería tener la silueta en esa vista, o None."""
    fn = RELACION_POR_VISTA.get(vista)
    return fn(alto, ancho, largo) if fn else None


def corregir(ruta: Path, relacion_objetivo, salida: Path = None, umbral=0.01, maximo=1.25):
    """
    Reescala la imagen para que la silueta quede con `relacion_objetivo` (alto/ancho).
    Siempre ACHICA el eje que sobra (nunca agranda), así nada se sale del lienzo, y
    recompone sobre un lienzo del mismo tamaño con el color de fondo original.

    `maximo`: freno de seguridad. Si hace falta deformar más que eso, NO se toca la
    imagen. Una corrección enorme casi nunca significa "el modelo dibujó mal la
    proporción": significa que dibujó OTRA VISTA (pedimos lateral y devolvió un frontal)
    o que las medidas declaradas no son las de ese producto. Estirar en ese caso fabrica
    una imagen falsa y Meshy la copia.

    Devuelve un informe, o None si no se pudo medir.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    actual, _ = relacion(ruta)
    if not actual or not relacion_objetivo:
        return None
    factor = relacion_objetivo / actual
    if abs(factor - 1.0) < umbral:
        return {"aplicado": False, "relacion_antes": round(actual, 4),
                "relacion_objetivo": round(relacion_objetivo, 4), "motivo": "ya coincide"}
    if max(factor, 1 / factor) > maximo:
        return {
            "aplicado": False,
            "relacion_antes": round(actual, 4),
            "relacion_objetivo": round(relacion_objetivo, 4),
            "factor_necesario": round(factor, 4),
            "motivo": (f"haría falta deformar x{factor:.2f} (límite {maximo}): la imagen "
                       f"probablemente no es la vista pedida, o las medidas declaradas no "
                       f"corresponden a este producto"),
        }

    img = Image.open(ruta).convert("RGBA")
    w, h = img.size
    if factor > 1:                     # falta alto relativo -> se angosta
        sx, sy, eje = 1.0 / factor, 1.0, "ancho"
    else:                              # sobra alto relativo -> se achata
        sx, sy, eje = 1.0, factor, "alto"

    escalada = img.resize((max(1, round(w * sx)), max(1, round(h * sy))), Image.LANCZOS)
    _, color, _np = _mascara(ruta)
    fondo = (0, 0, 0, 0) if color is None else (*color, 255)
    lienzo = Image.new("RGBA", (w, h), fondo)
    lienzo.paste(escalada, ((w - escalada.width) // 2, (h - escalada.height) // 2), escalada)

    destino = Path(salida or ruta)
    if color is None:
        lienzo.save(destino, "PNG")
    else:
        lienzo.convert("RGB").save(destino, "PNG")

    nueva, _ = relacion(destino)
    return {
        "aplicado": True,
        "eje_ajustado": eje,
        "factor": round(factor, 4),
        "relacion_antes": round(actual, 4),
        "relacion_objetivo": round(relacion_objetivo, 4),
        "relacion_despues": round(nueva, 4) if nueva else None,
        "error_pct": round(100 * (nueva - relacion_objetivo) / relacion_objetivo, 2)
                     if nueva else None,
    }
