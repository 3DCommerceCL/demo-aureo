r"""
PASO 1 en modo iteración: generar imágenes limpias y revisarlas a ojo hasta que sirvan
para Meshy. No toca Meshy ni Blender: solo OpenAI.

  # ver los prompts exactos y el costo SIN gastar nada
  py -m generator.imagen --imagen fotos\silla.jpg --dry-run

  # una imagen frontal sobre blanco
  py -m generator.imagen --imagen fotos\silla.jpg --nombre silla-nordica

  # comparar variantes en una corrida (producto cartesiano vista x fondo x n)
  py -m generator.imagen --imagen fotos\silla.jpg --vista frontal tres-cuartos `
      --fondo blanco transparente --abrir

  # afinar el prompt sin tocar el código
  py -m generator.imagen --imagen fotos\silla.jpg --extra "the seat is dark walnut, not black"

  # recalcular el QC y regenerar la hoja sin llamar a la API
  py -m generator.imagen --imagen fotos\silla.jpg --qc-solo

Cada intento queda numerado en generator/pruebas/<slug>/ con su prompt, su costo y su
QC; index.html es la hoja de contacto para comparar todos los intentos del producto.
Nada se sobrescribe: cada corrida agrega intentos nuevos.
"""

import argparse
import json
import math
import sys
import time
import webbrowser
from pathlib import Path

from . import (analizar, config, gltf_scale, image_prep, net, preview, proporcion,
               prompts, qc)

# Lienzos que acepta la Images API; se elige el más parecido a la silueta del producto
# para que entre grande y sin distorsión.
LIENZOS = {"1024x1024": 1.0, "1024x1536": 1.5, "1536x1024": 1 / 1.5}


def medidas_cm(a):
    """(alto, ancho, largo) TOTALES en cm, para corregir la proporción de la silueta,
    o None si el usuario no las declaró.

    `alto` ya incluye --alto-extra: la silueta de la foto muestra la pieza Y lo que
    tenga encima (un grifo, por ejemplo), así que la proporción real a la que hay que
    corregir la imagen es la del conjunto entero, no solo la de la pieza. Ver
    --alto-extra."""
    if not (a.alto and a.ancho and a.largo):
        return None
    f = gltf_scale.UNIDADES_M[a.unidad] * 100.0
    return (a.alto + a.alto_extra) * f, a.ancho * f, a.largo * f


def elegir_lienzo(a, vista, dims):
    """Tamaño de imagen: el declarado por el usuario o el que mejor calza la proporción."""
    if a.size:
        return a.size
    if not dims:
        return config.OPENAI_IMAGE_SIZE
    objetivo = proporcion.objetivo(vista, *dims)
    if not objetivo:
        return config.OPENAI_IMAGE_SIZE
    return min(LIENZOS, key=lambda n: abs(math.log(LIENZOS[n] / objetivo)))

PRUEBAS_DIR = config.PRUEBAS_DIR
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp"}


