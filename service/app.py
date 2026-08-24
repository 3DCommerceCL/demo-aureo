"""
Servicio de generación de modelos 3D — capa HTTP sobre el pipeline.

El pipeline NO se toca: este módulo traduce peticiones HTTP a llamadas de
`generator.imagen` y `generator.pipeline`, y traduce sus archivos de estado a la
instantánea JSON que define el contrato
(aureo-pro/docs/SERVICIO-GENERACION.md).

Arranque:
    python -m service.app

Variables:
    AUREO_SHARED_KEY   clave HMAC compartida con WordPress   (obligatoria)
    OPENAI_API_KEY     pasos 0-1                              (obligatoria)
    MESHY_API_KEY      paso 3                                 (obligatoria)
    GEN3D_JOBS_DIR     carpeta de trabajos  → volumen         (recomendada)
    GEN3D_PRUEBAS_DIR  carpeta de variantes → volumen         (recomendada)
    PORT               puerto a escuchar    (lo inyecta Railway)
    PUBLIC_URL         URL pública del servicio, para armar las de los archivos

Se usa http.server de la biblioteca estándar a propósito: el servicio atiende
pocas peticiones y así el despliegue no arrastra un framework más. Cambiarlo por
FastAPI no alteraría ninguna decisión de diseño.
"""

import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from generator import config

from . import ejecutor, estado, firma, multipart

RUTA_JOB = re.compile(r"^/v1/jobs/([A-Za-z0-9._-]+)(?:/([a-z]+))?$")
RUTA_FILE = re.compile(r"^/files/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$")

# QUÉ se puede descargar de la carpeta de un trabajo.
#
# Es una lista de nombres concretos, no un filtro por extensión. La diferencia
# importa: la carpeta también contiene service.json, y ahí adentro va el
# callback_url CON su token —el que autentica los avisos a WordPress—. Filtrar
# por extensión dejaría ese archivo servible.
#
# Regla: se nombra lo que se entrega, y todo lo demás no existe para el cliente.
ENTREGABLES = {
    "model.glb": "model/gltf-binary",
    "model_web.glb": "model/gltf-binary",
    "model.usdz": "model/vnd.usdz+zip",
    "preview.html": "text/html; charset=utf-8",
    "front.png": "image/png",
}

# Los renders de control que produce Blender, si están.
PREFIJOS_PNG = ("vista_",)


def url_publica() -> str:
    """URL con la que el servicio se nombra a sí mismo en las URLs de archivos."""
    u = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if u:
        return u
    # Railway expone el dominio generado en esta variable.
    dominio = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if dominio:
        return f"https://{dominio}"
    return f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"


