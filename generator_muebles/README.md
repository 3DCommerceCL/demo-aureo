# Generador AR especializado en muebles

Capa de política sobre [`generator/`](../generator/README.md). **No reimplementa el
pipeline**: importa la base como librería, decide los parámetros correctos para un mueble
y delega. Todo arreglo en la base (transparencia, proporción, escalado, Blender) llega acá
solo.

```powershell
py -m generator_muebles.cli --imagen "https://tienda.cl/vitrina.jpg" `
    --nombre "vitrina nordica" --alto 160 --ancho 80 --largo 40

# quedarse en la imagen, sin gastar créditos de Meshy
py -m generator_muebles.cli --imagen fotos\repisa.jpg --nombre repisa `
    --alto 30 --ancho 90 --largo 22 --solo-imagen
```

## Qué decide, y por qué

**Las medidas son obligatorias.** Un mueble siempre trae alto × ancho × fondo de catálogo;
sin eso no hay razón para usar este CLI en lugar del genérico. Con las medidas presentes,
el paso 1 corrige la proporción de la silueta y el paso 4 escala en **modo exacto**: sobre
una frontal ya corregida la deformación medida fue de 1.9%, imperceptible, y las medidas
quedan clavadas.

**La categoría se detecta sola**, en la **misma** llamada de visión que ya elegía cuál de
los objetos de la foto es el producto — no cuesta una llamada extra.
[`vision.py`](vision.py) extiende la instrucción y el esquema de la base
(`analizar.consultar_vision`) para pedir además `categoria`, `colocacion`,
`confianza_categoria` y `es_mueble`. El resultado sigue siendo compatible con
`analizar.elegir()`, así que la pregunta al usuario ante ambigüedad funciona igual.
`--categoria` la fuerza a mano.

**Cada categoría define cómo se coloca en AR.** Una repisa o una alacena van a la **pared**,
no al piso, y eso termina en el `ar-placement` del visor:

| categoría | colocación | vista por defecto | nota que entra al prompt |
|---|---|---|---|
| silla, banqueta, sillon, sofa | piso | frontal | patas completas y separadas; cojines sin deformar |
| mesa, cama | piso | **tres-cuartos** | de frente una mesa es casi una línea |
| escritorio, velador, comoda, vitrina, librero, closet | piso | frontal | cajones/puertas cerrados, estantes vacíos |
| **repisa, alacena, espejo** | **pared** | frontal | cara trasera plana, sin objetos encima |
| lampara_pie | piso | frontal | el tubo delgado no debe desaparecer |

Los defaults de categoría se pisan con `--vista` y `--fondo`.

## Estado

Probado end to end hasta el paso 1 con una foto ambientada real: detectó `vitrina` con 98%
de confianza, resolvió `ar-placement="floor"`, aplicó frontal + transparente + exacto,
metió la nota de categoría en el prompt y corrigió la proporción de 2.2056 a 2.005. Los
pasos 2-6 son los de la base, ya verificados ahí.

**Pendiente, en orden de valor:**

1. **Prompts por categoría** — hoy cada categoría aporta una sola línea de contexto. Falta
   el bloque completo por tipo de mueble.
2. **QC de mueble** — base plana (que apoye sin flotar en AR), simetría izquierda-derecha,
   patas presentes y no fusionadas, coherencia de la profundidad declarada con la malla.
3. **Lote por catálogo CSV** — nombre, URL, alto, ancho, fondo, categoría → una corrida con
   reanudación, control de gasto e informe. Es lo que lo vuelve ofrecible a una tienda con
   200 productos.

**Duda abierta a verificar en dispositivo:** para las categorías de pared, el modelo se
sigue anclando con la base en Y=0 y centrado en X/Z, como todos los demás. Falta comprobar
en un iPhone y un Android si Scene Viewer y Quick Look esperan el pivote en la cara
trasera; si es así, hay que agregar un modo de anclaje "pared" en `gltf_scale.escalar()`.
No lo implementé a ciegas.
