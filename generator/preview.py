"""
Páginas de revisión:
  escribir()       — control del job 3D: visor AR + medidas declaradas vs reales.
  hoja_contacto()  — hoja de contacto del paso 1: todos los intentos de imagen de un
                     producto, con su prompt, su QC y su costo, para comparar a ojo.
  hoja_indice()    — índice de productos con pruebas de imagen.
"""

import json
from pathlib import Path

MV = "https://cdn.jsdelivr.net/npm/@google/model-viewer@4.3.1/dist/model-viewer.min.js"


def escribir(job: Path, meta: dict):
    esc = meta.get("escala", {})
    err = esc.get("error_pct", {})
    med = esc.get("medido_m", {})
    obj = esc.get("objetivo_m", {})
    unidad = meta.get("medidas", {}).get("unidad", "cm")

    filas = ""
    for clave in ("alto", "ancho", "largo"):
        if clave not in obj:
            continue
        e = err.get(clave, 0.0)
        clase = "ok" if abs(e) <= 1.0 else ("aviso" if abs(e) <= 5.0 else "mal")
        filas += (f"<tr><td>{clave}</td><td>{meta['medidas'][clave]} {unidad}</td>"
                  f"<td>{obj[clave]:.4f} m</td><td>{med.get(clave, 0):.4f} m</td>"
                  f"<td class='{clase}'>{e:+.2f}%</td></tr>")

    imgs = ""
    for ruta in ["input" + meta.get("ext_entrada", ".jpg"), "front.png",
                 "views/derecha.png", "views/trasera.png", "views/izquierda.png"]:
        if (job / ruta).exists():
            imgs += f"<figure><img src='{ruta}' alt='{ruta}'><figcaption>{ruta}</figcaption></figure>"
    for nombre in (meta.get("blender", {}) or {}).get("previews", []) or []:
        imgs += f"<figure><img src='{nombre}' alt='{nombre}'><figcaption>{nombre}</figcaption></figure>"

    ios = ' ios-src="model.usdz"' if (job / "model.usdz").exists() else ""
    # floor por defecto; los perfiles especializados lo pisan (una repisa va a la pared)
    colocacion = meta.get("ar_placement", "floor")
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{meta.get('nombre', 'modelo')} — control de calidad</title>
<script type="module" src="{MV}"></script>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #111; color: #eee; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.sub {{ color: #999; margin: 0 0 16px; font-size: 13px; }}
  model-viewer {{ width: 100%; height: 60vh; background: #1c1c1e; border-radius: 12px; }}
  table {{ border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #333; padding: 6px 12px; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  .ok {{ color: #4ade80; }} .aviso {{ color: #fbbf24; }} .mal {{ color: #f87171; }}
  figure {{ margin: 0; width: 180px; }}
  figure img {{ width: 100%; border-radius: 8px; background: #fff; }}
  figcaption {{ font-size: 11px; color: #888; text-align: center; }}
  #tiras {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  pre {{ background: #1c1c1e; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; }}
</style>
</head>
<body>
<h1>{meta.get('nombre', 'modelo')}</h1>
<p class="sub">Generado por el pipeline imagen+medidas → 3D. Escala {esc.get('modo', '?')}
 · {meta.get('vistas', 1)} vista(s) → Meshy {meta.get('meshy', {}).get('endpoint', '')}</p>

<model-viewer src="model.glb"{ios} alt="{meta.get('nombre', 'modelo')}"
  ar ar-modes="webxr scene-viewer quick-look" ar-scale="fixed" ar-placement="{colocacion}"
  camera-controls touch-action="pan-y" shadow-intensity="1" exposure="1"
  environment-image="neutral" poster-color="transparent"></model-viewer>

<table>
  <tr><th>eje</th><th>declarado</th><th>objetivo</th><th>en el GLB</th><th>error</th></tr>
  {filas}
</table>

<div id="tiras">{imgs}</div>

<h2 style="font-size:15px">metadatos</h2>
<pre>{json.dumps(meta, indent=2, ensure_ascii=False)}</pre>
</body>
</html>
"""
    destino = job / "preview.html"
    destino.write_text(html, encoding="utf-8")
    return destino


# --------------------------------------------------------------- hoja de contacto (paso 1)
ESTILO_HOJA = """
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 20px; background: #111; color: #eee; }
  h1 { font-size: 20px; margin: 0 0 2px; }
  .sub { color: #999; font-size: 13px; margin: 0 0 18px; }
  .barra { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; margin-bottom: 18px;
           background: #1a1a1d; padding: 12px 16px; border-radius: 10px; }
  .barra img { height: 84px; border-radius: 6px; }
  .barra b { color: #fff; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
  .card { background: #1a1a1d; border-radius: 10px; overflow: hidden; border: 1px solid #2a2a2e; }
  .card.ultimo { border-color: #4ade80; }
  .lienzo { position: relative; cursor: zoom-in;
            background-image: linear-gradient(45deg, #3a3a3a 25%, transparent 25%),
                              linear-gradient(-45deg, #3a3a3a 25%, transparent 25%),
                              linear-gradient(45deg, transparent 75%, #3a3a3a 75%),
                              linear-gradient(-45deg, transparent 75%, #3a3a3a 75%);
            background-size: 16px 16px;
            background-position: 0 0, 0 8px, 8px -8px, -8px 0px; }
  .lienzo.blanco { background: #fff; } .lienzo.negro { background: #000; }
  .lienzo img { width: 100%; display: block; }
  .meta { padding: 10px 12px; font-size: 12px; line-height: 1.5; }
  .tags span { display: inline-block; background: #2a2a2e; border-radius: 4px; padding: 1px 7px;
               margin: 0 4px 4px 0; font-size: 11px; }
  .ok { color: #4ade80; } .revisar { color: #fbbf24; } .malo { color: #f87171; }
  ul.avisos { margin: 6px 0 0; padding-left: 16px; color: #fbbf24; }
  details { margin-top: 8px; } summary { cursor: pointer; color: #888; }
  details pre { white-space: pre-wrap; background: #111; padding: 8px; border-radius: 6px;
                font-size: 11px; color: #bbb; margin: 6px 0 0; }
  .lupa { position: fixed; inset: 0; background: rgba(0,0,0,.92); display: none;
          align-items: center; justify-content: center; z-index: 9; cursor: zoom-out; }
  .lupa img { max-width: 96vw; max-height: 96vh; }
  button.fondo { background: #2a2a2e; color: #ccc; border: 0; border-radius: 4px;
                 padding: 2px 8px; font-size: 11px; cursor: pointer; }
"""

SCRIPT_HOJA = """
  document.querySelectorAll('.lienzo').forEach(el => el.addEventListener('click', () => {
    const lupa = document.getElementById('lupa');
    lupa.firstElementChild.src = el.querySelector('img').src;
    lupa.style.display = 'flex';
  }));
  document.getElementById('lupa').addEventListener('click', e => e.currentTarget.style.display = 'none');
  document.querySelectorAll('button.fondo').forEach(b => b.addEventListener('click', () => {
    const l = b.closest('.card').querySelector('.lienzo');
    const ciclo = ['', 'blanco', 'negro'];
    const i = (ciclo.indexOf(l.classList.contains('blanco') ? 'blanco' :
               l.classList.contains('negro') ? 'negro' : '') + 1) % 3;
    l.className = 'lienzo ' + ciclo[i];
  }));
"""


def _card(d, ultimo=False):
    qc = d.get("qc") or {}
    avisos = qc.get("avisos", [])
    clase = "ok" if qc.get("veredicto") == "ok" else ("revisar" if avisos else "")
    linea_qc = ""
    if qc.get("disponible"):
        linea_qc = (f"<div class='{clase}'>QC: {qc.get('veredicto')} · ocupa "
                    f"{qc.get('ocupacion_pct')}% del frame"
                    f"{' · con alpha' if qc.get('alpha') else ''}</div>")
        if avisos:
            linea_qc += "<ul class='avisos'>" + "".join(f"<li>{a}</li>" for a in avisos) + "</ul>"
    obj = d.get("objeto_elegido") or {}
    linea_obj = ""
    if obj.get("nombre"):
        linea_obj = (f"<div style='color:#93c5fd'>aislado: {obj['nombre']}"
                     f"<span style='color:#666'> · {obj.get('origen', '')}</span></div>")
    prop = d.get("proporcion") or {}
    if prop.get("aplicado"):
        linea_obj += (f"<div style='color:#c4b5fd'>proporción corregida: "
                      f"{prop['relacion_antes']} → {prop['relacion_despues']} "
                      f"(objetivo {prop['relacion_objetivo']}, eje {prop['eje_ajustado']})</div>")
    elif prop.get("motivo", "").startswith("haría falta"):
        linea_obj += (f"<div style='color:#f87171'>proporción SIN corregir: "
                      f"{prop['relacion_antes']} vs objetivo {prop['relacion_objetivo']} — "
                      f"{prop['motivo']}</div>")
    elif prop:
        linea_obj += (f"<div style='color:#6ee7b7'>proporción ya correcta "
                      f"({prop.get('relacion_antes')} vs {prop.get('relacion_objetivo')})</div>")
    return f"""
  <div class="card{' ultimo' if ultimo else ''}">
    <div class="lienzo"><img src="{d['archivo']}" alt="{d['archivo']}" loading="lazy"></div>
    <div class="meta">
      <div class="tags">
        <span>#{d['n']:03d}</span><span>{d.get('vista', '?')}</span><span>fondo {d.get('fondo', '?')}</span>
        <span>{d.get('quality', '?')}</span><span>{d.get('size', '?')}</span>
        <span>US$ {d.get('costo_usd', 0):.3f}</span>
        <button class="fondo" type="button">fondo</button>
      </div>
      {linea_obj}
      {linea_qc}
      <details><summary>prompt</summary><pre>{d.get('prompt', '')}</pre></details>
      <details><summary>repetir / variar</summary><pre>{d.get('comando', '')}</pre></details>
    </div>
  </div>"""


def hoja_contacto(carpeta: Path, nombre=""):
    """Regenera carpeta/index.html leyendo todos los NNN.json de intentos."""
    intentos = []
    for f in sorted(carpeta.glob("[0-9][0-9][0-9].json")):
        try:
            intentos.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    intentos.sort(key=lambda d: d.get("n", 0), reverse=True)
    total = sum(d.get("costo_usd", 0) for d in intentos)
    entrada = next((p.name for p in carpeta.glob("input.*")), "")
    cards = "".join(_card(d, i == 0) for i, d in enumerate(intentos))

    escena = ""
    f_analisis = carpeta / "analisis.json"
    if f_analisis.exists():
        try:
            an = json.loads(f_analisis.read_text(encoding="utf-8"))
            objetos = "".join(
                f"<li>{'<b>' if o.get('es_producto_probable') else ''}{o['nombre']}"
                f"{'</b>' if o.get('es_producto_probable') else ''} "
                f"<span style='color:#777'>· {o['ubicacion']}</span></li>"
                for o in an.get("objetos", []))
            escena = (f"<details><summary style='color:#888'>objetos detectados en la foto "
                      f"({len(an.get('objetos', []))})</summary>"
                      f"<div style='font-size:12px'>{an.get('resumen', '')}</div>"
                      f"<ul style='font-size:12px'>{objetos}</ul></details>")
        except json.JSONDecodeError:
            pass

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{nombre or carpeta.name} — pruebas de imagen</title>
<style>{ESTILO_HOJA}</style></head>
<body>
<h1>{nombre or carpeta.name}</h1>
<p class="sub">Paso 1: limpieza de la foto para Meshy. Clic en una imagen para agrandarla;
 el botón <b>fondo</b> alterna damero/blanco/negro (sirve para ver la silueta de productos claros).</p>
<div class="barra">
  {f'<img src="{entrada}" alt="original">' if entrada else ''}
  <div><b>{len(intentos)}</b> intento(s) · gastado <b>US$ {total:.2f}</b><br>
  <span class="sub">original: {entrada or '—'}</span>
  {escena}</div>
</div>
<div class="grid">{cards}</div>
<div class="lupa" id="lupa"><img alt="zoom"></div>
<script>{SCRIPT_HOJA}</script>
</body></html>
"""
    destino = carpeta / "index.html"
    destino.write_text(html, encoding="utf-8")
    return destino


def hoja_indice(raiz: Path):
    """Índice de productos con pruebas de imagen."""
    filas = ""
    for carpeta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        n = len(list(carpeta.glob("[0-9][0-9][0-9].json")))
        if not n:
            continue
        filas += (f"<li><a href='{carpeta.name}/index.html'>{carpeta.name}</a> "
                  f"<span style='color:#888'>· {n} intento(s)</span></li>")
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Pruebas de imagen</title>
<style>{ESTILO_HOJA}</style></head>
<body><h1>Pruebas de imagen</h1>
<p class="sub">Paso 1 del generador. Un producto por carpeta.</p>
<ul>{filas or '<li>todavía no hay pruebas</li>'}</ul></body></html>
"""
    destino = raiz / "index.html"
    destino.write_text(html, encoding="utf-8")
    return destino