class Handler(BaseHTTPRequestHandler):
    server_version = "AureoGen/1.0"

    # ── Utilidades de respuesta ─────────────────────────────────────────

    def _json(self, codigo: int, datos: dict):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, codigo: int, mensaje: str):
        # El texto se muestra tal cual en el admin de WordPress.
        self._json(codigo, {"error": mensaje})

    def log_message(self, formato, *args):
        # El formato por defecto es ruidoso y no dice el estado del trabajo.
        sys.stderr.write("%s %s\n" % (self.address_string(), formato % args))

    # ── Lectura y autenticación ─────────────────────────────────────────

    def _cuerpo(self) -> bytes:
        largo = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(largo) if largo else b""

    def _autenticado(self, cuerpo: bytes) -> bool:
        motivo = firma.verificar(
            cuerpo,
            self.headers.get("X-Aureo-Timestamp"),
            self.headers.get("X-Aureo-Signature"),
        )
        if motivo:
            # El motivo va al log del servicio, no a la respuesta: decirle al
            # cliente cuál comprobación falló le ayuda a afinar un ataque.
            sys.stderr.write(f"[auth] rechazado: {motivo}\n")
            self._error(401, "No autorizado.")
            return False
        return True

    # ── Rutas ───────────────────────────────────────────────────────────

    def do_GET(self):
        try:
            if self.path == "/health" or self.path == "/":
                return self._salud()

            m = RUTA_FILE.match(self.path.split("?")[0])
            if m:
                if not self._autenticado(b""):
                    return
                return self._servir_archivo(m.group(1), m.group(2))

            m = RUTA_JOB.match(self.path.split("?")[0])
            if m and not m.group(2):
                if not self._autenticado(b""):
                    return
                return self._ver_trabajo(m.group(1))

            self._error(404, "Ruta desconocida.")
        except Exception as e:
            traceback.print_exc()
            self._error(500, f"Error interno del servicio: {e}")

    def do_POST(self):
        try:
            if self.path.split("?")[0] == "/v1/jobs":
                return self._crear_trabajo()

            m = RUTA_JOB.match(self.path.split("?")[0])
            if not m:
                return self._error(404, "Ruta desconocida.")

            slug, accion = m.group(1), m.group(2)
            cuerpo = self._cuerpo()
            if not self._autenticado(cuerpo):
                return

            if not estado.existe(slug):
                return self._error(404, "Ese trabajo no existe.")

            try:
                datos = json.loads(cuerpo) if cuerpo else {}
            except json.JSONDecodeError:
                return self._error(400, "El cuerpo de la petición no es JSON válido.")

            despacho = {
                "elegir": self._elegir,
                "variantes": self._variantes,
                "aprobar": self._aprobar,
                "reintentar": self._reintentar,
            }
            if accion not in despacho:
                return self._error(404, "Acción desconocida.")

            return despacho[accion](slug, datos)

        except Exception as e:
            traceback.print_exc()
            self._error(500, f"Error interno del servicio: {e}")

    # ── Implementación ──────────────────────────────────────────────────

    def _salud(self):
        """Sin autenticar a propósito: es lo que consulta el chequeo de Railway."""
        self._json(200, {
            "ok": True,
            "servicio": "aureo-generador",
            "claves": {
                "openai": bool(config.OPENAI_API_KEY),
                "meshy": bool(config.MESHY_API_KEY),
                "compartida": bool(os.environ.get("AUREO_SHARED_KEY")),
            },
            "jobs_dir": str(config.JOBS_DIR),
            "pruebas_dir": str(config.PRUEBAS_DIR),
        })

    def _ver_trabajo(self, slug):
        self._json(200, estado.snapshot(slug, url_publica()))

    def _crear_trabajo(self):
        """
        multipart/form-data porque lleva la foto. El cuerpo completo —imagen
        incluida— es lo que se firma.
        """
        cuerpo = self._cuerpo()
        if not self._autenticado(cuerpo):
            return

        tipo = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in tipo:
            return self._error(400, "Se esperaba multipart/form-data.")

        form = multipart.leer(cuerpo, tipo)
        campo = form.texto

        nombre = campo("name").strip()
        if not nombre:
            return self._error(400, "Falta el nombre del trabajo.")

        slug = estado.slugificar(nombre)

        # Idempotencia: reenviar el mismo nombre devuelve el trabajo que ya
        # existe en vez de crear otro. Sin esto, un reintento del plugin podría
        # llegar a pagar dos reconstrucciones.
        if estado.existe(slug):
            return self._json(201, estado.snapshot(slug, url_publica()))

        try:
            medidas = {
                "alto": float(campo("alto", "0")),
                "ancho": float(campo("ancho", "0")),
                "largo": float(campo("largo", "0")),
                "unidad": campo("unidad", "cm"),
            }
        except ValueError:
            return self._error(400, "Las medidas tienen que ser números.")

        if min(medidas["alto"], medidas["ancho"], medidas["largo"]) <= 0:
            return self._error(
                400,
                "Hacen falta las tres medidas reales del producto. Son lo que "
                "permite que el modelo se vea del tamaño correcto en AR."
            )

        carpeta = estado.carpeta_job(slug)
        carpeta.mkdir(parents=True, exist_ok=True)

        # La foto: o viene adjunta, o viene por URL y la baja el servicio.
        try:
            guardada = self._guardar_foto(form, carpeta, campo("image_url").strip())
        except Exception as e:
            # Que no se pueda traer la foto es un problema del lado del cliente
            # (una URL mal escrita, una imagen que ya no está), así que va como
            # 4xx y con un texto que se pueda leer sin ser programador.
            sys.stderr.write(f"[{slug}] no se pudo traer la foto: {e}" + chr(10))
            self._limpiar_job(carpeta)
            return self._error(
                400,
                "No se pudo descargar la foto del producto. Revisa que la "
                "dirección sea correcta y que la imagen siga publicada."
            )

        if not guardada:
            self._limpiar_job(carpeta)
            return self._error(400, "No llegó ninguna foto del producto.")

        estado.crear(
            slug, nombre, medidas, campo("callback_url").strip(),
            categoria=campo("categoria").strip(),
            objeto=campo("objeto").strip(),
        )
        estado.poner_estado(slug, "analyzing")

        ejecutor.lanzar_analisis(slug, url_publica(), guardada)

        self._json(201, estado.snapshot(slug, url_publica()))

    def _limpiar_job(self, carpeta: Path):
        """Borra la carpeta de un trabajo que no llegó a crearse del todo.

        Si no, un nombre que falló queda ocupado: el siguiente intento con el
        mismo nombre lo tomaría por idempotencia y devolvería un trabajo vacío."""
        import shutil
        shutil.rmtree(carpeta, ignore_errors=True)

    def _guardar_foto(self, form, carpeta: Path, image_url: str):
        adjunto = form.archivo("image")
        if adjunto:
            nombre_archivo, datos = adjunto
            ext = Path(nombre_archivo).suffix.lower() or ".jpg"
            destino = carpeta / f"original{ext}"
            destino.write_bytes(datos)
            return destino

        if image_url:
            import urllib.request
            ext = Path(image_url.split("?")[0]).suffix.lower() or ".jpg"
            destino = carpeta / f"original{ext}"
            with urllib.request.urlopen(image_url, timeout=60) as r:
                destino.write_bytes(r.read())
            return destino

        return None

    def _elegir(self, slug, datos):
        if estado.leer(slug).get("estado") != "choosing":
            return self._error(409, "Este trabajo no está esperando que elijas el producto.")

        objeto = str(datos.get("objeto") or "").strip()
        if not objeto:
            objeto = estado.objeto_por_numero(slug, int(datos.get("candidato") or 0))
        if not objeto:
            return self._error(400, "Hay que indicar cuál de los objetos es el producto.")

        d = estado.leer(slug)
        d["objeto"] = objeto
        estado.escribir(slug, d)

        ejecutor.lanzar_imagen(slug, url_publica())
        self._json(200, estado.snapshot(slug, url_publica()))

    def _variantes(self, slug, datos):
        if estado.leer(slug).get("estado") not in ("reviewing", "imaging"):
            return self._error(409, "Este trabajo no está en la etapa de la imagen.")

        ejecutor.lanzar_imagen(
            slug, url_publica(),
            vista=str(datos.get("vista") or "frontal"),
            fondo=str(datos.get("fondo") or "transparente"),
            n=max(1, min(4, int(datos.get("n") or 1))),
            extra=str(datos.get("extra") or ""),
        )
        self._json(200, estado.snapshot(slug, url_publica()))

    def _aprobar(self, slug, datos):
        """
        El único extremo que gasta créditos.

        La comprobación de estado NO es decorativa: pipeline.confirmar_gasto()
        no pregunta nada cuando no hay terminal interactiva, y un servicio nunca
        la tiene. Sin este 409, un doble clic o un reintento del plugin compran
        dos reconstrucciones de 30 créditos.
        """
        if estado.leer(slug).get("estado") != "reviewing":
            return self._error(409, "Este trabajo no está esperando aprobación.")

        variante = str(datos.get("variante") or "").strip()
        if not variante:
            return self._error(400, "Hay que indicar qué variante de imagen aprobar.")

        modo = str(datos.get("modo") or "exacto")
        if modo not in ("uniforme", "exacto", "alto", "ancho", "largo"):
            modo = "exacto"

        d = estado.leer(slug)
        d["variante_aprobada"] = variante
        d["modo"] = modo
        estado.escribir(slug, d)

        ejecutor.lanzar_reconstruccion(slug, url_publica(), variante, modo)
        self._json(200, estado.snapshot(slug, url_publica()))

    def _reintentar(self, slug, datos):
        desde = str(datos.get("desde") or "").strip()
        if desde not in ("imagen", "vistas", "malla", "escala", "blender", "web"):
            return self._error(400, "Ese paso no se puede rehacer.")

        ejecutor.lanzar_rehacer(slug, url_publica(), desde)
        self._json(200, estado.snapshot(slug, url_publica()))

    def _servir_archivo(self, slug, nombre):
        # El nombre ya viene acotado por la expresión de la ruta (sin barras ni
        # puntos dobles), pero se resuelve y se comprueba igual que quede dentro
        # de la carpeta del trabajo.
        carpeta = estado.carpeta_job(slug).resolve()
        ruta = (carpeta / nombre).resolve()
        if not str(ruta).startswith(str(carpeta)) or not ruta.is_file():
            return self._error(404, "Ese archivo no existe.")

        tipo = ENTREGABLES.get(nombre)
        if not tipo and nombre.startswith(PREFIJOS_PNG) and ruta.suffix.lower() == ".png":
            tipo = "image/png"
        if not tipo:
            # Se responde 404 y no 403 a propósito: para quien pregunta, un
            # archivo que no se entrega y uno que no existe son lo mismo.
            return self._error(404, "Ese archivo no existe.")

        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


