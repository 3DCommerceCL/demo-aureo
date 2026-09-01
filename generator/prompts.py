"""
Prompts de la limpieza de imagen (paso 1). Están acá y no dentro de image_prep.py
porque son LO QUE SE ITERA: la calidad del modelo 3D depende casi por completo de
esta imagen, así que se prueban variantes, se miran en la hoja de contacto y se ajusta.

En inglés a propósito: los modelos de imagen siguen mejor las instrucciones de
fidelidad ("do not invent details") en inglés.

Cómo agregar una variante nueva: sumá una entrada a VISTAS o a FONDOS y ya queda
disponible en `py -m generator.imagen --vista <nombre> --fondo <nombre>`.
"""

# Lo que NUNCA debe cambiar: el producto es el del cliente, no una reinterpretación.
FIDELIDAD = (
    "Preserve the object EXACTLY as in the reference: same geometry, same proportions, "
    "same colors, same materials and finish, same logos, seams, stitching, buttons and "
    "every design detail, including asymmetries and wear. Do not stylize, do not "
    "redesign, do not beautify, do not add or remove parts, do not add any text."
)

# Lo que necesita Meshy para reconstruir bien: objeto único, entero, nítido, sin sombras.
ENCUADRE = (
    "Exactly one object in frame, isolated, fully visible from top to bottom with an "
    "even margin of about 8% on every side, never cropped or touching the edges, "
    "centered. Even soft studio lighting with no cast shadow on the background, no "
    "contact shadow, no reflections, no mirror surface under it. Sharp focus across "
    "the whole object, high detail. Remove hands, mannequins, models, packaging, price "
    "tags, props, watermarks and any overlaid text or graphics."
)

# Cuando la foto es ambientada (el caso normal en e-commerce) hay que decir explícitamente
# cuál de todos los objetos es el producto; si no, el modelo elige por su cuenta.
#
# La excepción del grifo existe porque, sin ella, un mueble de cocina salía a veces con
# el grifo y a veces sin él: nada en el prompt distinguía "algo apoyado encima del
# producto" (se borra) de "hardware montado en el producto" (es parte de él). El modelo
# de imagen resolvía esa ambigüedad para cada lado según la foto, así que el resultado no
# era consistente. Un grifo atornillado a la mesada de un mueble de cocina es lo segundo:
# se queda, siempre, no según cómo interprete la imagen ese día.
OBJETIVO = (
    'The product to isolate is (described in Spanish): "{objeto}". That object is the ONLY '
    "thing that may remain in the image. Delete every other object of the scene — furniture, "
    "plants, lamps, rugs, curtains, artwork, people, packaging — including anything resting "
    "on top of the product, placed inside it or standing in front of it, and rebuild the "
    "parts of the product that they were covering. Shelves, drawers and interiors must end "
    "up empty. EXCEPTION: hardware and fixtures physically mounted onto the product itself — "
    "a faucet/tap on a kitchen or bathroom cabinet, handles, knobs, hinges — are part of the "
    "product, not a separate object resting on it. Never delete them; keep them exactly as "
    "they appear in the reference, in the same position."
)

# Recordatorio del hardware montado, repetido AL FINAL del prompt, después de la
# corrección libre que escribe el vendedor (--extra / "Corregir algo que entendió
# mal"). La excepción de OBJETIVO ya dice que no se borra, pero queda en la mitad
# del prompt; --extra va al final, y un pedido como "saca el mueble de arriba" se
# llevaba puesto el grifo también, porque el modelo de edición le da más peso a lo
# último que lee. Repetirlo después de --extra es lo que lo protege de una
# corrección que ni siquiera lo menciona.
RECORDATORIO_HARDWARE = (
    "Whatever the correction above asks for, it never includes removing hardware "
    "mounted on the product — a faucet/tap, handles, knobs, hinges. If the product has "
    "one, it must still be there afterwards, unchanged, no matter what else that "
    "correction changes."
)

VISTAS = {
    "frontal": (
        "Straight-on front elevation view: the camera is exactly at the object's "
        "mid-height and directly in front of it, lens axis perpendicular to the object's "
        "front face, no perspective distortion, no tilt, no rotation."
    ),
    "tres-cuartos": (
        "Three-quarter view: the camera is about 35 degrees to the object's left and "
        "about 20 degrees above it, so the front, one side and the top are all visible "
        "at once. Slight perspective, object upright and level."
    ),
    "lateral": (
        "Side elevation view: the camera is exactly at the object's mid-height, aimed at "
        "its left side, lens axis perpendicular to it, no perspective distortion, no tilt."
    ),
    "cenital": (
        "Top-down view: the camera is directly above the object looking straight down, "
        "the whole footprint visible, no tilt."
    ),
}

# (descripción para el prompt, valor del parámetro `background` de la API)
# OJO: gpt-image-2 NO acepta background=transparent (sí gpt-image-1 y 1.5). Cuando el
# modelo lo rechaza, image_prep pide la imagen sobre blanco y recorta el fondo localmente
# con recorte.py, así que `transparente` funciona igual con cualquier modelo.
FONDOS = {
    "blanco": ("a pure white (#FFFFFF) seamless background", "opaque"),
    "gris": ("a flat neutral light gray (#F2F2F2) seamless background", "opaque"),
    "transparente": ("a fully transparent background (alpha channel, nothing behind the "
                     "object)", "transparent"),
}

# Vistas extra para multi-image-to-3d (BETA): giros sobre el eje vertical.
VISTAS_EXTRA = [("derecha", 90), ("trasera", 180), ("izquierda", 270)]


