"""
PASO 2 — Imagen(es) -> malla texturizada con la API de Meshy.

  1 imagen  -> POST /openapi/v1/image-to-3d        (30 créditos)
  2-4 img   -> POST /openapi/v1/multi-image-to-3d  (30 créditos, mismo objeto en ángulos distintos)

Las imágenes van como data URI base64: no hace falta hostearlas en ningún lado.
El GLB que devuelve NO es métrico (viene normalizado) — la escala real la pone gltf_scale.py.
"""

import base64
import json
import mimetypes
import time
from pathlib import Path

from . import config, net

TERMINALES = {"SUCCEEDED", "FAILED", "CANCELED"}


def _cabeceras():
    clave = config.exigir(config.MESHY_API_KEY, "MESHY_API_KEY")
    return {"Authorization": f"Bearer {clave}"}


def _data_uri(ruta: Path) -> str:
    tipo = mimetypes.guess_type(ruta.name)[0] or "image/png"
    return f"data:{tipo};base64," + base64.b64encode(ruta.read_bytes()).decode("ascii")


def crear_tarea(imagenes, texture_prompt=None, pbr=False, polycount=None, remesh=True):
    """Devuelve (task_id, endpoint). imagenes: 1 ruta -> image-to-3d; 2-4 -> multi-image."""
    imagenes = [Path(p) for p in imagenes]
    if not 1 <= len(imagenes) <= 4:
        raise ValueError("Meshy acepta entre 1 y 4 imágenes")

    cuerpo = {
        "ai_model": config.MESHY_AI_MODEL,
        "should_texture": True,
        "enable_pbr": bool(pbr),
        "texture_resolution": config.MESHY_TEXTURE_RES,
        "target_formats": ["glb"],
        "should_remesh": bool(remesh),
        "topology": "triangle",
        "target_polycount": int(polycount or config.MESHY_POLYCOUNT),
        "auto_size": False,          # la escala la fijamos nosotros con las medidas reales
    }
    if texture_prompt:
        cuerpo["texture_prompt"] = texture_prompt[:600]

    if len(imagenes) == 1:
        endpoint = "image-to-3d"
        cuerpo["image_url"] = _data_uri(imagenes[0])
        cuerpo["origin_at"] = "bottom"
    else:
        endpoint = "multi-image-to-3d"
        cuerpo["image_urls"] = [_data_uri(p) for p in imagenes]

    url = f"{config.MESHY_BASE}/openapi/v1/{endpoint}"
    print(f"  [meshy] {endpoint} · {config.MESHY_AI_MODEL} · {len(imagenes)} imagen(es) · ~30 créditos")
    resp = net.post_json(url, cuerpo, _cabeceras(), timeout=300)
    tarea = resp["result"]
    print(f"  [meshy] tarea {tarea}")
    return tarea, endpoint


def esperar(tarea, endpoint, intervalo=5, timeout=None):
    url = f"{config.MESHY_BASE}/openapi/v1/{endpoint}/{tarea}"
    limite = time.time() + (timeout or config.MESHY_TIMEOUT_S)
    ultimo = -1
    while True:
        estado = net.get_json(url, _cabeceras())
        progreso = estado.get("progress", 0)
        if progreso != ultimo:
            print(f"  [meshy] {estado.get('status')} {progreso}%")
            ultimo = progreso
        if estado.get("status") in TERMINALES:
            break
        if time.time() > limite:
            raise TimeoutError(f"Meshy no terminó la tarea {tarea} en el tiempo previsto")
        time.sleep(intervalo)

    if estado["status"] != "SUCCEEDED":
        detalle = (estado.get("task_error") or {}).get("message", "sin detalle")
        raise RuntimeError(f"Meshy falló ({estado['status']}): {detalle}")
    print(f"  [meshy] listo · {estado.get('consumed_credits', '?')} créditos")
    return estado


def descargar_glb(estado, destino: Path):
    url = (estado.get("model_urls") or {}).get("glb")
    if not url:
        raise RuntimeError("La tarea de Meshy no trae model_urls.glb")
    net.descargar(url, destino)
    print(f"  [meshy] GLB -> {destino.name} ({destino.stat().st_size / 1e6:.2f} MB)")
    return destino


def generar(imagenes, destino: Path, registro: Path = None, al_crear=None, **kw):
    """al_crear(task_id, endpoint) se llama APENAS existe la tarea, antes de esperarla:
    el orquestador la persiste ahí para poder retomar el polling sin volver a pagarla."""
    tarea, endpoint = crear_tarea(imagenes, **kw)
    if al_crear:
        al_crear(tarea, endpoint)
    estado = esperar(tarea, endpoint)
    if registro:
        registro.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    descargar_glb(estado, destino)
    return {
        "task_id": tarea,
        "endpoint": endpoint,
        "creditos": estado.get("consumed_credits"),
        "thumbnail": estado.get("thumbnail_url"),
    }


def retomar(task_id, endpoint, destino: Path, registro: Path = None):
    """Vuelve a esperar/descargar una tarea ya creada (no gasta créditos nuevos)."""
    print(f"  [meshy] retomando la tarea {task_id} (ya pagada)")
    estado = esperar(task_id, endpoint)
    if registro:
        registro.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    descargar_glb(estado, destino)
    return {"task_id": task_id, "endpoint": endpoint,
            "creditos": estado.get("consumed_credits"),
            "thumbnail": estado.get("thumbnail_url")}
