"""
Corre los pasos del pipeline fuera del hilo de la petición HTTP.

Un trabajo completo tarda unos 150 segundos. Ninguna petición HTTP puede
sostener eso, así que cada paso arranca en un hilo, responde de inmediato con el
estado nuevo, y avisa a WordPress por callback cuando termina.

Si el contenedor se reinicia a mitad de un paso, no se pierde lo pagado: el
pipeline reanuda desde `job.json` y el `task_id` de Meshy queda persistido apenas
se crea la tarea. Del lado de WordPress hay además una reconciliación por cron
cada 5 minutos, por si el callback no llega.
"""

import json
import threading
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from generator import analizar, config, imagen, pipeline

from . import estado, firma


def _log(slug, *partes):
    print(f"[{slug}]", *partes, flush=True)


# En qué estado queda un trabajo según el paso que se está rehaciendo. Los
# nombres de los pasos son los de `pipeline.PASOS` / `--forzar`.
PASO_A_ESTADO = {
    "imagen": "imaging",
    "vistas": "imaging",
    "malla": "meshing",
    "escala": "finishing",
    "blender": "finishing",
    "web": "finishing",
}

# Estados en los que hay —o debería haber— un hilo trabajando. Si el proceso
# acaba de arrancar, por definición no lo hay: nadie sobrevive a un reinicio.
ESTADOS_EN_CURSO = ("analyzing", "imaging", "meshing", "finishing")


# ── Aviso a WordPress ───────────────────────────────────────────────────