# Las medidas reales dichas en el prompt: Meshy reconstruye la silueta que ve, así que la
# proporción de esta imagen es la proporción que va a tener la malla.
MEDIDAS = {
    "frontal": ("The real object measures {alto} cm high, {ancho} cm wide and {largo} cm "
                "deep. In this front view its silhouette must be exactly {r} times taller "
                "than wide. Respect that ratio precisely."),
    "lateral": ("The real object measures {alto} cm high, {ancho} cm wide and {largo} cm "
                "deep. In this side view its silhouette must be exactly {r} times taller "
                "than deep. Respect that ratio precisely."),
    "cenital": ("The real object measures {alto} cm high, {ancho} cm wide and {largo} cm "
                "deep. In this top view its footprint must be exactly {r} times deeper "
                "than wide. Respect that ratio precisely."),
    "tres-cuartos": ("The real object measures {alto} cm high, {ancho} cm wide and {largo} "
                     "cm deep. Keep those proportions: it must read as clearly "
                     "{r} times taller than wide, and its depth must look like {largo} cm, "
                     "not deeper. Do not make it look bulkier or slimmer than it is."),
}

# Para cuando la pieza tiene un accesorio de alto desconocido (--tiene-grifo) y nadie
# declaró --alto-extra: el "alto" que se conoce es solo el de la caja, pero la foto
# muestra la caja Y el accesorio juntos. Declarar una relación alto/ancho con ese alto
# es la misma foto achatada que --alto-extra existe para evitar cuando SÍ se conoce esa
# altura — acá no se conoce, así que mejor no declarar ninguna relación que dependa del
# alto: se pide ancho/largo nomás y se deja que la foto decida el alto sola. "cenital"
# no tiene entrada acá porque una vista desde arriba no ve el accesorio — su relación
# (largo/ancho) sigue siendo válida tal cual, con o sin grifo.
MEDIDAS_SIN_ALTO = {
    "frontal": ("The real object's main body is {ancho} cm wide and {largo} cm deep. It "
                "also has a faucet/tap (or similar fixture) mounted on top of it, whose "
                "height is not specified — draw it at whatever height looks proportionate "
                "in this photo. Do not stretch or compress the image to force any "
                "particular overall height."),
    "lateral": ("The real object's main body is {ancho} cm wide and {largo} cm deep. It "
                "also has a faucet/tap (or similar fixture) mounted on top of it, whose "
                "height is not specified — draw it at whatever height looks proportionate "
                "in this photo. Do not stretch or compress the image to force any "
                "particular overall height."),
    "tres-cuartos": ("The real object's main body is {ancho} cm wide and {largo} cm deep. "
                     "It also has a faucet/tap (or similar fixture) mounted on top of it, "
                     "whose height is not specified. Do not make the body look bulkier or "
                     "slimmer than its width/depth would suggest, and its depth must look "
                     "like {largo} cm, not deeper."),
}


def texto_medidas(vista, alto, ancho, largo, tiene_grifo=False):
    """Frase con las medidas reales (en cm) para meter en el prompt.

    tiene_grifo=True (ver --tiene-grifo, sin --alto-extra): usa MEDIDAS_SIN_ALTO en vez
    de MEDIDAS para frontal/lateral/tres-cuartos — ver el comentario ahí arriba. En
    "cenital" no hace diferencia."""
    if tiene_grifo and vista in MEDIDAS_SIN_ALTO:
        return MEDIDAS_SIN_ALTO[vista].format(ancho=_num(ancho), largo=_num(largo))

    plantilla = MEDIDAS.get(vista)
    if not plantilla:
        return ""
    relaciones = {"frontal": alto / ancho, "lateral": alto / largo,
                  "cenital": largo / ancho, "tres-cuartos": alto / ancho}
    return plantilla.format(alto=_num(alto), ancho=_num(ancho), largo=_num(largo),
                            r=round(relaciones[vista], 2))


def _num(v):
    return int(v) if float(v).is_integer() else round(float(v), 1)


def construir(vista="frontal", fondo="blanco", extra="", objeto="", medidas=""):
    """Prompt de la imagen principal. `objeto`: qué aislar cuando la foto es ambientada.
    `medidas`: frase de texto_medidas() con las proporciones reales."""
    if vista not in VISTAS:
        raise ValueError(f"vista desconocida: {vista} (hay: {', '.join(VISTAS)})")
    if fondo not in FONDOS:
        raise ValueError(f"fondo desconocido: {fondo} (hay: {', '.join(FONDOS)})")
    desc_fondo = FONDOS[fondo][0]
    partes = [
        f"Product catalog photo of the object in the reference image, isolated on {desc_fondo}.",
    ]
    if objeto:
        partes.append(OBJETIVO.format(objeto=objeto))
    partes.append(VISTAS[vista])
    if medidas:
        partes.append(medidas)
    partes += [ENCUADRE, FIDELIDAD]
    if extra:
        partes.append(extra)
        partes.append(RECORDATORIO_HARDWARE)
    return " ".join(partes)


def construir_vista_extra(nombre, grados, fondo="blanco", extra=""):
    """Prompt de una vista girada, con la imagen ya limpia como referencia."""
    desc_fondo = FONDOS[fondo][0]
    partes = [
        f"Same object as the reference image, same studio setup on {desc_fondo}: same "
        f"distance, same size in frame, same lighting, centered and fully visible.",
        f"Show it rotated exactly {grados} degrees to its right around its vertical axis, "
        f"so the camera sees its {nombre} side.",
        ENCUADRE,
        FIDELIDAD,
    ]
    if extra:
        partes.append(extra)
        partes.append(RECORDATORIO_HARDWARE)
    return " ".join(partes)


def modo_fondo(fondo):
    """Valor del parámetro `background` de la Images API."""
    return FONDOS[fondo][1]
