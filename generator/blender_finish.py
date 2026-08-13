"""
PASO 4 (opcional) — Verificación independiente en Blender + USDZ + previews.

Se ejecuta DENTRO de Blender, no con el Python del sistema:
  "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" --background \
      --python generator/blender_finish.py -- --glb jobs/x/model.glb --out jobs/x

Qué hace (y qué NO):
  - MIDE el GLB ya escalado con el importador de Blender y compara contra las medidas
    declaradas. Es una segunda opinión sobre gltf_scale.py: si las dos coinciden, la
    escala métrica es correcta.
  - Exporta USDZ para AR Quick Look de iOS (opcional: model-viewer también lo genera
    al vuelo, pero un USDZ propio da mejor control).
  - Renderiza 4 vistas de control (frente/derecha/atrás/izquierda).
  - NO reescala ni reposiciona nada: si la medición no cuadra, lo reporta y sale con
    código 1. La corrección se hace en gltf_scale.py, no acá.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def argumentos():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--glb", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--alto-m", type=float, default=0.0, help="alto esperado en METROS")
    p.add_argument("--ancho-m", type=float, default=0.0)
    p.add_argument("--largo-m", type=float, default=0.0)
    p.add_argument("--tolerancia", type=float, default=1.0, help="% de desvío admitido")
    p.add_argument("--usdz", action="store_true")
    p.add_argument("--previews", type=int, default=4)
    p.add_argument("--size", type=int, default=512)
    return p.parse_args(argv)


def escena_limpia():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def importar(ruta):
    for op in ("import_scene.gltf", "wm.gltf_import"):
        modulo, nombre = op.split(".")
        fn = getattr(getattr(bpy.ops, modulo), nombre, None)
        if fn is None:
            continue
        try:
            fn(filepath=str(ruta))
            return
        except (RuntimeError, TypeError) as e:
            print(f"[blender] {op} falló: {e}")
    raise RuntimeError("No se pudo importar el GLB con ningún operador conocido")


def bbox_mundo():
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for esquina in obj.bound_box:
            p = obj.matrix_world @ Vector(esquina)
            for e in range(3):
                lo[e] = min(lo[e], p[e])
                hi[e] = max(hi[e], p[e])
    if math.isinf(lo.x):
        raise RuntimeError("No hay mallas en el GLB importado")
    return lo, hi


def preparar_render(size):
    escena = bpy.context.scene
    for motor in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            escena.render.engine = motor
            break
        except TypeError:
            continue
    escena.render.resolution_x = escena.render.resolution_y = size
    escena.render.film_transparent = False
    # Con 'Standard' un producto blanco se quema y la preview sale en blanco: AgX
    # comprime las altas luces y deja ver la forma.
    for tm in ("AgX", "Filmic", "Standard"):
        try:
            escena.view_settings.view_transform = tm
            break
        except TypeError:
            continue

    # Gris medio: contrasta tanto con productos blancos como con oscuros.
    mundo = bpy.data.worlds.new("fondo")
    escena.world = mundo
    mundo.use_nodes = True
    fondo = mundo.node_tree.nodes["Background"]
    fondo.inputs[0].default_value = (0.25, 0.25, 0.27, 1.0)
    fondo.inputs[1].default_value = 1.0

    for nombre, energia, rot in (("sol", 2.0, 35), ("relleno", 0.8, -120)):
        luz = bpy.data.objects.new(nombre, bpy.data.lights.new(nombre, type="SUN"))
        luz.data.energy = energia
        luz.data.angle = math.radians(15)          # sombras suaves
        luz.rotation_euler = (math.radians(50), 0, math.radians(rot))
        escena.collection.objects.link(luz)


def renderizar_vistas(centro, tam, out: Path, cantidad, size):
    preparar_render(size)
    escena = bpy.context.scene

    objetivo = bpy.data.objects.new("mira", None)
    escena.collection.objects.link(objetivo)
    objetivo.location = centro

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    escena.collection.objects.link(cam)
    escena.camera = cam
    cam.data.lens = 50
    seguir = cam.constraints.new(type="TRACK_TO")
    seguir.target = objetivo
    seguir.track_axis = "TRACK_NEGATIVE_Z"
    seguir.up_axis = "UP_Y"

    # Distancia calculada desde el FOV real de la cámara (render cuadrado): el lado más
    # grande del objeto entra completo con ~30% de aire. Un factor fijo recorta productos
    # altos como una vitrina.
    fov = 2.0 * math.atan((cam.data.sensor_width / 2.0) / cam.data.lens)
    dist = max((tam / 2.0) / math.tan(fov / 2.0) * 1.3, 0.05)
    elev = math.radians(20)
    generadas = []
    nombres = ["frente", "derecha", "atras", "izquierda"]
    for i in range(cantidad):
        az = math.radians(360.0 * i / max(cantidad, 1))
        cam.location = centro + Vector((
            dist * math.cos(elev) * math.sin(az),
            -dist * math.cos(elev) * math.cos(az),
            dist * math.sin(elev),
        ))
        destino = out / f"vista_{nombres[i] if i < len(nombres) else i}.png"
        escena.render.filepath = str(destino)
        bpy.ops.render.render(write_still=True)
        generadas.append(destino.name)
        print(f"[blender] render -> {destino.name}")
    return generadas


def exportar_usdz(destino: Path):
    # Blender 5.1: export_textures_mode='NEW' empaqueta las texturas dentro del .usdz y
    # generate_preview_surface=True es lo que AR Quick Look necesita para ver el material.
    intentos = [
        dict(filepath=str(destino), export_textures_mode="NEW", generate_preview_surface=True,
             export_materials=True, relative_paths=False, usdz_downscale_size="2048",
             evaluation_mode="RENDER"),
        dict(filepath=str(destino), export_textures=True, relative_paths=False),  # Blender 4.x
        dict(filepath=str(destino)),
    ]
    for kw in intentos:
        try:
            bpy.ops.wm.usd_export(**kw)
            print(f"[blender] USDZ -> {destino.name}")
            return destino.name
        except (RuntimeError, TypeError) as e:
            print(f"[blender] usd_export {list(kw)} falló: {e}")
    print("[blender] sin USDZ: model-viewer lo generará al vuelo para iOS")
    return None


def main():
    a = argumentos()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    escena_limpia()
    importar(Path(a.glb))
    lo, hi = bbox_mundo()
    # Blender importa glTF como Z-up: alto = Z, ancho = X, largo = Y
    medido = {"ancho": hi.x - lo.x, "largo": hi.y - lo.y, "alto": hi.z - lo.z}
    informe = {"medido_blender_m": {k: round(v, 6) for k, v in medido.items()},
               "base_z_m": round(lo.z, 6)}

    ok = True
    esperado = {k: v for k, v in
                (("alto", a.alto_m), ("ancho", a.ancho_m), ("largo", a.largo_m)) if v > 0}
    if esperado:
        informe["esperado_m"] = esperado
        informe["error_pct"] = {}
        for clave, valor in esperado.items():
            err = 100.0 * (medido[clave] - valor) / valor
            informe["error_pct"][clave] = round(err, 2)
            if abs(err) > a.tolerancia:
                ok = False
        if abs(lo.z) > 1e-4:
            informe["aviso"] = f"la base no está en Z=0 (z_min={lo.z:.4f} m)"

    if a.usdz:
        informe["usdz"] = exportar_usdz(out / "model.usdz")
    if a.previews:
        centro = (lo + hi) / 2.0
        informe["previews"] = renderizar_vistas(centro, max(max(hi - lo), 1e-4),
                                                out, a.previews, a.size)

    (out / "blender_check.json").write_text(
        json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[blender] " + json.dumps(informe, ensure_ascii=False))
    if not ok:
        print("[blender] ERROR: la medición de Blender no coincide con la esperada "
              "(las dos implementaciones discrepan: revisar gltf_scale.py)")
        sys.exit(1)


if __name__ == "__main__":
    main()
