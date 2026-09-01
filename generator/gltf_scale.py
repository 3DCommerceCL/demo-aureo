"""
PASO 3 — Escalado métrico del GLB (sin dependencias, sin re-exportar la malla).

Meshy devuelve el modelo normalizado (unidades arbitrarias). Para AR hace falta que
1 unidad = 1 metro y que el objeto mida EXACTAMENTE lo que declaró el usuario.

En vez de reimportar/reexportar en Blender (que reescribe materiales, texturas y
extensiones KHR_*, y suele degradar el PBR), este módulo:
  1. mide el bounding box real recorriendo el grafo de nodos y leyendo accessor.min/max
     de los POSITION (el spec de glTF los exige),
  2. inserta UN nodo raíz nuevo con escala + traslación y cuelga de él la escena,
  3. reescribe el contenedor GLB dejando el chunk binario intacto.

Los bytes de geometría y texturas NO se tocan: el modelo escalado es idéntico al de
Meshy salvo por la matriz del nodo raíz. blender_finish.py vuelve a medirlo por otra
vía para confirmar (medir dos veces, con dos implementaciones distintas).

Nota sobre la regla dura #1 del proyecto ("nunca reescalar/reposicionar GLBs en
scripts"): esa regla protege los assets alineados A MANO del pipeline de entrenamiento.
Acá el reescalado ES el entregable, y se aplica a un GLB recién generado por Meshy que
nadie alineó a mano. Son cosas distintas — no mezclar ambos flujos.

Uso directo:
  py -m generator.gltf_scale crudo.glb modelo.glb --alto 30 --ancho 12 --largo 20 --unidad cm
"""

import json
import math
import struct
from pathlib import Path

MAGIC = 0x46546C67          # 'glTF'
CHUNK_JSON = 0x4E4F534A     # 'JSON'
CHUNK_BIN = 0x004E4942      # 'BIN\0'

# glTF es Y-up: alto -> Y siempre; ancho/largo -> X/Z (permutables, ver `escalar`)
UNIDADES_M = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048}


# ---------------------------------------------------------------- contenedor GLB
def leer_glb(ruta: Path):
    datos = Path(ruta).read_bytes()
    if len(datos) < 12 or struct.unpack_from("<I", datos, 0)[0] != MAGIC:
        raise ValueError(f"{ruta} no es un GLB binario (¿.gltf con buffers externos?)")
    total = struct.unpack_from("<I", datos, 8)[0]
    pos, gltf, binario = 12, None, b""
    while pos + 8 <= min(total, len(datos)):
        largo, tipo = struct.unpack_from("<II", datos, pos)
        cuerpo = datos[pos + 8: pos + 8 + largo]
        if tipo == CHUNK_JSON:
            gltf = json.loads(cuerpo.decode("utf-8"))
        elif tipo == CHUNK_BIN:
            binario = cuerpo
        pos += 8 + largo + (-largo % 4)
    if gltf is None:
        raise ValueError(f"{ruta} no trae chunk JSON")
    return gltf, binario


def escribir_glb(ruta: Path, gltf: dict, binario: bytes):
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    trozos = [(CHUNK_JSON, js)]
    if binario:
        binario = binario + b"\x00" * (-len(binario) % 4)
        trozos.append((CHUNK_BIN, binario))
    total = 12 + sum(8 + len(c) for _, c in trozos)
    salida = bytearray(struct.pack("<III", MAGIC, 2, total))
    for tipo, cuerpo in trozos:
        salida += struct.pack("<II", len(cuerpo), tipo) + cuerpo
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    Path(ruta).write_bytes(bytes(salida))
    return ruta