def parsear(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--imagen", required=True,
                   help="foto del producto: archivo, carpeta con fotos, o URL")
    p.add_argument("--objeto", default="",
                   help='qué aislar, en texto libre ("la vitrina blanca"): saltea la pregunta')
    p.add_argument("--auto", action="store_true",
                   help="ante varios objetos, tomar el más probable sin preguntar")
    p.add_argument("--confirmar", action="store_true",
                   help="preguntar siempre cuál es el producto, aunque no haya ambigüedad")
    p.add_argument("--sin-analisis", action="store_true",
                   help="no mirar la foto: prompt genérico (solo si ya hay un único objeto)")
    p.add_argument("--reanalizar", action="store_true", help="rehacer el análisis de objetos")
    p.add_argument("--nombre", default="", help="nombre del producto (define la carpeta)")
    p.add_argument("--vista", nargs="+", default=["frontal"], choices=sorted(prompts.VISTAS))
    p.add_argument("--fondo", nargs="+", default=["blanco"], choices=sorted(prompts.FONDOS))
    p.add_argument("--n", type=int, default=1, help="variantes por combinación vista/fondo")
    p.add_argument("--alto", type=float, default=0.0, help="medida real (para la proporción)")
    p.add_argument("--ancho", type=float, default=0.0)
    p.add_argument("--largo", type=float, default=0.0, help="profundidad real")
    p.add_argument("--alto-extra", type=float, default=0.0,
                   help="alto de algo por encima de la pieza (p. ej. un grifo sobre un "
                        "mueble de cocina) que SÍ sale en la foto pero no cuenta como "
                        "parte de --alto")
    p.add_argument("--tiene-grifo", action="store_true",
                   help="la pieza tiene un grifo/accesorio montado de alto desconocido. "
                        "Sin --alto-extra para decir cuánto mide, no se fuerza ninguna "
                        "relación alto/ancho en frontal/lateral: se deja que la foto decida "
                        "el alto en vez de arriesgarse a achatar el grifo adentro de --alto")
    p.add_argument("--unidad", default="cm", choices=sorted(gltf_scale.UNIDADES_M))
    p.add_argument("--sin-correccion", action="store_true",
                   help="no reescalar la imagen para calzar la proporción declarada")
    p.add_argument("--max-correccion", type=float, default=1.25,
                   help="deformación máxima admitida al corregir la proporción (default 1.25)")
    p.add_argument("--quality", default=config.OPENAI_IMAGE_QUALITY,
                   choices=["low", "medium", "high"])
    p.add_argument("--size", default="",
                   help="1024x1024 | 1024x1536 | 1536x1024 | auto "
                        "(vacío: se elige según las medidas)")
    p.add_argument("--extra", default="", help="frase extra al final del prompt")
    p.add_argument("--vistas-extra", action="store_true",
                   help="[BETA] además, las 3 vistas giradas a partir del intento elegido")
    p.add_argument("--desde", type=int, default=0,
                   help="nº de intento base para --vistas-extra (default: el último)")
    p.add_argument("--qc-solo", action="store_true",
                   help="no llama a la API: recalcula el QC y regenera la hoja")
    p.add_argument("--dry-run", action="store_true", help="muestra prompts y costo, no llama a nada")
    p.add_argument("--abrir", action="store_true", help="abre la hoja de contacto al terminar")
    return p.parse_args(argv)


def slugificar(texto):
    import re
    return re.sub(r"[^a-z0-9]+", "-", texto.lower().strip()).strip("-") or "producto"


def es_url(texto):
    return str(texto).lower().startswith(("http://", "https://"))


def entradas(origen, nombre=""):
    """Fotos a procesar como [(ruta, slug)]. Acepta archivo, carpeta o URL."""
    if es_url(origen):
        from urllib.parse import urlparse
        stem = Path(urlparse(origen).path).stem or "producto"
        slug = slugificar(nombre or stem)
        carpeta = PRUEBAS_DIR / slug
        ya = sorted(carpeta.glob("original.*"))
        if ya:
            print(f"  URL ya descargada antes: {ya[0].name}")
            return [(ya[0], slug)]
        return [(net.descargar_imagen(origen, carpeta), slug)]

    ruta = Path(origen).expanduser().resolve()
    if ruta.is_dir():
        fotos = sorted(p for p in ruta.iterdir() if p.suffix.lower() in EXTENSIONES)
        if not fotos:
            sys.exit(f"No hay imágenes en {ruta}")
        return [(p, slugificar(p.stem)) for p in fotos]
    if not ruta.exists():
        sys.exit(f"No existe la imagen: {ruta}")
    return [(ruta, slugificar(nombre or ruta.stem))]


def siguiente_n(carpeta: Path):
    usados = [int(f.stem) for f in carpeta.glob("[0-9][0-9][0-9].json") if f.stem.isdigit()]
    return max(usados, default=0) + 1


def combos(a):
    return [(v, f, i) for v in a.vista for f in a.fondo for i in range(a.n)]


def comando_repetir(a, foto: Path, vista, fondo, objeto=""):
    partes = [f'py -m generator.imagen --imagen "{foto}"']
    if a.nombre:
        partes.append(f'--nombre "{a.nombre}"')
    partes.append(f"--vista {vista} --fondo {fondo} --quality {a.quality}")
    if a.size:
        partes.append(f"--size {a.size}")
    if a.alto and a.ancho and a.largo:
        partes.append(f"--alto {a.alto:g} --ancho {a.ancho:g} --largo {a.largo:g} "
                      f"--unidad {a.unidad}")
        if a.alto_extra:
            partes.append(f"--alto-extra {a.alto_extra:g}")
    if a.tiene_grifo:
        partes.append("--tiene-grifo")
    if objeto:
        partes.append(f'--objeto "{objeto}"')
    if a.extra:
        partes.append(f'--extra "{a.extra}"')
    return " ".join(partes)