def avisar(slug: str, base_url: str):
    """
    Manda la instantánea al callback del trabajo.

    Es una optimización, no la fuente de verdad: si falla, WordPress igual se
    entera por su reconciliación periódica. Por eso un fallo acá se registra
    pero no marca el trabajo como fallido.
    """
    d = estado.leer(slug)
    url = d.get("callback_url", "")
    if not url:
        return

    cuerpo = json.dumps(
        estado.snapshot(slug, base_url), ensure_ascii=False
    ).encode("utf-8")

    cabeceras = {"Content-Type": "application/json"}
    cabeceras.update(firma.firmar(cuerpo))

    req = urllib.request.Request(url, data=cuerpo, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _log(slug, f"callback {d.get('estado')} → HTTP {r.status}")
    except urllib.error.HTTPError as e:
        _log(slug, f"callback rechazado: HTTP {e.code}")
    except Exception as e:  # red caída, sitio inalcanzable, DNS…
        _log(slug, f"callback no llegó: {e}")


def reconciliar(base_url: str):
    """
    Al arrancar, ningún trabajo puede quedar anunciando un paso que ya no corre.

    Los pasos viven en hilos de ESTE proceso. Si el contenedor se reinicia a
    mitad de uno —un despliegue, un OOM, el reinicio automático de la
    plataforma— el hilo muere, pero `service.json` conserva el último estado que
    alcanzó a escribirse. Nadie lo vuelve a tocar nunca: el trabajo queda para
    siempre en "meshing" o en "finishing", WordPress lo sondea eternamente y la
    pantalla muestra un spinner sobre un paso que no existe. Ese es exactamente
    el síntoma de "quedó en «Ajustando medidas y exportando» y nunca avanzó".

    Acá se cierra el caso, con el mismo criterio que usa el servicio de merch:
    si los archivos están, el trabajo se da por terminado (la malla ya se pagó y
    tirarla serían 30 créditos a la basura); si no, se marca como fallido con un
    mensaje que explica qué pasó, para que la persona pueda rehacerlo desde el
    paso que corresponda —desde la escala o la compresión no cuesta nada—.

    -> (rescatados, marcados_fallidos)
    """
    rescatados, fallidos = 0, 0

    if not config.JOBS_DIR.exists():
        return 0, 0

    for marca in sorted(config.JOBS_DIR.glob("*/service.json")):
        slug = marca.parent.name
        d = estado.leer(slug)

        if d.get("estado") not in ESTADOS_EN_CURSO:
            continue

        if estado.entregables(slug, base_url):
            estado.poner_estado(slug, "done")
            rescatados += 1
            _log(slug, "reconciliado: los entregables ya estaban, se da por terminado")
        else:
            estado.poner_estado(slug, "failed", _MENSAJE_REINICIO)
            fallidos += 1
            _log(slug, f"reconciliado: se reinició durante «{d.get('estado')}», queda fallido")

        # Se avisa igual que en cualquier otro cambio de estado: si el sitio es
        # alcanzable se entera al instante, y si no, lo verá en su reconciliación.
        avisar(slug, base_url)

    if rescatados or fallidos:
        print(
            f"reconciliación: {rescatados} rescatado(s), {fallidos} marcado(s) como fallidos",
            flush=True,
        )

    return rescatados, fallidos


_MENSAJE_REINICIO = (
    "El servicio de generación se reinició mientras este trabajo estaba en "
    "curso y el paso quedó a medias. Lo que ya se había generado se conserva: "
    "rehacer desde el ajuste de medidas o desde la compresión para web "
    "reutiliza la reconstrucción 3D y no vuelve a gastar créditos."
)


def _en_hilo(fn, slug, base_url, *args):
    """Corre fn en segundo plano; cualquier excepción deja el trabajo en 'failed'."""

    def correr():
        try:
            fn(slug, base_url, *args)
        except Exception as e:
            traceback.print_exc()
            # El mensaje se muestra tal cual en el admin de WordPress, así que
            # se escribe para el dueño de una tienda, no para un desarrollador.
            estado.poner_estado(slug, "failed", _mensaje_humano(e))
            avisar(slug, base_url)

    threading.Thread(target=correr, daemon=True).start()


def _mensaje_humano(e: Exception) -> str:
    texto = str(e).strip() or e.__class__.__name__
    if "OPENAI_API_KEY" in texto or "MESHY_API_KEY" in texto:
        return "El servicio de generación no tiene configuradas sus claves de API."

    # Un 401/403 del proveedor es siempre la clave, no la foto ni las medidas.
    # Se traduce en vez de reenviar el cuerpo crudo: ese cuerpo trae la clave
    # rechazada parcialmente enmascarada, y terminaba impresa en el admin de
    # WordPress a la vista de cualquiera que abriera el trabajo.
    if getattr(e, "codigo", None) in (401, 403):
        proveedor = "OpenAI" if "openai" in texto.lower() else "Meshy"
        return (
            f"El servicio de generación no pudo autenticarse con {proveedor}: "
            "la clave de API es incorrecta o está vencida. Revisá las variables "
            "del servicio (consultá /health para ver el diagnóstico)."
        )
    if "timed out" in texto.lower() or "timeout" in texto.lower():
        return "La reconstrucción tardó más de lo permitido y se canceló."
    return texto[:300]


# ── Paso 0: ¿qué objetos hay en la foto? ────────────────────────────────

def analizar_foto(slug: str, base_url: str, foto: Path):
    _log(slug, "paso 0: identificando objetos")
    carpeta = estado.carpeta_pruebas(slug)
    carpeta.mkdir(parents=True, exist_ok=True)

    analisis = analizar.cargar_o_detectar(foto, carpeta)
    # El análisis cuesta centésimas de centavo; se contabiliza igual para que la
    # cifra que ve el usuario sea la real.
    estado.sumar_costo(slug, 0.0005)

    d = estado.leer(slug)

    # Si la persona ya dijo cuál es el producto, no hay nada que preguntar.
    if d.get("objeto"):
        _log(slug, "producto ya indicado, saltando la pregunta")
        estado.poner_estado(slug, "imaging")
        avisar(slug, base_url)
        return generar_imagen(slug, base_url)

    objetos = analisis.get("objetos", [])
    probables = [o for o in objetos if o.get("es_producto_probable")]

    # Un solo candidato claro: seguir sin molestar a nadie. Es el mismo criterio
    # que usa analizar.elegir() en el CLI.
    if len(objetos) == 1 or (not analisis.get("ambiguo") and len(probables) == 1):
        elegido = (probables or objetos)[0]
        d["objeto"] = elegido.get("descripcion") or elegido.get("nombre", "")
        estado.escribir(slug, d)
        _log(slug, f"producto único: {elegido.get('nombre')}")
        estado.poner_estado(slug, "imaging")
        avisar(slug, base_url)
        return generar_imagen(slug, base_url)

    _log(slug, f"{len(objetos)} candidatos: hay que preguntar")
    estado.poner_estado(slug, "choosing")
    avisar(slug, base_url)


# ── Paso 1: imagen limpia con la proporción corregida ───────────────────

def generar_imagen(slug: str, base_url: str, vista="frontal", fondo="transparente",
                   n=1, extra=""):
    _log(slug, f"paso 1: imagen {vista}/{fondo} ×{n}")
    estado.poner_estado(slug, "imaging")

    d = estado.leer(slug)
    med = d.get("medidas", {})
    foto = _foto_de(slug)

    argv = [
        "--imagen", str(foto),
        "--nombre", d.get("nombre", slug),
        "--vista", vista,
        "--fondo", fondo,
        "--n", str(n),
        "--alto", str(med.get("alto", 0)),
        "--ancho", str(med.get("ancho", 0)),
        "--largo", str(med.get("largo", 0)),
        "--alto-extra", str(med.get("alto_extra", 0)),
        "--unidad", med.get("unidad", "cm"),
    ]
    # --objeto salta la pregunta interactiva del paso 0, que en un servicio
    # nunca podría responderse.
    if d.get("objeto"):
        argv += ["--objeto", d["objeto"]]
    if extra:
        argv += ["--extra", extra]

    imagen.main(argv)

    # El costo real de cada variante lo escribe imagen.py en su NNN.json.
    estado.escribir(slug, {**estado.leer(slug), "costo_usd": _costo_imagenes(slug)})

    _log(slug, f"{len(estado.variantes(slug))} variantes listas")
    estado.poner_estado(slug, "reviewing")
    avisar(slug, base_url)


# ── Pasos 3-6: la malla. ACÁ SE GASTA EL DINERO ─────────────────────────

def reconstruir(slug: str, base_url: str, variante: str, modo: str):
    """
    Solo debe llamarse desde el extremo `aprobar`, y solo si el trabajo estaba
    en `reviewing`. El motivo es concreto: pipeline.confirmar_gasto() devuelve
    True sin preguntar cuando no hay terminal interactiva —y un servicio nunca
    la tiene— así que esta función compra 30 créditos sin más aviso. La
    confirmación humana vive en el asistente de WordPress.
    """
    _log(slug, f"pasos 3-6: reconstruyendo desde la variante {variante} (modo {modo})")
    estado.poner_estado(slug, "meshing")
    avisar(slug, base_url)

    d = estado.leer(slug)
    med = d.get("medidas", {})

    limpia = _ruta_variante(slug, variante)
    if not limpia:
        raise RuntimeError(f"la variante {variante} no existe")

    argv = [
        "--imagen", str(_foto_de(slug)),
        "--nombre", d.get("nombre", slug),
        "--alto", str(med.get("alto", 0)),
        "--ancho", str(med.get("ancho", 0)),
        "--largo", str(med.get("largo", 0)),
        "--alto-extra", str(med.get("alto_extra", 0)),
        "--unidad", med.get("unidad", "cm"),
        "--imagen-limpia", str(limpia),
        "--modo", modo,
        "--web",   # produce model_web.glb, que es el archivo que importa el plugin
        "--si",    # el gasto ya lo aprobó una persona en WordPress
    ]
    # Sin --blender a propósito: no está disponible en el contenedor. Se pierde
    # la segunda medición y su USDZ; model-viewer genera el USDZ al vuelo.

    pipeline.main(argv)

    estado.poner_estado(slug, "done")
    _log(slug, "listo")
    avisar(slug, base_url)


def rehacer(slug: str, base_url: str, desde: str):
    _log(slug, f"rehaciendo desde: {desde}")
    # El estado tiene que decir en qué se está trabajando de verdad: rehacer la
    # imagen anunciándolo como "Ajustando medidas y exportando" deja a quien
    # mira la pantalla esperando el final de un paso que ni siquiera empezó.
    estado.poner_estado(slug, PASO_A_ESTADO.get(desde, "finishing"))
    avisar(slug, base_url)

    d = estado.leer(slug)
    med = d.get("medidas", {})
    limpia = _ruta_variante(slug, d.get("variante_aprobada", ""))

    argv = [
        "--imagen", str(_foto_de(slug)),
        "--nombre", d.get("nombre", slug),
        "--alto", str(med.get("alto", 0)),
        "--ancho", str(med.get("ancho", 0)),
        "--largo", str(med.get("largo", 0)),
        "--alto-extra", str(med.get("alto_extra", 0)),
        "--unidad", med.get("unidad", "cm"),
        "--modo", d.get("modo", "exacto"),
        "--forzar", desde,
        "--web", "--si",
    ]
    if limpia:
        argv += ["--imagen-limpia", str(limpia)]

    pipeline.main(argv)

    estado.poner_estado(slug, "done")
    avisar(slug, base_url)


# ── Auxiliares ──────────────────────────────────────────────────────────

def _foto_de(slug: str) -> Path:
    """La foto original que subió el usuario, guardada al crear el trabajo."""
    carpeta = estado.carpeta_job(slug)
    for f in sorted(carpeta.glob("original.*")):
        return f
    raise RuntimeError("no se encontró la foto original del trabajo")


def _ruta_variante(slug: str, variante_id: str):
    if not variante_id:
        return None
    for v in estado.variantes(slug):
        if v["id"] == variante_id:
            return estado.carpeta_pruebas(slug) / v["archivo"]
    return None


def _costo_imagenes(slug: str) -> float:
    """Suma lo que costaron las variantes, leyendo lo que anotó imagen.py."""
    total = 0.0005  # el análisis de objetos
    carpeta = estado.carpeta_pruebas(slug)
    for f in carpeta.glob("[0-9][0-9][0-9].json"):
        try:
            total += float(json.loads(f.read_text(encoding="utf-8")).get("costo_usd", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return round(total, 4)


# ── Lanzadores en segundo plano ─────────────────────────────────────────

def lanzar_analisis(slug, base_url, foto):
    _en_hilo(analizar_foto, slug, base_url, foto)


def lanzar_imagen(slug, base_url, vista="frontal", fondo="transparente", n=1, extra=""):
    _en_hilo(generar_imagen, slug, base_url, vista, fondo, n, extra)


def lanzar_reconstruccion(slug, base_url, variante, modo):
    _en_hilo(reconstruir, slug, base_url, variante, modo)


def lanzar_rehacer(slug, base_url, desde):
    _en_hilo(rehacer, slug, base_url, desde)
