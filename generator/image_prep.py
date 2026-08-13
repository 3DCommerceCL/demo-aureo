"""
PASO 1 — Limpieza de la foto de producto con la Images API de OpenAI.

  generar_vista():  foto real (fondo cualquiera) -> producto aislado sobre fondo limpio,
                    en la vista pedida. Es LA entrada de Meshy: toda la calidad del
                    modelo 3D depende de este paso.
  vistas_extra():   [BETA] inventa lateral/trasera/otro lateral a partir de la limpia.
                    Son alucinaciones plausibles, no información real del producto.

Los prompts viven en prompts.py (es lo que se itera). Para probar variantes usá el CLI:
  py -m generator.imagen --imagen fotos/silla.jpg --vista frontal --fondo blanco
"""

import base64
import shutil
from pathlib import Path

from . import config, net, prompts, recorte

MAX_LADO_ENTRADA = 1536      # más grande no mejora el resultado y se paga por tokens de imagen


def preparar_entrada(entrada: Path, destino: Path, max_lado=MAX_LADO_ENTRADA):
    """Corrige la rotación EXIF (foto de celular) y baja la resolución. Sin PIL, copia tal cual."""
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(entrada))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if max(img.size) > max_lado:
            escala = max_lado / max(img.size)
            img = img.resize((round(img.width * escala), round(img.height * escala)),
                             Image.LANCZOS)
        img.save(destino, "PNG")
        return destino, f"{img.width}x{img.height}"
    except Exception as e:
        print(f"  [openai] sin preproceso PIL ({e}); se envía la foto original")
        destino = destino.with_suffix(entrada.suffix.lower())
        if destino.resolve() != entrada.resolve():
            shutil.copy2(entrada, destino)
        return destino, "original"


def _editar(imagenes, prompt, salida: Path, size=None, quality=None, background="opaque"):
    """POST /v1/images/edits con una o varias imágenes de referencia."""
    clave = config.exigir(config.OPENAI_API_KEY, "OPENAI_API_KEY")
    modelo = config.OPENAI_IMAGE_MODEL
    campo = "image[]" if len(imagenes) > 1 else "image"
    archivos = [(campo, Path(p).name, Path(p).read_bytes()) for p in imagenes]

    campos = {
        "model": modelo,
        "prompt": prompt,
        "n": "1",
        "size": size or config.OPENAI_IMAGE_SIZE,
        "quality": quality or config.OPENAI_IMAGE_QUALITY,
        "background": background,
        "output_format": "png",
    }
    # input_fidelity solo existe en gpt-image-1 (no en 1.5); si el modelo lo rechaza se
    # reintenta sin él en vez de romper la corrida.
    opcionales = []
    if modelo.startswith("gpt-image-1") and not modelo.startswith("gpt-image-1.5"):
        campos["input_fidelity"] = "high"
        opcionales.append("input_fidelity")

    cabeceras = {"Authorization": f"Bearer {clave}"}
    url = f"{config.OPENAI_BASE}/images/edits"
    try:
        resp = net.post_multipart(url, campos, archivos, cabeceras, timeout=600)
    except net.HttpError as e:
        sobrantes = [p for p in opcionales if p in str(e)]
        if e.codigo != 400 or not sobrantes:
            raise
        for p in sobrantes:
            campos.pop(p, None)
        print(f"  [openai] el modelo no acepta {sobrantes}; reintento sin eso")
        resp = net.post_multipart(url, campos, archivos, cabeceras, timeout=600)

    dato = resp["data"][0]
    salida.parent.mkdir(parents=True, exist_ok=True)
    if dato.get("b64_json"):
        salida.write_bytes(base64.b64decode(dato["b64_json"]))
    else:
        net.descargar(dato["url"], salida)
    return salida, resp.get("usage", {})


SIN_TRANSPARENCIA = set()      # modelos que ya rechazaron background=transparent


def generar_vista(entrada: Path, salida: Path, vista="frontal", fondo="blanco", extra="",
                  size=None, quality=None, objeto="", medidas=""):
    """Genera UNA imagen limpia. Devuelve (ruta, metadatos)."""
    modelo = config.OPENAI_IMAGE_MODEL
    prompt = prompts.construir(vista, fondo, extra, objeto, medidas)
    modo = prompts.modo_fondo(fondo)
    recortar_local = False

    # gpt-image-2 no genera alfa: se pide sobre blanco y se recorta acá.
    if modo == "transparent" and modelo in SIN_TRANSPARENCIA:
        prompt = prompts.construir(vista, "blanco", extra, objeto, medidas)
        modo, recortar_local = "opaque", True

    print(f"  [openai] {modelo} · vista {vista} · fondo {fondo}"
          f"{' (recorte local)' if recortar_local else ''}…")
    try:
        ruta, uso = _editar([entrada], prompt, salida, size, quality, modo)
    except net.HttpError as e:
        if modo != "transparent" or e.codigo != 400 or "background" not in str(e):
            raise
        print(f"  [openai] {modelo} no genera transparencia; se pide sobre blanco "
              f"y se recorta el fondo localmente")
        SIN_TRANSPARENCIA.add(modelo)
        prompt = prompts.construir(vista, "blanco", extra, objeto, medidas)
        modo, recortar_local = "opaque", True
        ruta, uso = _editar([entrada], prompt, salida, size, quality, modo)

    if recortar_local:
        recortada = recorte.recortar(ruta, ruta)
        if recortada is None:
            print("  [recorte] no se pudo aislar el fondo: queda opaca")
            recortar_local = False

    meta = {
        "fondo_via": ("recorte local" if recortar_local else "modelo"),
        "vista": vista,
        "fondo": fondo,
        "objeto": objeto,
        "prompt": prompt,
        "modelo": config.OPENAI_IMAGE_MODEL,
        "quality": quality or config.OPENAI_IMAGE_QUALITY,
        "size": size or config.OPENAI_IMAGE_SIZE,
        "usage": uso,
        "costo_usd": config.costo_imagen(quality),
    }
    print(f"  [openai] -> {ruta.name}")
    return ruta, meta


def vista_frontal(entrada: Path, salida: Path, extra="", fondo="blanco"):
    """Atajo usado por el pipeline completo."""
    return generar_vista(entrada, salida, "frontal", fondo, extra)


def vistas_extra(frontal: Path, dir_salida: Path, extra="", fondo="blanco",
                 size=None, quality=None):
    """[BETA] 3 vistas sintéticas a partir de la imagen ya limpia."""
    dir_salida.mkdir(parents=True, exist_ok=True)
    generadas, metas = [], []
    for nombre, grados in prompts.VISTAS_EXTRA:
        destino = dir_salida / f"{nombre}.png"
        if destino.exists():
            print(f"  [openai] vista {nombre} ya existe, se reutiliza")
            generadas.append(destino)
            continue
        prompt = prompts.construir_vista_extra(nombre, grados, fondo, extra)
        print(f"  [openai] vista {nombre} ({grados}°)…")
        ruta, uso = _editar([frontal], prompt, destino, size, quality,
                            prompts.modo_fondo(fondo))
        generadas.append(ruta)
        metas.append({"vista": nombre, "grados": grados, "prompt": prompt, "usage": uso,
                      "costo_usd": config.costo_imagen(quality)})
    return generadas, metas
