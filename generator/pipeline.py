"""
Orquestador: foto de producto + medidas -> GLB métrico listo para visores AR.

  foto ──(1 OpenAI)──> front.png (fondo blanco, vista frontal)
        └(2 OpenAI beta)─> views/{derecha,trasera,izquierda}.png
                            └──(3 Meshy)──> raw.glb (texturizado, sin escala real)
                                             └──(4 gltf_scale)──> model.glb (metros, base en Y=0)
                                                                   ├─(5 Blender, opc.)─> USDZ + previews
                                                                   └─(6 gltf-transform, opc.)─> model_web.glb

Todo vive en generator/jobs/<slug>/ y el estado queda en job.json: si un paso ya se
hizo, no se repite (no se vuelven a gastar créditos). --forzar rehace desde el paso dado.

  py -m generator.pipeline --imagen fotos/silla.jpg --alto 82 --ancho 45 --largo 50 --nombre silla-nordica
  py -m generator.pipeline --imagen fotos/silla.jpg --alto 82 --ancho 45 --largo 50 --vistas 4 --blender
  py -m generator.pipeline --imagen fotos/silla.jpg --alto 82 --ancho 45 --largo 50 --dry-run
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, gltf_scale, image_prep, meshy, net, preview, prompts

PASOS = ["imagen", "vistas", "malla", "escala", "blender", "web"]


def slugificar(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", texto.lower().strip())
    return s.strip("-") or "modelo"


def parsear(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--imagen", required=True, help="foto del producto (jpg/png/webp)")
    p.add_argument("--alto", type=float, required=True)
    p.add_argument("--ancho", type=float, required=True)
    p.add_argument("--largo", type=float, required=True)
    p.add_argument("--alto-extra", type=float, default=0.0,
                   help="alto de algo por encima de la pieza (p. ej. un grifo sobre un "
                        "mueble de cocina) que SÍ sale en la foto/malla pero no cuenta "
                        "como parte de --alto")
    p.add_argument("--tiene-grifo", action="store_true",
                   help="la pieza tiene un grifo/accesorio montado de alto desconocido. "
                        "Sin --alto-extra, --modo se ignora y se escala en modo 'ancho': "
                        "el alto (caja + grifo) queda librado a lo que midan ancho/largo, "
                        "en vez de forzarlo a --alto (que es solo la caja) y achatar todo.")
    p.add_argument("--unidad", default="cm", choices=sorted(gltf_scale.UNIDADES_M))
    p.add_argument("--nombre", default="", help="nombre del producto (define la carpeta del job)")
    p.add_argument("--vistas", type=int, default=1, choices=[1, 4],
                   help="1 = solo frontal; 4 = frontal + 3 vistas inventadas (BETA)")
    p.add_argument("--imagen-limpia", nargs="+", default=[],
                   help="imágenes ya aprobadas en generator/pruebas/ (saltea el paso 1). "
                        "Con 2 a 4 se usa multi-image-to-3d: frontal + lateral le dan a "
                        "Meshy el ancho Y la profundidad reales")
    p.add_argument("--vista", default="frontal", choices=sorted(prompts.VISTAS),
                   help="encuadre de la imagen limpia (paso 1)")
    p.add_argument("--fondo", default="blanco", choices=sorted(prompts.FONDOS),
                   help="fondo de la imagen limpia (paso 1)")
    p.add_argument("--modo", default="uniforme",
                   choices=["uniforme", "exacto", "alto", "ancho", "largo"],
                   help="ajuste de escala: uniforme no deforma; exacto calza los 3 ejes deformando")
    p.add_argument("--pbr", action="store_true", help="pide mapas PBR a Meshy (más pesado)")
    p.add_argument("--polycount", type=int, default=config.MESHY_POLYCOUNT)
    p.add_argument("--prompt-textura", default="", help="guía de textura para Meshy (máx 600)")
    p.add_argument("--prompt-extra", default="", help="instrucción extra para la limpieza de imagen")
    p.add_argument("--blender", action="store_true", help="verificación + USDZ + previews en Blender")
    p.add_argument("--web", action="store_true",
                   help="model_web.glb comprimido con @gltf-transform/cli (necesita Node/npx)")
    p.add_argument("--sin-ejes-auto", action="store_true",
                   help="no permutar ancho/largo aunque la malla sugiera lo contrario")
    p.add_argument("--forzar", default="", choices=[""] + PASOS,
                   help="rehacer desde este paso (borra sus salidas)")
    p.add_argument("--si", action="store_true",
                   help="no preguntar antes de gastar los créditos de Meshy")
    p.add_argument("--dry-run", action="store_true", help="muestra el plan y el costo, no llama a nada")
    return p.parse_args(argv)


def cargar_estado(job: Path) -> dict:
    f = job / "job.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def guardar_estado(job: Path, meta: dict):
    (job / "job.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def limpiar_desde(job: Path, paso: str):
    """Borra las salidas del paso indicado en adelante para poder rehacerlas."""
    salidas = {
        "imagen": ["front.png"],
        "vistas": ["views"],
        "malla": ["raw.glb", "meshy_task.json"],
        "escala": ["model.glb"],
        "blender": ["model.usdz", "blender_check.json", "vista_frente.png", "vista_derecha.png",
                    "vista_atras.png", "vista_izquierda.png"],
        "web": ["model_web.glb"],
    }
    for siguiente in PASOS[PASOS.index(paso):]:
        for nombre in salidas[siguiente]:
            ruta = job / nombre
            if ruta.is_dir():
                shutil.rmtree(ruta)
            elif ruta.exists():
                ruta.unlink()


def plan(a, job: Path):
    imgs = (0 if a.imagen_limpia else 1) + (3 if a.vistas == 4 else 0)
    entran = len(a.imagen_limpia) if a.imagen_limpia else 1 + (3 if a.vistas == 4 else 0)
    usd = config.costo_imagen() * imgs
    extra_txt = f" + {a.alto_extra:g} {a.unidad} de alto agregado" if a.alto_extra else (
        " (tiene grifo: alto sin forzar)" if a.tiene_grifo else "")
    print(f"Job:      {job}")
    print(f"Producto: {a.alto} x {a.ancho} x {a.largo} {a.unidad} (alto x ancho x largo)"
          f"{extra_txt}")
    if a.imagen_limpia:
        print(f"Paso 1:   {len(a.imagen_limpia)} imagen(es) ya aprobada(s) "
              f"({', '.join(Path(r).name for r in a.imagen_limpia)}) — US$ 0")
    print(f"Paso 1-2: {imgs} imagen(es) con {config.OPENAI_IMAGE_MODEL} "
          f"({config.OPENAI_IMAGE_QUALITY}) ≈ US$ {usd:.2f}")
    print(f"Paso 3:   Meshy {config.MESHY_AI_MODEL} "
          f"{'multi-image-to-3d' if entran > 1 else 'image-to-3d'} = 30 créditos")
    print(f"Paso 4:   escala {a.modo} -> metros, base en Y=0")
    print(f"Paso 5:   {'Blender: verificación + USDZ + 4 previews' if a.blender else '(omitido)'}")
    print(f"Paso 6:   {'gltf-transform: model_web.glb' if a.web else '(omitido)'}")


def confirmar_gasto(a, cuantas_imagenes):
    """Pregunta antes de crear una tarea nueva de Meshy (lo único caro del pipeline).

    Solo pregunta en terminal interactiva: en background o en un script seguiría colgado
    esperando una respuesta que nadie va a escribir.
    """
    if a.si or not sys.stdin.isatty():
        return True
    print(f"\n  Se va a crear UNA tarea de Meshy con {cuantas_imagenes} imagen(es): "
          f"30 créditos (~US$0.60 en el plan Pro).")
    print("  Los pasos 4-6 (escala, Blender, web) son gratis y se pueden repetir "
          "cuantas veces quieras.")
    return input("  ¿Seguir? [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")


def optimizar_web(job: Path, meta: dict):
    """model.glb -> model_web.glb con @gltf-transform/cli (necesita Node/npx).

    Solo transforms que NO tocan la escala ni la posición: texturas más chicas + webp,
    geometría con draco, prune/dedup. Prohibido center/flatten/join (moverían el modelo).
    """
    origen, destino = job / "model.glb", job / "model_web.glb"
    base = ["npx", "--yes", "@gltf-transform/cli", "optimize", str(origen), str(destino),
            "--compress", "draco", "--texture-size", "1024",
            "--simplify", "false", "--join", "false", "--flatten", "false"]
    # La conversión a webp depende de sharp/libvips y falla con ciertos perfiles de color
    # ("colourspace: parameter space not set"); si pasa, se comprime solo la geometría.
    intentos = [base + ["--texture-compress", "webp"],
                base + ["--texture-compress", "false"]]
    res = None
    for i, cmd in enumerate(intentos):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 shell=(sys.platform == "win32"))
        except FileNotFoundError:
            print("  [web] npx no está disponible: se omite la optimización")
            return None
        if res.returncode == 0 and destino.exists():
            break
        detalle = (res.stderr or res.stdout or "").strip().splitlines()
        if i == 0:
            print("  [web] falló la compresión de texturas"
                  + (f" ({detalle[-1].strip()})" if detalle else "")
                  + "; reintento solo con draco")
    if res is None or res.returncode != 0 or not destino.exists():
        print("  [web] gltf-transform falló; se sigue con model.glb sin optimizar")
        detalle = (res.stderr or res.stdout or "").strip().splitlines() if res else []
        if detalle:
            print("  [web] " + detalle[-1])
        return None
    medido = gltf_scale.dimensiones(gltf_scale.leer_glb(destino)[0])
    info = {"archivo": "model_web.glb",
            "mb": round(destino.stat().st_size / 1e6, 2),
            "mb_original": round(origen.stat().st_size / 1e6, 2),
            "dimensiones_m": [round(v, 6) for v in medido]}
    print(f"  [web] model_web.glb {info['mb_original']} MB -> {info['mb']} MB "
          f"(dimensiones intactas: {info['dimensiones_m']})")
    return info


def correr_blender(job: Path, meta: dict):
    exe = Path(config.BLENDER_EXE)
    if not exe.exists():
        print(f"  [blender] no encontrado en {exe} — definí BLENDER_EXE en generator/.env")
        return None
    # Se verifica contra lo que gltf_scale dice que MIDE el archivo, no contra las medidas
    # declaradas: en modo uniforme el desvío respecto de lo declarado es intencional, y lo
    # que Blender tiene que confirmar es que el escalador hizo lo que dijo.
    esperado = meta["escala"]["medido_m"]
    cmd = [str(exe), "--background", "--python",
           str(Path(__file__).with_name("blender_finish.py")), "--",
           "--glb", str(job / "model.glb"), "--out", str(job),
           "--alto-m", str(esperado["alto"]), "--ancho-m", str(esperado["ancho"]),
           "--largo-m", str(esperado["largo"]), "--usdz", "--previews", "4"]
    print("  [blender] verificando medidas y exportando…")
    res = subprocess.run(cmd, capture_output=True, text=True)
    for linea in (res.stdout or "").splitlines():
        if linea.startswith("[blender]"):
            print("  " + linea)
    chequeo = job / "blender_check.json"
    informe = json.loads(chequeo.read_text(encoding="utf-8")) if chequeo.exists() else {}
    if res.returncode != 0:
        print(f"  [blender] terminó con código {res.returncode} — revisá blender_check.json")
        if res.stderr:
            print("  " + (res.stderr.strip().splitlines() or [""])[-1])
    return informe


def main(argv=None):
    a = parsear(argv)
    entrada = Path(a.imagen).expanduser().resolve()
    if not entrada.exists():
        sys.exit(f"No existe la imagen: {entrada}")

    slug = slugificar(a.nombre or entrada.stem)
    job = config.JOBS_DIR / slug
    plan(a, job)
    if a.dry_run:
        print("\n(dry-run: no se llamó a ninguna API)")
        return 0

    job.mkdir(parents=True, exist_ok=True)
    if a.forzar:
        limpiar_desde(job, a.forzar)

    meta = cargar_estado(job)
    meta.update({
        "nombre": a.nombre or slug,
        "slug": slug,
        "medidas": {"alto": a.alto, "ancho": a.ancho, "largo": a.largo, "unidad": a.unidad,
                    "alto_extra": a.alto_extra, "tiene_grifo": a.tiene_grifo},
        "vistas": a.vistas,
        "ext_entrada": entrada.suffix.lower(),
        "modelos": {"imagen": config.OPENAI_IMAGE_MODEL, "malla": config.MESHY_AI_MODEL},
        "actualizado": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    copia = next(iter(job.glob("input.*")), None)
    if copia is None:
        copia, _ = image_prep.preparar_entrada(entrada, job / "input.png")
    guardar_estado(job, meta)
    t0 = time.time()

    # -- 1. limpieza de la foto -> imagen limpia para Meshy
    frontal = job / "front.png"
    aprobadas = []
    if a.imagen_limpia and not frontal.exists():
        # imágenes ya aprobadas en la iteración del paso 1 (py -m generator.imagen)
        for i, ruta in enumerate(a.imagen_limpia):
            origen = Path(ruta).expanduser().resolve()
            destino = frontal if i == 0 else job / f"vista_{i}{origen.suffix.lower()}"
            shutil.copy2(origen, destino)
            aprobadas.append(destino)
        meta["imagen_limpia_origen"] = [str(r) for r in a.imagen_limpia]
        print(f"  [1/6] usando {len(aprobadas)} imagen(es) aprobada(s): "
              f"{', '.join(Path(r).name for r in a.imagen_limpia)}")
    elif frontal.exists():
        print("  [1/6] front.png ya existe, se reutiliza")
    else:
        print("  [1/6] limpiando la foto…")
        medidas = prompts.texto_medidas(
            a.vista, *[v * gltf_scale.UNIDADES_M[a.unidad] * 100 for v in
                       (a.alto, a.ancho, a.largo)])
        _, meta["imagen"] = image_prep.generar_vista(copia, frontal, a.vista, a.fondo,
                                                     a.prompt_extra, objeto="",
                                                     medidas=medidas)

    # -- 2. vistas extra (BETA)
    imagenes = aprobadas or [frontal]
    if a.vistas == 4:
        print("  [2/6] generando 3 vistas sintéticas (BETA)…")
        extra, _ = image_prep.vistas_extra(frontal, job / "views", a.prompt_extra, a.fondo)
        imagenes += extra
    else:
        print("  [2/6] vistas extra desactivadas (--vistas 4 para probarlas)")

    # -- 3. Meshy: imagen(es) -> malla texturizada
    crudo = job / "raw.glb"
    pendiente = meta.get("meshy") or {}
    if crudo.exists():
        print("  [3/6] raw.glb ya existe, se reutiliza (no se gastan créditos)")
    elif pendiente.get("task_id"):
        # el proceso anterior murió esperando: la tarea YA está pagada, se retoma
        print("  [3/6] hay una tarea de Meshy sin terminar…")
        meta["meshy"] = meshy.retomar(pendiente["task_id"], pendiente["endpoint"], crudo,
                                      registro=job / "meshy_task.json")
        guardar_estado(job, meta)
    else:
        if not confirmar_gasto(a, len(imagenes)):
            print("  [3/6] cancelado por el usuario (no se gastaron créditos)")
            return 1
        print("  [3/6] generando la malla en Meshy…")

        def anotar(task_id, endpoint):
            meta["meshy"] = {"task_id": task_id, "endpoint": endpoint, "estado": "en curso"}
            guardar_estado(job, meta)

        meta["meshy"] = meshy.generar(
            imagenes, crudo, registro=job / "meshy_task.json", al_crear=anotar,
            texture_prompt=a.prompt_textura or None, pbr=a.pbr, polycount=a.polycount)
        guardar_estado(job, meta)

    # -- 4. escala métrica
    print("  [4/6] escalando a las medidas declaradas…")
    modelo = job / "model.glb"
    # Con --tiene-grifo y sin --alto-extra, --alto es solo la caja: no hay un alto
    # total confiable para escalar contra él, en ningún modo (ni "exacto" ni "alto").
    # "ancho" sí sirve de referencia — un grifo no le agrega ancho a nada — así que
    # se fuerza ese modo pase lo que pase con --modo: escalar uniformemente por el
    # ancho deja que el alto (caja + grifo) salga de lo que la malla ya trae, en vez
    # de forzarlo a un número que no incluye el grifo y achatar todo para que quepa.
    sin_alto = a.tiene_grifo and not a.alto_extra
    modo_efectivo = "ancho" if sin_alto else a.modo
    if sin_alto and a.modo != modo_efectivo:
        print(f"        tiene grifo, sin alto-extra: modo '{a.modo}' -> 'ancho' "
              f"(el alto sale de la malla, no se fuerza)")
    meta["escala"] = gltf_scale.escalar(
        crudo, modelo, a.alto, a.ancho, a.largo, a.unidad,
        modo=modo_efectivo, ejes_auto=not a.sin_ejes_auto, anclar=True, alto_extra=a.alto_extra)
    meta["archivo"] = {"glb": "model.glb", "mb": round(modelo.stat().st_size / 1e6, 2)}
    if meta["escala"]["ejes_permutados"]:
        print("        ancho/largo permutados: la malla venía girada 90° respecto a lo declarado")
    peor = 0.0
    for eje, err in meta["escala"]["error_pct"].items():
        marca = "ok" if abs(err) <= 1 else ("REVISAR" if abs(err) <= 5 else "MAL")
        peor = max(peor, abs(err))
        print(f"        {eje:6s} {meta['escala']['medido_m'][eje]:.4f} m  ({err:+.2f}%) {marca}")
    if peor > 5 and a.modo == "uniforme":
        print(f"        AVISO: la malla no tiene las proporciones declaradas (hasta {peor:.0f}% de "
              f"desvío).\n        Suele significar foto en perspectiva o medidas mal tomadas. "
              f"Opciones: rehacer\n        la foto (--forzar imagen), o --modo exacto para calzar "
              f"los 3 ejes deformando el modelo.")
    guardar_estado(job, meta)

    # -- 5. Blender: segunda medición + USDZ + previews
    if a.blender:
        print("  [5/6] verificación en Blender…")
        meta["blender"] = correr_blender(job, meta)
    else:
        print("  [5/6] Blender omitido (--blender para USDZ + previews)")

    # -- 6. versión liviana para servir en la web
    if a.web:
        print("  [6/6] optimizando para web…")
        meta["web"] = optimizar_web(job, meta)
    else:
        print("  [6/6] optimización web omitida (--web)")
    meta["segundos"] = round(time.time() - t0, 1)
    guardar_estado(job, meta)

    pagina = preview.escribir(job, meta)
    print(f"\nListo en {meta['segundos']}s")
    print(f"  modelo:  {modelo}")
    print(f"  control: {pagina}")
    print(f"  AR:      servilo por HTTPS y abrí preview.html en el móvil "
          f"(py -m http.server desde {job.parent})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except config.FaltaClave as e:
        sys.exit(f"\nERROR: {e}")
    except (net.HttpError, RuntimeError, ValueError, TimeoutError) as e:
        sys.exit(f"\nERROR: {e}\n(el job quedó guardado: volvé a correr el mismo comando "
                 f"y retoma donde falló)")
