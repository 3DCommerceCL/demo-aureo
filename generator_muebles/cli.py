r"""
Generador de modelos AR especializado en MUEBLES.

Es una capa de política sobre `generator`: no reimplementa nada del pipeline, decide los
parámetros correctos para un mueble y delega. Lo que aporta:

  · Las medidas son OBLIGATORIAS. Un mueble siempre trae alto x ancho x fondo de catálogo,
    y con eso el paso 1 corrige la proporción de la silueta y el paso 4 escala en modo
    exacto (con la frontal ya corregida, la deformación es ~2%: imperceptible).
  · La categoría se detecta sola mirando la foto, en la MISMA llamada de visión que ya
    elegía cuál de los objetos es el producto (no cuesta una llamada extra).
  · Cada categoría define cómo se coloca en AR: una repisa o una alacena van a la PARED,
    no al piso, y eso queda en el ar-placement del visor.

  py -m generator_muebles.cli --imagen "https://tienda.cl/vitrina.jpg" `
      --nombre "vitrina nordica" --alto 160 --ancho 80 --largo 40

  py -m generator_muebles.cli --imagen fotos\repisa.jpg --nombre repisa `
      --alto 30 --ancho 90 --largo 22 --categoria repisa --solo-imagen
"""

import argparse
import json
import sys
from pathlib import Path

from generator import config, gltf_scale, imagen as base_imagen, image_prep, net
from generator import analizar, pipeline as base_pipeline, preview, prompts

from . import categorias, vision