def procesar(a, foto: Path, slug: str):
    carpeta = PRUEBAS_DIR / slug
    carpeta.mkdir(parents=True, exist_ok=True)

    # entrada preparada una sola vez por producto (rotación EXIF + reescalado)
    preparada = next(iter(carpeta.glob("input.*")), None)
    if preparada is None and not a.dry_run:
        preparada, tam = image_prep.preparar_entrada(foto, carpeta / "input.png")
        print(f"  entrada preparada: {preparada.name} ({tam})")
    elif preparada is not None:
        print(f"  entrada: {preparada.name} (ya preparada)")

    if a.qc_solo:
        for f in sorted(carpeta.glob("[0-9][0-9][0-9].json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            img = carpeta / d["archivo"]
            if img.exists():
                d["qc"] = qc.analizar(img)
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"  #{d['n']:03d} {d['qc'].get('veredicto', '?')}"
                      + (f" · {'; '.join(d['qc'].get('avisos', []))}" if d["qc"].get("avisos") else ""))
        return carpeta

    # paso 0: ¿cuál de los objetos de la foto es el producto?
    objeto, frase_objeto = None, ""
    if a.objeto:
        objeto = {"nombre": a.objeto, "descripcion": a.objeto, "ubicacion": "",
                  "origen": "indicado por el usuario"}
        frase_objeto = a.objeto
        print(f"  producto (--objeto): {a.objeto}")
    elif not a.sin_analisis:
        if a.dry_run and not (carpeta / "analisis.json").exists():
            print("  [visión] (dry-run: el análisis de objetos se haría acá)")
        else:
            analisis = analizar.cargar_o_detectar(preparada or foto, carpeta, a.reanalizar)
            objeto = analizar.elegir(analisis, auto=a.auto, confirmar=a.confirmar)
            frase_objeto = analizar.frase(objeto)

    dims = medidas_cm(a)
    # Con --tiene-grifo y SIN --alto-extra no hay forma de saber cuánto del alto
    # declarado es la pieza y cuánto el grifo — así que en frontal/lateral (las
    # vistas cuya proporción usa el alto) no se fuerza ninguna relación alto/ancho:
    # forzarla con un alto que es solo el de la caja es la misma foto achatada que
    # --alto-extra vino a resolver, para cuando SÍ se declara ese dato.
    sin_alto = a.tiene_grifo and not a.alto_extra
    if dims:
        extra_txt = f" + {a.alto_extra:g} {a.unidad} de alto agregado" if a.alto_extra else (
            " (con grifo: alto sin forzar en frontal/lateral)" if sin_alto else "")
        print(f"  medidas declaradas: {a.alto:g} x {a.ancho:g} x {a.largo:g} {a.unidad} "
              f"(alto x ancho x largo){extra_txt}")

    n = siguiente_n(carpeta)
    for vista, fondo, _ in combos(a):
        size = elegir_lienzo(a, vista, dims)
        sin_alto_vista = sin_alto and vista in ("frontal", "lateral")
        texto_med = prompts.texto_medidas(vista, *dims, tiene_grifo=sin_alto_vista) if dims else ""
        prompt = prompts.construir(vista, fondo, a.extra, frase_objeto, texto_med)
        if a.dry_run:
            print(f"\n  #{n:03d} vista={vista} fondo={fondo} quality={a.quality} size={size}"
                  f"  ≈ US$ {config.costo_imagen(a.quality):.3f}")
            print(f"  prompt: {prompt}")
            n += 1
            continue

        destino = carpeta / f"{n:03d}_{vista}_{fondo}.png"
        try:
            ruta, meta = image_prep.generar_vista(preparada, destino, vista, fondo, a.extra,
                                                  size, a.quality, frase_objeto, texto_med)
        except (net.HttpError, RuntimeError) as e:
            # una variante que falla no debe tirar abajo el resto de la corrida
            print(f"  #{n:03d} FALLÓ ({vista}/{fondo}): {e}")
            n += 1
            continue
        # la silueta manda: Meshy va a copiar esta proporción a la malla
        ajuste = None
        if dims and not a.sin_correccion and not sin_alto_vista:
            objetivo_rel = proporcion.objetivo(vista, *dims)
            if objetivo_rel:
                crudo = ruta.with_name(ruta.stem + "_crudo.png")
                ruta.replace(crudo)                       # se conserva el original
                ajuste = proporcion.corregir(crudo, objetivo_rel, ruta,
                                             maximo=a.max_correccion)
                if ajuste is None or not ajuste.get("aplicado"):
                    crudo.replace(ruta)                   # se deja la imagen tal cual
                    motivo = (ajuste or {}).get("motivo", "no se pudo medir")
                    print(f"  proporción: SIN corregir — {motivo}")
                else:
                    print(f"  proporción: {ajuste['relacion_antes']} -> "
                          f"{ajuste['relacion_despues']} (objetivo "
                          f"{ajuste['relacion_objetivo']}, se ajustó el "
                          f"{ajuste['eje_ajustado']})")

        meta.update({
            "n": n, "archivo": ruta.name, "entrada": preparada.name,
            "objeto_elegido": objeto,
            "medidas_cm": {"alto": dims[0], "ancho": dims[1], "largo": dims[2]} if dims else None,
            "proporcion": ajuste,
            "foto_original": str(foto), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "comando": comando_repetir(a, foto, vista, fondo,
                                       (objeto or {}).get("nombre", "")),
            "qc": qc.analizar(ruta),
        })
        (carpeta / f"{n:03d}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        avisos = meta["qc"].get("avisos", [])
        print(f"  #{n:03d} {ruta.name} · QC {meta['qc'].get('veredicto', 'n/d')}"
              + ("\n        - " + "\n        - ".join(avisos) if avisos else ""))
        n += 1

    if a.vistas_extra and not a.dry_run:
        base = elegir_base(carpeta, a.desde)
        if base is None:
            print("  [beta] no hay ningún intento para usar de base")
        else:
            print(f"  [beta] 3 vistas giradas a partir de {base.name}")
            dir_vistas = carpeta / f"views_{base.stem.split('_')[0]}"
            _, metas = image_prep.vistas_extra(base, dir_vistas, a.extra, a.fondo[0],
                                               a.size, a.quality)
            (dir_vistas / "meta.json").write_text(
                json.dumps(metas, indent=2, ensure_ascii=False), encoding="utf-8")
            for f in sorted(dir_vistas.glob("*.png")):
                v = qc.analizar(f)
                print(f"        {f.name} · QC {v.get('veredicto', 'n/d')}")

    return carpeta


def elegir_base(carpeta: Path, numero=0):
    imgs = sorted(carpeta.glob("[0-9][0-9][0-9]_*.png"))
    if not imgs:
        return None
    if numero:
        elegidas = [p for p in imgs if p.name.startswith(f"{numero:03d}_")]
        return elegidas[0] if elegidas else None
    return imgs[-1]


def main(argv=None):
    a = parsear(argv)
    fotos = entradas(a.imagen, a.nombre)
    if len(fotos) > 1 and a.nombre:
        sys.exit("--nombre no se puede usar con una carpeta (cada foto define su propio nombre)")

    por_foto = len(combos(a))
    total_imgs = por_foto * len(fotos) + (3 * len(fotos) if a.vistas_extra else 0)
    print(f"{len(fotos)} producto(s) × {por_foto} variante(s)"
          f"{' + 3 vistas BETA' if a.vistas_extra else ''} = {total_imgs} imagen(es)")
    print(f"Modelo {config.OPENAI_IMAGE_MODEL} · quality {a.quality} · size {a.size}"
          f" ≈ US$ {total_imgs * config.costo_imagen(a.quality):.2f}")

    carpetas = []
    for foto, slug in fotos:
        print(f"\n== {foto.name} -> {slug} ==")
        carpetas.append(procesar(a, foto, slug))

    if a.dry_run:
        print("\n(dry-run: no se llamó a ninguna API)")
        return 0

    for carpeta in dict.fromkeys(carpetas):
        hoja = preview.hoja_contacto(carpeta, a.nombre or carpeta.name)
    indice = preview.hoja_indice(PRUEBAS_DIR)
    print(f"\nHoja de contacto: {hoja}")
    print(f"Índice:           {indice}")
    if a.abrir:
        webbrowser.open(hoja.as_uri())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except config.FaltaClave as e:
        sys.exit(f"\nERROR: {e}")
    except (net.HttpError, RuntimeError, ValueError) as e:
        sys.exit(f"\nERROR: {e}")
