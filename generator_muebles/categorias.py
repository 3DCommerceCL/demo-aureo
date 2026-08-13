"""
Catálogo de categorías de mueble y sus defaults.

Un mueble no es un producto genérico: siempre viene con alto x ancho x fondo de catálogo,
casi siempre conviene la vista frontal (la silueta da alto y ancho exactos, y Meshy la
copia con ~2% de error) y la profundidad la fija el escalador, no la imagen. Además, la
mitad de las categorías no van al piso: una repisa o una alacena se colocan en la pared,
y eso cambia el `ar-placement` del visor.

Acá vive esa tabla. Los prompts detallados por categoría son el siguiente paso: por ahora
cada una aporta una sola línea de contexto (`nota`).
"""

# colocacion: cómo se ancla en AR ("piso" | "pared" | "superficie")
# vista/fondo/modo: defaults del paso 1 y del escalado
CATEGORIAS = {
    "silla": {
        "colocacion": "piso",
        "nota": "las cuatro patas completas, separadas y apoyadas; respaldo entero",
    },
    "banqueta": {
        "colocacion": "piso",
        "nota": "patas completas y separadas; asiento entero",
    },
    "sillon": {
        "colocacion": "piso",
        "nota": "cojines en su posición normal, sin arrugas exageradas; patas visibles",
    },
    "sofa": {
        "colocacion": "piso",
        "nota": "cojines en su posición normal; sin mantas ni almohadones sueltos",
    },
    "mesa": {
        "colocacion": "piso",
        "nota": "tablero de grosor constante; patas completas hasta el piso",
        "vista": "tres-cuartos",   # de frente una mesa es casi una línea
    },
    "escritorio": {
        "colocacion": "piso",
        "nota": "tablero de grosor constante; cajones cerrados",
    },
    "velador": {
        "colocacion": "piso",
        "nota": "cajones cerrados; tiradores fieles",
    },
    "comoda": {
        "colocacion": "piso",
        "nota": "todos los cajones cerrados y alineados; tiradores fieles",
    },
    "vitrina": {
        "colocacion": "piso",
        "nota": "puertas cerradas, estantes vacíos, vidrios limpios y transparentes",
    },
    "librero": {
        "colocacion": "piso",
        "nota": "estantes vacíos y de grosor constante",
    },
    "closet": {
        "colocacion": "piso",
        "nota": "puertas cerradas; sin ropa ni contenido",
    },
    "cama": {
        "colocacion": "piso",
        "nota": "sin ropa de cama arrugada; respaldo entero; patas visibles",
        "vista": "tres-cuartos",
    },
    "repisa": {
        "colocacion": "pared",
        "nota": "sin soportes improvisados ni objetos encima; cara trasera plana",
    },
    "alacena": {
        "colocacion": "pared",
        "nota": "puertas cerradas; cara trasera plana (va contra la pared)",
    },
    "espejo": {
        "colocacion": "pared",
        "nota": "marco fiel; el espejo como superficie neutra, sin reflejos de la escena",
    },
    "lampara_pie": {
        "colocacion": "piso",
        "nota": "pantalla y base completas; el tubo delgado no debe desaparecer",
    },
    "otro": {
        "colocacion": "piso",
        "nota": "",
    },
}

# Lo que vale para cualquier mueble salvo que la categoría diga otra cosa.
DEFAULTS = {
    "vista": "frontal",
    "fondo": "transparente",
    "modo": "exacto",          # las medidas son de catálogo: se calzan clavadas
    "colocacion": "piso",
    "nota": "",
}

AR_PLACEMENT = {"piso": "floor", "pared": "wall", "superficie": "floor"}


def lista():
    return sorted(CATEGORIAS)


def perfil(categoria):
    """Defaults efectivos de una categoría (los propios pisan a los generales)."""
    datos = dict(DEFAULTS)
    datos.update(CATEGORIAS.get(categoria or "", {}))
    datos["categoria"] = categoria if categoria in CATEGORIAS else "otro"
    datos["ar_placement"] = AR_PLACEMENT[datos["colocacion"]]
    return datos
