# Generador de modelos 3D para AR — foto de producto + medidas → GLB métrico

Complemento del try-on: acá **no** hay tracking. Este módulo convierte **una foto de
producto + alto/ancho/largo reales** en un GLB texturizado, a escala métrica y anclado
en su base, listo para cualquier visor AR (`<model-viewer>`, Scene Viewer, Quick Look).

```
foto ──(1 OpenAI)──> front.png            producto sobre blanco, vista frontal
      └(2 OpenAI, BETA)─> views/*.png     3 vistas inventadas (derecha/atrás/izquierda)
                     └──(3 Meshy)──> raw.glb        malla + textura, escala arbitraria
                          └──(4 gltf_scale)──> model.glb   metros reales, base en Y=0
                               ├─(5 Blender, opcional)─> model.usdz + 4 renders de control
                               └─(6 gltf-transform, opc.)─> model_web.glb (liviano para la web)
```

## Instalación

```powershell
copy generator\.env.example generator\.env      # y completar las dos claves
notepad generator\.env
py -m generator.check --ping                    # verifica claves y APIs sin gastar nada
```

`generator/.env` está en `.gitignore`: las claves nunca salen de tu máquina. Si alguna vez
se filtra una, se revoca en el panel del proveedor y se genera otra — no alcanza con
borrarla del archivo.

Sin dependencias de Python: todo corre con la stdlib (`py -m generator.pipeline`).
Opcionales: **Blender 5.1** (verificación independiente + USDZ + previews) y **Node/npx**
(compresión web con `@gltf-transform/cli`).

## Pasos 0-1: iterar la imagen (empezá por acá)

La calidad del modelo 3D la decide esta imagen, no Meshy. Por eso tiene su propio CLI,
independiente del resto: identifica el producto, genera variantes, las puntúa y arma una
**hoja de contacto** para revisarlas a ojo. No toca Meshy ni gasta créditos.

```powershell
# 1. ver los prompts exactos y el costo SIN gastar nada
py -m generator.imagen --imagen fotos\silla.jpg --nombre silla-nordica --dry-run

# 2. comparar variantes de una (producto cartesiano vista × fondo × --n).
#    --imagen acepta archivo, carpeta o URL de la ficha del producto
py -m generator.imagen --imagen "https://tienda.cl/foto.jpg" --nombre silla-nordica `
    --vista frontal tres-cuartos --fondo blanco transparente --abrir

# 3. corregir lo que el modelo entendió mal, sin tocar código
py -m generator.imagen --imagen fotos\silla.jpg --nombre silla-nordica `
    --extra "the seat is dark walnut, not black; the legs are matte steel"

# 4. lote: una carpeta entera de fotos, una variante cada una
py -m generator.imagen --imagen fotos\catalogo --vista frontal --fondo blanco --auto
```

### Paso 0 — ¿cuál de los objetos de la foto es el producto?

Las fotos de e-commerce casi nunca son de estudio: el producto está ambientado entre
plantas, lámparas y sofás, y con cosas apoyadas encima o adentro. Antes de limpiar nada,
[analizar.py](analizar.py) mira la foto con un modelo de visión (`gpt-5.4-mini`, unas
décimas de centavo) y enumera los objetos candidatos:

```
  Escena: Una vitrina blanca moderna domina la escena, rodeada por una planta grande…
   1. vitrina blanca (centro de la imagen)  <- probable producto
   2. planta en maceta (izquierda, delante de la vitrina)
   3. lámpara de pie (derecha, junto a la pared)
   ...
   o. otro (describilo vos)
  ¿Cuál es el producto? [número / o]
```

- Si hay **un solo candidato claro**, sigue sin preguntar (avisa cuántos objetos va a borrar).
- Si hay **ambigüedad**, pregunta con la lista + opción `o` para describirlo con tus palabras.
- `--objeto "la vitrina blanca"` saltea la pregunta · `--auto` toma el más probable (para
  lotes) · `--confirmar` pregunta siempre · `--sin-analisis` desactiva el paso 0.