# ---------------------------------------------------------------- álgebra 4x4 (column-major)
IDENT = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def mat_mul(a, b):
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def mat_trs(t, q, s):
    x, y, z, w = q
    m = [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0.0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0.0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    for c, f in enumerate(s):
        for r in range(3):
            m[c * 4 + r] *= f
    m[12], m[13], m[14] = t
    return m


def mat_nodo(nodo):
    if "matrix" in nodo:
        return list(nodo["matrix"])
    return mat_trs(nodo.get("translation", [0, 0, 0]),
                   nodo.get("rotation", [0, 0, 0, 1]),
                   nodo.get("scale", [1, 1, 1]))


def transformar(m, p):
    return [sum(m[c * 4 + r] * p[c] for c in range(3)) + m[12 + r] for r in range(3)]


# ---------------------------------------------------------------- medición
def medir(gltf: dict):
    """Bounding box de la escena por defecto, en unidades del archivo. -> (min, max)."""
    nodos = gltf.get("nodes", [])
    mallas = gltf.get("meshes", [])
    accesos = gltf.get("accessors", [])
    escena = gltf.get("scenes", [{}])[gltf.get("scene", 0)]

    lo = [math.inf] * 3
    hi = [-math.inf] * 3

    def recorrer(idx, padre):
        nodo = nodos[idx]
        mundo = mat_mul(padre, mat_nodo(nodo))
        if "mesh" in nodo:
            for prim in mallas[nodo["mesh"]].get("primitives", []):
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is None:
                    continue
                acc = accesos[pos]
                if "min" not in acc or "max" not in acc:
                    raise ValueError(
                        "El accessor POSITION no trae min/max: usá --engine blender para medir")
                amin, amax = acc["min"], acc["max"]
                for i in range(8):
                    esquina = [amax[e] if (i >> e) & 1 else amin[e] for e in range(3)]
                    p = transformar(mundo, esquina)
                    for e in range(3):
                        lo[e] = min(lo[e], p[e])
                        hi[e] = max(hi[e], p[e])
        for hijo in nodo.get("children", []):
            recorrer(hijo, mundo)

    for raiz in escena.get("nodes", []):
        recorrer(raiz, IDENT)

    if math.isinf(lo[0]):
        raise ValueError("El GLB no tiene geometría en la escena por defecto")
    return lo, hi


def dimensiones(gltf: dict):
    lo, hi = medir(gltf)
    return [hi[e] - lo[e] for e in range(3)]


# ---------------------------------------------------------------- escalado
def _factores(tam, objetivo, modo, mapeo):
    """tam/objetivo indexados por eje glTF (x, y, z). Devuelve 3 factores."""
    ratios = [objetivo[e] / tam[e] for e in range(3)]
    if modo == "exacto":
        return ratios
    if modo == "uniforme":
        # mínimos cuadrados en log: reparte el error entre los 3 ejes
        u = math.exp(sum(math.log(r) for r in ratios) / 3.0)
    elif modo in mapeo:
        u = ratios[mapeo[modo]]
    else:
        raise ValueError(f"modo de ajuste desconocido: {modo}")
    return [u, u, u]


def escalar(entrada: Path, salida: Path, alto, ancho, largo, unidad="cm",
            modo="uniforme", ejes_auto=True, anclar=True, alto_extra=0.0):
    """
    alto/ancho/largo en `unidad`. Devuelve un dict con el informe del ajuste.
      modo: uniforme (default, no deforma) | exacto (por eje, calza clavado pero deforma)
            | alto | ancho | largo (uniforme tomando ese eje como referencia)
      ejes_auto: si el ancho/largo de la malla están intercambiados respecto a lo declarado,
                 los permuta (el frente que ve Meshy no siempre queda en -Z).
      anclar: deja el origen en el centro de la base (apoyo en el piso para AR).
      alto_extra: alto de algo que sobresale por encima de la pieza y SÍ está en la
                  malla (un grifo sobre un mueble de cocina, por ejemplo), pero que la
                  persona no cuenta como parte de `alto`. La malla entera (pieza +
                  agregado) es lo que de verdad mide `alto + alto_extra` en la realidad;
                  escalar solo por `alto` comprime también la pieza para que el conjunto
                  entero quepa en una medida pensada solo para ella. Por eso el target
                  real del eje Y es la suma, y `alto` queda para la pieza sola.
    """
    if alto_extra < 0:
        raise ValueError("La medida 'alto_extra' no puede ser negativa")

    factor = UNIDADES_M[unidad]
    alto_total = alto + alto_extra
    objetivo_m = {"alto": alto_total * factor, "ancho": ancho * factor, "largo": largo * factor}
    for k, v in objetivo_m.items():
        if v <= 0:
            raise ValueError(f"La medida '{k}' debe ser > 0")

    gltf, binario = leer_glb(entrada)
    lo, hi = medir(gltf)
    tam = [hi[e] - lo[e] for e in range(3)]
    if min(tam) <= 1e-9:
        raise ValueError(f"Bounding box degenerado {tam}: la malla es plana en un eje")

    # mapeo alto->Y fijo; ancho/largo -> X/Z, con permutación automática opcional
    mapeo = {"ancho": 0, "alto": 1, "largo": 2}
    permutado = False
    if ejes_auto:
        directo = abs(math.log((tam[0] / tam[2]) / (objetivo_m["ancho"] / objetivo_m["largo"])))
        cruzado = abs(math.log((tam[2] / tam[0]) / (objetivo_m["ancho"] / objetivo_m["largo"])))
        if cruzado < directo - 1e-6:
            mapeo = {"ancho": 2, "alto": 1, "largo": 0}
            permutado = True

    objetivo = [0.0, 0.0, 0.0]
    for nombre, eje in mapeo.items():
        objetivo[eje] = objetivo_m[nombre]

    escala = _factores(tam, objetivo, modo, mapeo)

    traslacion = [0.0, 0.0, 0.0]
    if anclar:
        traslacion = [
            -((lo[0] + hi[0]) / 2.0) * escala[0],
            -lo[1] * escala[1],
            -((lo[2] + hi[2]) / 2.0) * escala[2],
        ]

    # nodo raíz nuevo: la escena entera cuelga de él
    escena = gltf.get("scenes", [{}])[gltf.get("scene", 0)]
    gltf.setdefault("nodes", [])
    raiz = {"name": "ar_scale_root", "scale": escala, "translation": traslacion}
    hijos = list(escena.get("nodes", []))
    if hijos:
        raiz["children"] = hijos
    gltf["nodes"].append(raiz)
    escena["nodes"] = [len(gltf["nodes"]) - 1]
    escribir_glb(salida, gltf, binario)

    # verificación: se vuelve a medir el archivo escrito
    medido = dimensiones(leer_glb(salida)[0])
    informe = {
        "modo": modo,
        "unidad": unidad,
        # objetivo_m/medido_m/error_pct del eje "alto" son de la pieza + alto_extra
        # juntos (lo único que la malla puede medir): alto_extra_m separado es lo que
        # permite reconstruir cuánto de eso era la pieza sola.
        "alto_extra_m": round(alto_extra * factor, 6),
        "objetivo_m": {k: round(v, 6) for k, v in objetivo_m.items()},
        "medido_m": {n: round(medido[e], 6) for n, e in mapeo.items()},
        "escala": [round(s, 8) for s in escala],
        "ejes_permutados": permutado,
        "anclado_en_base": bool(anclar),
        "error_pct": {n: round(100 * (medido[e] - objetivo_m[n]) / objetivo_m[n], 2)
                      for n, e in mapeo.items()},
        "bbox_original": {"min": [round(v, 6) for v in lo], "max": [round(v, 6) for v in hi]},
    }
    return informe


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Escala un GLB a medidas reales (metros para AR)")
    p.add_argument("entrada")
    p.add_argument("salida")
    p.add_argument("--alto", type=float, required=True)
    p.add_argument("--ancho", type=float, required=True)
    p.add_argument("--largo", type=float, required=True)
    p.add_argument("--alto-extra", type=float, default=0.0,
                   help="alto de algo por encima de la pieza (p. ej. un grifo) que no "
                        "cuenta como parte de --alto pero sí está en la malla")
    p.add_argument("--unidad", default="cm", choices=sorted(UNIDADES_M))
    p.add_argument("--modo", default="uniforme",
                   choices=["uniforme", "exacto", "alto", "ancho", "largo"])
    p.add_argument("--sin-ejes-auto", action="store_true")
    p.add_argument("--sin-anclar", action="store_true")
    a = p.parse_args()
    informe = escalar(Path(a.entrada), Path(a.salida), a.alto, a.ancho, a.largo, a.unidad,
                      a.modo, not a.sin_ejes_auto, not a.sin_anclar, alto_extra=a.alto_extra)
    print(json.dumps(informe, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