def main():
    # El pipeline escribe su progreso con caracteres como "≈" y "→". Si la
    # consola del sistema no es UTF-8 —el caso de Windows, cp1252— un print
    # revienta y se lleva puesto el paso entero, sin haber llegado siquiera a
    # llamar a ninguna API. Se fija la salida en UTF-8 y el problema desaparece
    # en cualquier sistema.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    puerto = int(os.environ.get("PORT", "8080"))

    faltan = [v for v in ("AUREO_SHARED_KEY", "OPENAI_API_KEY", "MESHY_API_KEY")
              if not os.environ.get(v)]
    if faltan:
        # Se avisa pero no se aborta: /health tiene que poder responder para que
        # se vea desde afuera qué falta, en vez de un contenedor que no arranca.
        print(f"AVISO: faltan variables: {', '.join(faltan)}", file=sys.stderr)

    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    config.PRUEBAS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"servicio en 0.0.0.0:{puerto} · público: {url_publica()}", flush=True)
    print(f"  trabajos: {config.JOBS_DIR}", flush=True)
    print(f"  variantes: {config.PRUEBAS_DIR}", flush=True)

    # 0.0.0.0 y no 127.0.0.1: dentro de un contenedor hay que escuchar en todas
    # las interfaces o el enrutador de la plataforma no llega.
    ThreadingHTTPServer(("0.0.0.0", puerto), Handler).serve_forever()


if __name__ == "__main__":
    main()