def parsear(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--imagen", required=True, help="foto del mueble: archivo o URL")
    p.add_argument("--nombre", default="", help="nombre del producto")
    p.add_argument("--alto", type=float, required=True, help="medida de catálogo")
    p.add_argument("--ancho", type=float, required=True)
    p.add_argument("--largo", type=float, required=True, help="fondo/profundidad")
    p.add_argument("--alto-extra", type=float, default=0.0,
                   help="alto de algo por encima del mueble (p. ej. un grifo sobre un "
                        "mueble de cocina) que no cuenta como parte de --alto")
    p.add_argument("--unidad", default="cm", choices=sorted(gltf_scale.UNIDADES_M))
    p.add_argument("--categoria", default="", choices=[""] + categorias.lista(),
                   help="si no se indica, se detecta mirando la foto")
    p.add_argument("--objeto", default="", help="qué aislar (saltea la pregunta)")
    p.add_argument("--auto", action="store_true", help="no preguntar cuál es el producto")
    p.add_argument("--confirmar", action="store_true", help="preguntar siempre cuál es")
    p.add_argument("--vista", default="", choices=[""] + sorted(prompts.VISTAS),
                   help="pisa el default de la categoría")
    p.add_argument("--fondo", default="", choices=[""] + sorted(prompts.FONDOS),
                   help="pisa el default de la categoría")
    p.add_argument("--quality", default=config.OPENAI_IMAGE_QUALITY,
                   choices=["low", "medium", "high"])
    p.add_argument("--solo-imagen", action="store_true",
                   help="quedarse en el paso 1 (no gasta créditos de Meshy)")
    p.add_argument("--reanalizar", action="store_true", help="rehacer el análisis de visión")
    p.add_argument("--si", action="store_true", help="no preguntar antes de gastar créditos")
    p.add_argument("--abrir", action="store_true", help="abrir la hoja de contacto")
    return p.parse_args(argv)


def resolver(a):
    """Paso 0: producto + categoría + defaults. Devuelve (foto, slug, carpeta, plan)."""
    fotos = base_imagen.entradas(a.imagen, a.nombre)
    if len(fotos) != 1:
        sys.exit("este CLI procesa un mueble por vez (para lotes va el paso de catálogo)")
    foto, slug = fotos[0]
    carpeta = base_imagen.PRUEBAS_DIR / slug

    preparada = next(iter(carpeta.glob("input.*")), None)
    if preparada is None:
        preparada, tam = image_prep.preparar_entrada(foto, carpeta / "input.png")
        print(f"  entrada preparada: {preparada.name} ({tam})")

    analisis = vision.analizar_mueble(preparada, carpeta, a.reanalizar)
    if analisis.get("es_mueble") is False:
        print("  AVISO: la visión dice que el producto principal NO es un mueble. "
              "Seguimos igual, pero revisá la foto o pasá --objeto.")

    objeto = analizar.elegir(analisis, forzado=a.objeto, auto=a.auto, confirmar=a.confirmar)
    categoria, origen, confianza = vision.resolver_categoria(analisis, a.categoria)
    perfil = categorias.perfil(categoria)
    if a.vista:
        perfil["vista"] = a.vista
    if a.fondo:
        perfil["fondo"] = a.fondo

    print(f"\n  Categoría: {categoria} ({origen}, confianza {confianza:.0%})")
    print(f"  Colocación en AR: {perfil['colocacion']} -> ar-placement=\"{perfil['ar_placement']}\"")
    extra_txt = f" + {a.alto_extra:g} {a.unidad} de alto agregado" if a.alto_extra else ""
    print(f"  Vista {perfil['vista']} · fondo {perfil['fondo']} · escala {perfil['modo']} "
          f"· medidas {a.alto:g} x {a.ancho:g} x {a.largo:g} {a.unidad}{extra_txt}")
    if perfil["nota"]:
        print(f"  Nota de categoría: {perfil['nota']}")
    return foto, slug, carpeta, objeto, perfil


def argv_imagen(a, foto, objeto, perfil):
    argv = ["--imagen", str(foto),
            "--vista", perfil["vista"], "--fondo", perfil["fondo"],
            "--alto", f"{a.alto:g}", "--ancho", f"{a.ancho:g}", "--largo", f"{a.largo:g}",
            "--alto-extra", f"{a.alto_extra:g}",
            "--unidad", a.unidad, "--quality", a.quality]
    if a.nombre:
        argv += ["--nombre", a.nombre]
    if objeto:
        # ya resolvimos el objeto: que la base no vuelva a preguntar ni a pagar visión
        argv += ["--objeto", analizar.frase(objeto)]
    if perfil["nota"]:
        argv += ["--extra", perfil["nota"]]
    if a.abrir:
        argv += ["--abrir"]
    return argv


def ultima_imagen(carpeta: Path):
    imgs = [p for p in carpeta.glob("[0-9][0-9][0-9]_*.png") if "_crudo" not in p.name]
    return max(imgs, key=lambda p: p.name) if imgs else None


def anotar_job(slug, perfil, categoria_origen):
    """Deja la categoría y el ar-placement en el job y regenera la página de control."""
    job = config.JOBS_DIR / slug
    f = job / "job.json"
    if not f.exists():
        return None
    meta = json.loads(f.read_text(encoding="utf-8"))
    meta["mueble"] = {"categoria": perfil["categoria"], "origen": categoria_origen,
                      "colocacion": perfil["colocacion"], "nota": perfil["nota"]}
    meta["ar_placement"] = perfil["ar_placement"]
    f.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return preview.escribir(job, meta)


def main(argv=None):
    a = parsear(argv)
    foto, slug, carpeta, objeto, perfil = resolver(a)

    print("\n== paso 1: imagen ==")
    if base_imagen.main(argv_imagen(a, foto, objeto, perfil)) != 0:
        return 1
    limpia = ultima_imagen(carpeta)
    if limpia is None:
        sys.exit("no se generó ninguna imagen limpia")
    print(f"  imagen para Meshy: {limpia.name}")

    if a.solo_imagen:
        print("\n(--solo-imagen: no se tocó Meshy, no se gastaron créditos)")
        return 0

    print("\n== pasos 2-6: modelo 3D ==")
    argv_pl = ["--imagen", str(foto), "--alto", f"{a.alto:g}", "--ancho", f"{a.ancho:g}",
               "--largo", f"{a.largo:g}", "--alto-extra", f"{a.alto_extra:g}",
               "--unidad", a.unidad,
               "--imagen-limpia", str(limpia), "--modo", perfil["modo"],
               "--blender", "--web"]
    if a.nombre:
        argv_pl += ["--nombre", a.nombre]
    if a.si:
        argv_pl += ["--si"]
    if base_pipeline.main(argv_pl) != 0:
        return 1

    pagina = anotar_job(slug, perfil, "perfil muebles")
    if pagina:
        print(f"\n  categoría y ar-placement anotados · control: {pagina}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except config.FaltaClave as e:
        sys.exit(f"\nERROR: {e}")
    except (net.HttpError, RuntimeError, ValueError) as e:
        sys.exit(f"\nERROR: {e}")
