"""
Lectura de multipart/form-data sin el módulo `cgi`.

`cgi` era la forma habitual de hacer esto, pero quedó obsoleto en Python 3.11 y
**se eliminó en 3.13**. La vía que recomienda la propia documentación es el
módulo `email`, que ya sabe leer cuerpos multipart porque es el mismo formato de
los adjuntos de correo.

Solo hace falta para un extremo (crear un trabajo, que lleva la foto adjunta),
así que se resuelve con una función y sin dependencias.
"""

from email.parser import BytesParser
from email.policy import HTTP  # cabeceras al estilo de una petición HTTP


class Campos:
    """Los campos de un formulario multipart, ya separados."""

    def __init__(self, texto: dict, archivos: dict):
        self._texto = texto
        self._archivos = archivos

    def texto(self, nombre: str, defecto: str = "") -> str:
        return self._texto.get(nombre, defecto)

    def archivo(self, nombre: str):
        """Devuelve (nombre_de_archivo, contenido) o None."""
        return self._archivos.get(nombre)

    def __contains__(self, nombre):
        return nombre in self._texto or nombre in self._archivos


def leer(cuerpo: bytes, content_type: str) -> Campos:
    """
    Separa un cuerpo multipart en campos de texto y archivos.

    @param cuerpo        bytes crudos de la petición
    @param content_type  la cabecera completa, con su boundary
    """
    # BytesParser espera un mensaje con cabeceras; se le antepone la única que
    # necesita para saber cómo separar las partes.
    crudo = b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + cuerpo
    mensaje = BytesParser(policy=HTTP).parsebytes(crudo)

    texto, archivos = {}, {}

    if not mensaje.is_multipart():
        return Campos(texto, archivos)

    for parte in mensaje.iter_parts():
        disp = parte.get("Content-Disposition", "")
        if not disp:
            continue

        nombre = parte.get_param("name", header="Content-Disposition")
        if not nombre:
            continue

        archivo = parte.get_param("filename", header="Content-Disposition")
        datos = parte.get_payload(decode=True) or b""

        if archivo:
            archivos[nombre] = (archivo, datos)
        else:
            # Los campos de texto vienen en UTF-8; un byte suelto no debe tirar
            # abajo la petición entera.
            texto[nombre] = datos.decode("utf-8", errors="replace").strip()

    return Campos(texto, archivos)