- El análisis se cachea en `analisis.json` y no se repite; `--reanalizar` lo rehace.

El objeto elegido entra al prompt como "esto es lo único que queda, todo lo demás se
borra, incluido lo que esté apoyado encima o adentro, y se reconstruye lo que tapaban".

Todo queda en `generator/pruebas/<slug>/`: `NNN_<vista>_<fondo>.png` + `NNN.json`
(prompt exacto, modelo, quality, costo, QC y el comando para repetirlo) + `index.html`.
**Nada se sobrescribe**: cada corrida agrega intentos numerados, así se puede comparar el
intento 7 contra el 2. En la hoja, clic en una imagen la agranda y el botón `fondo`
alterna damero/blanco/negro — imprescindible para ver la silueta de productos claros.

**Variantes disponibles** (`prompts.py`, ahí se agregan más):

| `--vista` | | `--fondo` | |
|---|---|---|---|
| `frontal` | alzado frontal, sin perspectiva | `blanco` | #FFFFFF opaco |
| `tres-cuartos` | 35° lateral + 20° arriba: muestra volumen | `gris` | #F2F2F2, para productos blancos |
| `lateral` | perfil | `transparente` | canal alfa, silueta sin ambigüedad |
| `cenital` | desde arriba | | |

`--fondo transparente` funciona con cualquier modelo: **gpt-image-2 no acepta
`background=transparent`** (sí gpt-image-1 y 1.5), así que cuando la API lo rechaza se
pide la imagen sobre blanco y el fondo se recorta localmente con
[recorte.py](recorte.py). El recorte es por relleno desde los bordes, no por umbral de
color: eso es lo que permite recortar un producto **blanco sobre fondo blanco** sin
comérselo, y conserva los interiores encerrados (el vidrio de una vitrina, el hueco de
un asa).

Vale la pena medir `frontal` vs `tres-cuartos` con el mismo producto: una vista frontal
pura le da poca información de profundidad a Meshy, y el 3/4 suele reconstruir mejor —
pero deja el "frente" del modelo girado, cosa que el escalador del paso 4 detecta y
corrige (`ejes_permutados`).

### Proporción real: que Meshy no invente las medidas

Meshy reconstruye la **silueta** que ve. Si la imagen limpia sale con otra relación
alto/ancho que la real, la malla nace deformada y después hay que estirarla al escalar.
Sale mucho más barato arreglarlo en la imagen. Pasale las medidas al paso 1:

```powershell
py -m generator.imagen --imagen fotos\vitrina.jpg --nombre vitrina `
    --alto 160 --ancho 80 --largo 40 --unidad cm --vista frontal --fondo transparente
