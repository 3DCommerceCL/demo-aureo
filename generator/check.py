"""
Verifica que las claves de generator/.env estén bien cargadas y que las APIs respondan,
sin generar nada (no gasta dólares ni créditos).

  py -m generator.check            # solo lo local: qué claves ve el proceso
  py -m generator.check --ping     # además consulta cada API (endpoints de lectura, gratis)

Nunca imprime la clave completa: solo prefijo, longitud y sufijo.
"""

import argparse
import sys

from . import config, net


def enmascarar(clave):
    if not clave:
        return "— (vacía)"
    if len(clave) < 12:
        return f"{clave[:2]}…{clave[-2:]} ({len(clave)} car., parece corta)"
    return f"{clave[:6]}…{clave[-4:]} ({len(clave)} caracteres)"


def local():
    print(f"archivo .env: {config.ENV_FILE} "
          f"{'(existe)' if config.ENV_FILE.exists() else '(NO existe)'}")
    print(f"OPENAI_API_KEY: {enmascarar(config.OPENAI_API_KEY)}")
    print(f"  modelo {config.OPENAI_IMAGE_MODEL} · quality {config.OPENAI_IMAGE_QUALITY} "
          f"· size {config.OPENAI_IMAGE_SIZE}")
    print(f"MESHY_API_KEY:  {enmascarar(config.MESHY_API_KEY)}")
    print(f"  modelo {config.MESHY_AI_MODEL} · textura {config.MESHY_TEXTURE_RES} "
          f"· polycount {config.MESHY_POLYCOUNT}")
    print(f"Blender: {config.BLENDER_EXE} "
          f"{'(existe)' if __import__('pathlib').Path(config.BLENDER_EXE).exists() else '(NO existe)'}")
    faltan = [n for n, v in (("OPENAI_API_KEY", config.OPENAI_API_KEY),
                             ("MESHY_API_KEY", config.MESHY_API_KEY)) if not v]
    if faltan:
        print(f"\nFaltan: {', '.join(faltan)}. Copiá generator/.env.example a generator/.env "
              f"y completalas (o exportalas en el entorno).")
    return not faltan


def ping():
    ok = True
    if config.OPENAI_API_KEY:
        try:
            datos = net.get_json(f"{config.OPENAI_BASE}/models",
                                 {"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                                 timeout=60, reintentos=1)
            ids = [m["id"] for m in datos.get("data", [])]
            imagen = sorted(i for i in ids if "image" in i)
            print(f"OpenAI OK · {len(ids)} modelos visibles")
            print(f"  de imagen: {', '.join(imagen) or 'ninguno visible'}")
            if config.OPENAI_IMAGE_MODEL not in ids:
                print(f"  AVISO: {config.OPENAI_IMAGE_MODEL} no aparece en la lista de tu cuenta")
        except Exception as e:
            ok = False
            print(f"OpenAI FALLA · {e}")

    if config.MESHY_API_KEY:
        try:
            net.get_json(f"{config.MESHY_BASE}/openapi/v1/image-to-3d?page_size=1",
                         {"Authorization": f"Bearer {config.MESHY_API_KEY}"},
                         timeout=60, reintentos=1)
            print("Meshy OK · la clave responde el listado de tareas")
        except Exception as e:
            ok = False
            print(f"Meshy FALLA · {e}")
    return ok


def gastos():
    """Suma lo gastado leyendo los jobs y las pruebas que hay en disco."""
    import json
    usd = creditos = 0.0
    filas = []
    for f in sorted(config.RAIZ.glob("pruebas/*/[0-9][0-9][0-9].json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        usd += d.get("costo_usd", 0)
    for f in sorted(config.RAIZ.glob("jobs/*/job.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        c = (d.get("meshy") or {}).get("creditos") or 0
        creditos += c
        u = (d.get("imagen") or {}).get("costo_usd", 0)
        usd += u
        filas.append((d.get("slug", f.parent.name), c, u))

    print("\nGastos registrados en disco")
    print(f"  imágenes (OpenAI): US$ {usd:.2f}")
    print(f"  Meshy: {creditos:.0f} créditos "
          f"(~US$ {creditos * 0.02:.2f} al precio del plan Pro: 1000 créditos por US$20)")
    for slug, c, u in filas:
        print(f"    · {slug}: {c:.0f} créditos" + (f" + US$ {u:.3f} de imagen" if u else ""))
    print("  El análisis de objetos (visión) no está contabilizado: es de centésimas de centavo.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ping", action="store_true", help="consultar las APIs (lectura, gratis)")
    p.add_argument("--gastos", action="store_true", help="cuánto se lleva gastado en este repo")
    a = p.parse_args(argv)
    ok = local()
    if a.ping:
        print()
        ok = ping() and ok
    if a.gastos:
        gastos()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
