"""
Control de calidad automático de la imagen limpia, antes de gastar créditos en Meshy.

No reemplaza la revisión visual: marca las fallas que se pueden medir (objeto recortado,
demasiado chico en el frame, fondo con sombras, descentrado) para que la revisión manual
se concentre en lo que importa (¿es el mismo producto? ¿inventó detalles?).

Usa Pillow + numpy si están; si no, devuelve {"disponible": False} y no rompe nada.
"""


def _cargar(ruta):
    from PIL import Image
    import numpy as np
    img = Image.open(ruta)
    img = img.convert("RGBA")
    return np.asarray(img).astype("int16"), np


def analizar(ruta):
    try:
        px, np = _cargar(ruta)
    except Exception as e:                     # PIL ausente o imagen ilegible
        return {"disponible": False, "motivo": str(e)}

    alto, ancho = px.shape[:2]
    rgb, alfa = px[:, :, :3], px[:, :, 3]
    tiene_alpha = bool((alfa < 250).any())

    if tiene_alpha:
        objeto = alfa > 32
        dist_fondo = None
    else:
        # color de fondo = mediana de las 4 esquinas (16x16 cada una)
        k = 16
        esquinas = np.concatenate([
            rgb[:k, :k].reshape(-1, 3), rgb[:k, -k:].reshape(-1, 3),
            rgb[-k:, :k].reshape(-1, 3), rgb[-k:, -k:].reshape(-1, 3)])
        color_fondo = np.median(esquinas, axis=0)
        dist_fondo = np.abs(rgb - color_fondo).max(axis=2)
        objeto = dist_fondo > 24

    total = alto * ancho
    ocupacion = float(objeto.sum()) * 100.0 / total
    info = {
        "disponible": True,
        "tamano": [ancho, alto],
        "alpha": tiene_alpha,
        "ocupacion_pct": round(ocupacion, 1),
        "avisos": [],
    }

    if objeto.any():
        filas = np.where(objeto.any(axis=1))[0]
        cols = np.where(objeto.any(axis=0))[0]
        arriba, abajo, izq, der = int(filas[0]), int(filas[-1]), int(cols[0]), int(cols[-1])
        margenes = {"arriba": arriba, "abajo": alto - 1 - abajo,
                    "izquierda": izq, "derecha": ancho - 1 - der}
        info["margen_pct"] = {k: round(v * 100.0 / (alto if k in ("arriba", "abajo") else ancho), 1)
                              for k, v in margenes.items()}
        # Cuánto del encuadre abarca el objeto por su lado más largo. Es mejor señal que
        # el área: un producto alto y angosto (una vitrina) ocupa poca área y está bien.
        info["extension_pct"] = round(max((der - izq + 1) * 100.0 / ancho,
                                          (abajo - arriba + 1) * 100.0 / alto), 1)
        tocados = [k for k, v in margenes.items() if v <= 1]
        info["descentrado_pct"] = {
            "x": round(((izq + der) / 2.0 - ancho / 2.0) * 100.0 / ancho, 1),
            "y": round(((arriba + abajo) / 2.0 - alto / 2.0) * 100.0 / alto, 1),
        }

        if tocados:
            info["avisos"].append(f"el objeto toca {', '.join(tocados)}: está recortado")
        elif min(info["margen_pct"].values()) < 3:
            info["avisos"].append(
                f"margen mínimo {min(info['margen_pct'].values())}%: casi sin aire")
        dx, dy = info["descentrado_pct"]["x"], info["descentrado_pct"]["y"]
        if max(abs(dx), abs(dy)) > 8:
            info["avisos"].append(f"descentrado: {dx:+.0f}% en X, {dy:+.0f}% en Y")
        if info["extension_pct"] < 55:
            info["avisos"].append(
                f"el objeto abarca solo {info['extension_pct']:.0f}% del encuadre: "
                f"poco detalle para Meshy")
    else:
        info["avisos"].append("no se detectó objeto (¿producto del mismo color que el fondo?)")

    if ocupacion > 90:
        info["avisos"].append(f"el objeto ocupa {ocupacion:.0f}% del frame: sin margen")

    if dist_fondo is not None:
        fondo = ~objeto
        if fondo.any():
            # >20 de desvío es una sombra o un objeto de verdad; los degradados suaves que
            # deja el modelo de imagen quedan por debajo y no molestan a Meshy.
            sucio = float(((dist_fondo > 20) & fondo).sum()) * 100.0 / float(fondo.sum())
            info["fondo_sucio_pct"] = round(sucio, 2)
            info["fondo_desvio_medio"] = round(float(dist_fondo[fondo].mean()), 1)
            if sucio > 2:
                info["avisos"].append(
                    f"fondo no uniforme ({sucio:.0f}%): sombras, degradado o piso visible")

    info["veredicto"] = "ok" if not info["avisos"] else "revisar"
    return info