```

Con las medidas presentes pasan tres cosas:

1. **Lienzo acorde**: se elige `1024x1536`, `1024x1024` o `1536x1024` según la silueta
   esperada, en vez del cuadrado fijo. El producto entra más grande y con menos margen
   desperdiciado (`--size` sigue mandando si lo forzás).
2. **Prompt con las medidas**: "the real object measures 160 cm high, 80 cm wide and 40 cm
   deep; in this front view its silhouette must be exactly 2.0 times taller than wide".
3. **Corrección geométrica**: se mide la silueta generada y se reescala la imagen para que
   la relación quede exacta. El original queda guardado como `NNN_..._crudo.png`.

El punto 3 es el que realmente funciona: en la prueba real la frontal salió 2.28 cuando
debía ser 2.00 (14% de error pese a pedírselo en el prompt) y la corrección la dejó en
2.005.

**Y Meshy copia esa proporción.** Medido sobre el mismo producto, misma foto de origen:

| | imagen de entrada | alto/ancho de la malla | deformación al calzar medidas |
|---|---|---|---|
| sin control | vista 3/4 | 2.165 (**+8.2%** vs 2.00) | 8.2% (visible) |
| con corrección | frontal a 2.00 | 2.038 (**+1.9%**) | 1.9% (imperceptible) |

O sea: la silueta de la imagen se transfiere a la malla con ~2% de error. Lo que se
controla en la imagen, se controla en el 3D.

**Freno de seguridad** (`--max-correccion`, default 1.25): si haría falta deformar más que
eso, **no se corrige** y se marca en rojo en la hoja de contacto. Una corrección enorme casi
nunca significa "dibujó mal la proporción": significa que dibujó **otra vista** o que las
medidas no son de ese producto. Pasó en la prueba: pedimos `lateral`, el modelo devolvió
otro frontal, y sin el freno se habría apretado a 4:1 fabricando una imagen falsa que Meshy
habría copiado.

**Qué eje corrige cada cosa.** La imagen solo puede fijar lo que se ve en la silueta:
en la frontal, alto y ancho. La **profundidad no está en una vista frontal** y la vista
lateral generada no es confiable (ver arriba). Para la profundidad, la herramienta correcta
es el escalador: con la frontal ya corregida, `--modo exacto` en el paso 4 escala X e Y por
factores casi idénticos (sin deformación visible) y deja que Z absorba la profundidad de
catálogo. División de trabajo: **la imagen fija alto/ancho, el escalador fija la
profundidad.**

**QC automático** (Pillow + numpy, opcional): mide ocupación del frame, recorte contra
los bordes, centrado y limpieza del fondo, y marca cada intento `ok` o `revisar`. No
reemplaza tu revisión: sirve para que mires primero los que están marcados. Si mejorás
las reglas en `qc.py`, `--qc-solo` recalcula todo sin volver a llamar a la API.

**Qué mirar antes de aprobar una imagen** (esto decide el 3D):

1. ¿Es el **mismo producto**? Comparar contra `input.png` — el modelo tiende a
   "mejorar" formas, inventar herrajes o cambiar terminaciones.
2. ¿Está **entero** y con aire alrededor? Un objeto cortado por el borde produce una malla
   cortada.
3. ¿El **fondo** está limpio? Sombra de contacto o degradado = geometría fantasma.
4. ¿Se distingue la **silueta**? Producto blanco sobre blanco → probá `--fondo gris` o
   `transparente`.
5. ¿Hay **detalle** suficiente? Si el objeto ocupa menos del 20-25% del frame, Meshy pierde
   textura.

Cuando una imagen te convence, pasala al pipeline sin regenerarla:

```powershell
py -m generator.pipeline --imagen fotos\silla.jpg --alto 82 --ancho 45 --largo 50 `
    --nombre silla-nordica --imagen-limpia generator\pruebas\silla-nordica\007_frontal_gris.png
```

## Pipeline completo

```powershell
# ver el plan y el costo sin gastar nada
py -m generator.pipeline --imagen fotos\silla.jpg --alto 82 --ancho 45 --largo 50 --dry-run

# corrida normal (1 vista → Meshy image-to-3d)
py -m generator.pipeline --imagen fotos\silla.jpg --alto 82 --ancho 45 --largo 50 --nombre silla-nordica

# completa: 4 vistas (BETA) + USDZ/previews en Blender + GLB liviano
py -m generator.pipeline --imagen fotos\silla.jpg --alto 82 --ancho 45 --largo 50 `
    --nombre silla-nordica --vistas 4 --blender --web
