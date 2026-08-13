"""
HTTP mínimo sobre urllib (sin dependencias externas): JSON, multipart y descargas,
con reintentos exponenciales en 429/5xx.
"""

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

UA = "ar-shoes-generator/1.0"
REINTENTABLES = (408, 409, 429, 500, 502, 503, 504)


class HttpError(RuntimeError):
    def __init__(self, codigo, url, cuerpo):
        self.codigo = codigo
        super().__init__(f"HTTP {codigo} en {url}: {cuerpo[:600]}")


def request_raw(url, method="GET", headers=None, data=None, content_type=None,
                timeout=300, reintentos=4):
    cab = {"User-Agent": UA}
    cab.update(headers or {})
    if content_type:
        cab["Content-Type"] = content_type

    for intento in range(reintentos + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=cab)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", "replace")
            if e.code in REINTENTABLES and intento < reintentos:
                espera = _espera(e.headers.get("Retry-After"), intento)
                print(f"  [http] {e.code}, reintento en {espera:.0f}s…")
                time.sleep(espera)
                continue
            raise HttpError(e.code, url, cuerpo) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if intento < reintentos:
                espera = _espera(None, intento)
                print(f"  [http] {e}, reintento en {espera:.0f}s…")
                time.sleep(espera)
                continue
            raise


def _espera(retry_after, intento):
    if retry_after:
        try:
            return min(120.0, float(retry_after))
        except ValueError:
            pass
    return min(60.0, 2.0 ** intento * 2.0)


def post_json(url, payload, headers=None, **kw):
    cuerpo = json.dumps(payload).encode("utf-8")
    crudo = request_raw(url, "POST", headers, cuerpo, "application/json", **kw)
    return json.loads(crudo.decode("utf-8"))


def get_json(url, headers=None, **kw):
    return json.loads(request_raw(url, "GET", headers, **kw).decode("utf-8"))


def post_multipart(url, campos, archivos, headers=None, **kw):
    """campos: dict[str, str|list[str]]. archivos: list[(campo, nombre, bytes)]."""
    borde = uuid.uuid4().hex
    out = bytearray()
    for k, v in campos.items():
        if v is None:
            continue
        for item in (v if isinstance(v, list) else [v]):
            out += f'--{borde}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{item}\r\n'.encode("utf-8")
    for campo, nombre, blob in archivos:
        tipo = mimetypes.guess_type(nombre)[0] or "application/octet-stream"
        out += (f'--{borde}\r\nContent-Disposition: form-data; name="{campo}"; '
                f'filename="{nombre}"\r\nContent-Type: {tipo}\r\n\r\n').encode("utf-8")
        out += blob + b"\r\n"
    out += f"--{borde}--\r\n".encode("utf-8")
    crudo = request_raw(url, "POST", headers, bytes(out),
                        f"multipart/form-data; boundary={borde}", **kw)
    return json.loads(crudo.decode("utf-8"))


def descargar(url, destino: Path, headers=None):
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(request_raw(url, "GET", headers, timeout=600))
    return destino


# Los CDN de e-commerce rechazan agentes desconocidos; para bajar fotos de producto
# se usa un User-Agent de navegador.
UA_NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
EXT_POR_TIPO = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
                "image/webp": ".webp", "image/avif": ".avif", "image/gif": ".gif"}


def descargar_imagen(url, carpeta: Path, nombre="original"):
    """Baja una imagen y le pone la extensión según el Content-Type. -> Path."""
    req = urllib.request.Request(url, headers={"User-Agent": UA_NAVEGADOR, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(req, timeout=120) as r:
        tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        datos = r.read()
    if not tipo.startswith("image/"):
        raise RuntimeError(f"la URL no devolvió una imagen sino {tipo or 'algo desconocido'}")
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / (nombre + EXT_POR_TIPO.get(tipo, ".img"))
    destino.write_bytes(datos)
    print(f"  descargada {destino.name} ({len(datos) / 1024:.0f} KB, {tipo})")
    return destino
