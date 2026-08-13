"""
Recorte del fondo por relleno desde los bordes (flood fill), para obtener PNG con alfa
cuando el modelo de imagen no sabe generar transparencia (gpt-image-2 no acepta
background=transparent; gpt-image-1/1.5 sí).

Por qué flood fill y no un umbral global: este pipeline procesa productos BLANCOS sobre
fondo BLANCO todo el tiempo (una vitrina blanca, una zapatilla blanca). Un umbral por
color se comería el producto; el relleno desde los bordes solo borra el fondo que está
CONECTADO al borde, así que los interiores encerrados (el vidrio de una vitrina, el hueco
de un asa) se conservan.

Necesita Pillow. Si no está, devuelve None y el pipeline sigue con la imagen opaca.
"""

from pathlib import Path

SENTINELA = (255, 0, 255)


def recortar(entrada: Path, salida: Path = None, tolerancia=24, suavizado=0.8):
    """Devuelve la ruta del PNG con alfa, o None si no se pudo."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
        import numpy as np
    except ImportError:
        return None

    salida = salida or entrada.with_name(entrada.stem + "_alpha.png")
    img = Image.open(entrada).convert("RGB")
    marcado = img.copy()
    w, h = img.size
    for semilla in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(marcado, semilla, SENTINELA, thresh=tolerancia)

    px = np.asarray(marcado)
    fondo = ((px[:, :, 0] == SENTINELA[0]) & (px[:, :, 1] == SENTINELA[1]) &
             (px[:, :, 2] == SENTINELA[2]))
    if fondo.mean() < 0.05:
        return None                      # no encontró fondo: mejor no romper la imagen

    alfa = Image.fromarray(((~fondo) * 255).astype("uint8"), "L")
    if suavizado:
        alfa = alfa.filter(ImageFilter.GaussianBlur(suavizado))
    rgba = img.convert("RGBA")
    rgba.putalpha(alfa)
    rgba.save(salida, "PNG")
    return salida