```

Todo queda en `generator/jobs/<slug>/`:

| archivo | qué es |
|---|---|
| `input.*`, `front.png`, `views/*.png` | entrada y las imágenes que vio Meshy |
| `raw.glb` | lo que devolvió Meshy (sin escala real) |
| **`model.glb`** | **el entregable**: metros, base en Y=0, centrado en X/Z |
| `model_web.glb` | igual pero comprimido (draco + webp 1024) para servir en la web |
| `model.usdz` | para AR Quick Look de iOS (opcional; model-viewer también lo genera al vuelo) |
| `preview.html` | página de control: visor AR + tabla medidas declaradas vs medidas reales |
| `job.json`, `meshy_task.json`, `blender_check.json` | trazabilidad completa del job |

El estado vive en `job.json`: **volver a correr el mismo comando no repite pasos ya
hechos** (no se regastan créditos). Para rehacer desde un punto: `--forzar imagen |
vistas | malla | escala | blender | web`.

## Costos por producto

| paso | costo |
|---|---|
| análisis de objetos (`gpt-5.4-mini`, paso 0) | centésimas de centavo · se cachea, no se repite |
| limpieza de imagen (`gpt-image-2`, medium 1024²) | ~US$0.05 · `low` ~US$0.006 · con `--vistas 4` son 4 |
| Meshy `image-to-3d` o `multi-image-to-3d` (meshy-6) | **30 créditos ≈ US$0.60** (plan Pro: 1000 créditos por US$20) |
| escala / Blender / compresión | $0 (local, repetible) |

**Un producto = una reconstrucción de Meshy.** Los 30 créditos se gastan una sola vez por
producto, y `multi-image-to-3d` con 2-4 imágenes cuesta lo mismo que con 1. Lo que evita
gastarlos de más:

- **El paso 1 es un CLI aparte a propósito**: se itera la imagen por centavos y recién
  cuando convence se paga la malla.
- **El job es reanudable**: `raw.glb` se reutiliza y el `task_id` se persiste apenas se
  crea la tarea, así que un corte de luz o un timeout retoman la tarea ya pagada en vez de
  crear otra.
- **Los pasos 4-6 son gratis**: cambiar medidas, modo de ajuste, USDZ o compresión es
  `--forzar escala` y cuesta $0.
- **Confirmación antes de gastar**: el pipeline pregunta antes de crear una tarea nueva
  (`--si` la saltea; en background no pregunta para no quedarse colgado).
- `py -m generator.check --gastos` suma lo gastado leyendo los jobs del disco.

Solo se paga una segunda reconstrucción si la imagen resulta mala y hay que rehacer la
malla (`--forzar malla`).

Bajar a `OPENAI_IMAGE_QUALITY=low` deja la imagen en ~US$0.006; para catálogos grandes
conviene medir primero si `low` alcanza para que Meshy reconstruya bien.

## Decisiones de diseño (leer antes de tocar)

**El escalado NO reexporta la malla.** `gltf_scale.py` mide el bounding box leyendo
`accessor.min/max` del GLB, inserta **un nodo raíz** con escala + traslación y reescribe
el contenedor dejando el chunk binario intacto. Reimportar/reexportar en Blender
reescribiría materiales, texturas y extensiones `KHR_*` y suele degradar el PBR que
justamente pagamos en Meshy. Blender entra después, solo para **medir por segunda vía**
(si las dos mediciones coinciden, la escala es correcta), exportar USDZ y renderizar.

**`--modo uniforme` (default) no deforma**: reparte el error entre los 3 ejes por mínimos
cuadrados en log. Si la malla no tiene las proporciones declaradas, el pipeline avisa con
el desvío por eje. Un desvío grande casi siempre significa foto en perspectiva fuerte o
medidas mal tomadas — se arregla con una foto mejor, no con el escalador. `--modo exacto`
calza los 3 ejes clavados pero deforma el modelo; `--modo alto|ancho|largo` escala
uniforme tomando ese eje como referencia.

**Ancho/largo se auto-permutan.** El "frente" que interpreta Meshy no siempre queda en el
mismo eje. Si las proporciones de la malla encajan mejor cruzadas, el script permuta X/Z y
lo reporta (`ejes_permutados: true`). Se desactiva con `--sin-ejes-auto`.

**Las 3 vistas extra son BETA y pueden empeorar el resultado.** Son alucinaciones
plausibles del modelo de imagen, no información real del producto: sirven en objetos
simétricos y sencillos, y meten geometría inventada en objetos con detalle asimétrico.
Antes de usarlas por defecto en un catálogo, comparar 1 vista vs 4 sobre los mismos 5
productos.

**Relación con la regla dura #1 del proyecto** ("nunca reescalar/reposicionar GLBs en
scripts"): esa regla protege los assets del pipeline de entrenamiento, alineados a mano en
Blender. Acá el reescalado **es** el entregable y se aplica a un GLB recién salido de
Meshy que nadie alineó. Son dos flujos distintos: `training/` sigue sin tocar escalas.

## Control de calidad (qué mirar en `preview.html`)

1. **Medidas**: la tabla debe dar error < 1% en los 3 ejes (verde). Con `--blender`, la
   medición independiente de Blender queda en `blender_check.json` y debe coincidir.
2. **Apoyo**: el modelo tiene que descansar sobre el piso en el visor (base en Y=0) y no
   flotar ni hundirse.
3. **Escala percibida en AR**: `ar-scale="fixed"` está activado a propósito — en el móvil
   el objeto debe verse del tamaño real, no "de juguete".
4. **Textura**: Meshy inventa lo que no ve. Revisar la cara trasera y las zonas ocultas en
   la foto original antes de publicar.
5. **Peso**: `model_web.glb` debería quedar por debajo de ~4 MB para móvil en 4G.

## Estructura

| archivo | rol |
|---|---|
| `imagen.py` | **CLI de los pasos 0-1** (iteración de imágenes, es lo que se usa primero) |
| `analizar.py` | paso 0: qué objetos hay en la foto y cuál es el producto |
| `prompts.py` | los prompts y sus variantes — lo que se itera |
| `qc.py` | control de calidad automático de la imagen |
| `proporcion.py` | medición y corrección de la silueta contra las medidas reales |
| `recorte.py` | fondo transparente por relleno desde los bordes |
| `check.py` | verificación de claves y APIs |
| `image_prep.py` | llamadas a la Images API de OpenAI |
| `meshy.py` | llamadas a la API de Meshy |
| `gltf_scale.py` | escalado métrico del GLB (stdlib pura) |
| `blender_finish.py` | verificación en Blender + USDZ + renders |
| `preview.py` | hoja de contacto del paso 1 + página de control del job 3D |
| `pipeline.py` | orquestador de los 6 pasos |

## Estado

Probado end-to-end con un GLB sintético de geometría conocida: medición del bbox con
jerarquía de nodos y cuaterniones, escalado a medidas declaradas (uniforme/exacto/
permutación de ejes), anclaje en la base, verificación independiente en Blender 5.1
(0.00% de error en los 3 ejes), export USDZ, 4 renders y compresión con gltf-transform.

**Pipeline completo probado con llamadas reales** (2026-08-12, foto ambientada de una
vitrina de Falabella, ~US$0.03 + 30 créditos):

- Pasos 0-1: descarga por URL, análisis de objetos (identificó la vitrina como producto y
  4 distractores), 4 variantes (frontal/blanco, frontal/gris, frontal y tres-cuartos
  transparentes), recorte local de fondo, QC en todas. La limpieza borró planta, lámpara,
  mesa y sofá, y dejó los estantes vacíos.
- Paso 3: Meshy meshy-6 desde la vista de tres cuartos → `raw.glb` de 2.55 MB en ~2 min
  (30 créditos). Reconstruyó puertas de vidrio, 4 estantes, tirador y patas inclinadas.
- Pasos 4-6: escala métrica, verificación en Blender (coincide con `gltf_scale` en los 3
  ejes hasta el sexto decimal), USDZ, 4 renders y `model_web.glb` de 1.5 MB.
- Tiempo total: 148 s.
- Segunda corrida con la frontal corregida a 2.00 + `--modo exacto`: `model.glb` de
  1.60 × 0.80 × 0.40 m exactos (0.00% de error en los 3 ejes, confirmado por Blender),
  `model_web.glb` de 0.89 MB. La profundidad la fijó el escalador, no la imagen.

**Sin verificar con llamadas reales**: el paso 2 (vistas extra BETA) y el flujo
multi-image de Meshy.
