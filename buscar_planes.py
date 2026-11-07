#!/usr/bin/env python3
"""Busca planes con niños (1 a 4 años) en la Región de Murcia y actualiza docs/planes.json.

Uso normal (lo hace GitHub cada semana):   python buscar_planes.py
Pruebas sin gastar API:                    python buscar_planes.py --respuesta prueba.txt --hoy 2026-11-07 --sin-verificar
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).parent
SALIDA = RAIZ / "docs" / "planes.json"
MODELO = os.environ.get("MODELO", "claude-sonnet-5-5")
MAX_BUSQUEDAS = int(os.environ.get("MAX_BUSQUEDAS", "12"))   # tope de búsquedas web por ejecución (controla el coste)
DIAS = int(os.environ.get("DIAS", "14"))                      # cuántos días hacia delante se buscan
MIN_EVENTOS = 3                                               # si salen menos, se considera un fallo y no se guarda

TIPOS = ["Música/Concierto", "Teatro/Musical", "Mercadillo/Mercado", "Naturaleza/Granja", "Parque/Infantil",
         "Festival", "Taller", "Zoo/Animales", "Cultura/Museo", "Magia/Circo", "Deporte", "Gastronomía",
         "Exposición", "Otro"]

PROMPT = """Hoy es {HOY_TXT}. Busca en internet planes y actividades para niños de 1 a 4 años en la Región de Murcia (España), entre el {DESDE} y el {HASTA}. Da prioridad a los fines de semana.

FUENTES: agendas municipales (Murcia, Cartagena, Molina de Segura, Lorca, Alcantarilla y otros municipios), La Verdad, 7 Región, Murcia Turismo, webs de teatros y auditorios (Teatro Romea, Teatro Circo Murcia, Teatro Circo Apolo, Auditorio El Batel), Eventbrite, El Corte Inglés Entradas, Atrápalo, webs oficiales de festivales, y webs de parques, granjas y mercadillos. Haz búsquedas concretas (por ejemplo "teatro infantil Murcia {MES}", "mercadillo Murcia fin de semana", "festival infantil Murcia {MES}", "talleres infantiles gratis Murcia") y abre la web oficial del evento cuando puedas.

TIPOS: mercadillos y mercados, conciertos familiares, teatro infantil, títeres y circo, festivales (si tienen varias funciones, cada función apta para 1-4 años es un plan aparte), talleres, naturaleza y granjas, parques, museos y exposiciones con programación familiar, magia, actividades gratuitas en la calle, eventos de temporada.

YA CONOZCO ESTOS LUGARES FIJOS, NO LOS REPITAS: {CONOCIDOS}.
Solo incluye un lugar permanente si es NUEVO y claramente apto para niños pequeños.

REGLAS:
- Descarta resultados de otras ciudades o países, páginas antiguas y eventos ya pasados o fuera del rango.
- No inventes nada. Si la fuente no confirma el precio escribe "Consultar"; si no confirma la edad usa edadMin 0 y edadMax 99 y dilo en la descripción. Un evento sin fecha confirmada NO se incluye.
- urlEntradas y urlInfo: SOLO enlaces reales que hayas visto en tus búsquedas. Si no hay, cadena vacía. Nunca construyas un enlace a mano.
- urlEntradas: enlace directo para comprar o reservar (taquilla oficial, Eventbrite, El Corte Inglés, Atrápalo...). Vacío si es gratis o no lo encuentras.
- urlInfo: la página donde confirmaste el evento (web oficial o agenda).
- Entre 15 y 25 planes, con variedad de tipos, municipios y precios.

FORMATO: al terminar responde SOLO con un array JSON, sin texto antes ni después ni bloques de código. Cada plan:
{"nombre": "...", "fechaISO": "YYYY-MM-DD", "fechaFinISO": "YYYY-MM-DD (igual que fechaISO si es un solo día)", "hora": "HH:MM o franja", "tipo": "uno de: {TIPOS}", "lugar": "...", "municipio": "...", "precio": "Gratis | 8€ | Desde 5€ | Consultar", "urlEntradas": "", "urlInfo": "", "edadMin": 0, "edadMax": 99, "esPermanente": false, "descripcion": "1-2 frases útiles"}"""


def sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn")


def clave(nombre):
    return re.sub(r"[^a-z0-9]+", " ", sin_acentos(nombre).lower()).strip()


def texto(v, n=220):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def a_iso(v):
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        return ""


def entero(v, defecto, lo=0, hi=99):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return defecto


def url_ok(v):
    u = str(v or "").strip()
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else ""


def verificar_url(u):
    """False solo si el enlace claramente no existe (404/410, dominio inexistente, error de conexión)."""
    cab = {"User-Agent": "Mozilla/5.0 (compatible; planes-murcia-bot)"}
    for metodo in ("HEAD", "GET"):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, method=metodo, headers=cab), timeout=10):
                return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return False
            if metodo == "GET" or e.code != 405:
                return True   # 403, 429, 5xx...: hay servidor, puede ser un bloqueo a robots
        except Exception:
            return False
    return True


def extraer_objetos(txt):
    """Saca todos los objetos JSON con campo 'nombre', aunque haya texto alrededor o la respuesta esté cortada."""
    dec, out, i = json.JSONDecoder(), [], 0
    while True:
        i = txt.find("{", i)
        if i == -1:
            return out
        try:
            obj, fin = dec.raw_decode(txt, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and "nombre" in obj:
            out.append(obj)
            i = fin
        else:
            i += 1


def hay_finde(fi, ff):
    d, fin = date.fromisoformat(fi), date.fromisoformat(ff)
    for _ in range(14):
        if d > fin:
            break
        if d.weekday() >= 5:
            return True
        d += timedelta(days=1)
    return False


def fmt(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def normalizar(c, desde, hasta):
    nombre = texto(c.get("nombre"), 140)
    if not nombre:
        return None
    perm = bool(c.get("esPermanente"))
    p = {"nombre": nombre, "hora": texto(c.get("hora"), 40) or "Consultar",
         "tipo": c.get("tipo") if c.get("tipo") in TIPOS else "Otro",
         "lugar": texto(c.get("lugar"), 120), "municipio": texto(c.get("municipio"), 60),
         "precio": texto(c.get("precio"), 40) or "Consultar",
         "urlEntradas": url_ok(c.get("urlEntradas")), "urlInfo": url_ok(c.get("urlInfo")),
         "edadMin": entero(c.get("edadMin"), 0, 0, 18), "edadMax": entero(c.get("edadMax"), 99, 0, 99),
         "esPermanente": perm, "descripcion": texto(c.get("descripcion")), "estado": "Pendiente"}
    if p["precio"].lower().startswith("grat"):
        p["precio"], p["urlEntradas"] = "Gratis", ""
    if perm:
        p.update(fecha="Cualquier finde", esFinde=True)
        return p
    fi = a_iso(c.get("fechaISO"))
    if not fi:
        return None   # sin fecha fiable no se publica
    ff = a_iso(c.get("fechaFinISO")) or fi
    ff = max(fi, ff)
    if ff < desde.isoformat() or fi > hasta.isoformat():
        return None
    p.update(fechaISO=fi, fechaFinISO=ff, fecha=fmt(fi) if fi == ff else f"{fmt(fi)} al {fmt(ff)}",
             esFinde=hay_finde(fi, ff))
    return p


def llamar_api(prompt):
    import anthropic
    cliente = anthropic.Anthropic()   # lee ANTHROPIC_API_KEY
    mensajes = [{"role": "user", "content": prompt}]
    partes = []
    for _ in range(4):               # la búsqueda puede pausarse y continuar
        with cliente.messages.stream(
            model=MODELO, max_tokens=16000, messages=mensajes,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_BUSQUEDAS}],
        ) as flujo:
            resp = flujo.get_final_message()
        partes += [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        if resp.stop_reason != "pause_turn":
            break
        mensajes = mensajes + [{"role": "assistant", "content": resp.content}]
    return "\n".join(partes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--respuesta", help="archivo con una respuesta de prueba (no llama a la API)")
    ap.add_argument("--hoy", help="fecha de hoy AAAA-MM-DD (solo para pruebas)")
    ap.add_argument("--sin-verificar", action="store_true", help="no comprobar los enlaces")
    a = ap.parse_args()

    hoy = date.fromisoformat(a.hoy) if a.hoy else datetime.now(ZoneInfo("Europe/Madrid")).date()
    hasta = hoy + timedelta(days=DIAS)
    fijos = json.loads((RAIZ / "lugares_fijos.json").read_text(encoding="utf-8"))
    ruta_estados = RAIZ / "estados.json"
    estados = {clave(k): v for k, v in json.loads(ruta_estados.read_text(encoding="utf-8")).items()
               if not k.startswith("_")} if ruta_estados.exists() else {}

    if a.respuesta:
        bruto = Path(a.respuesta).read_text(encoding="utf-8")
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Falta ANTHROPIC_API_KEY (Settings > Secrets and variables > Actions).")
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                 "octubre", "noviembre", "diciembre"]
        prompt = (PROMPT.replace("{HOY_TXT}", f"{hoy.day} de {meses[hoy.month - 1]} de {hoy.year}")
                  .replace("{DESDE}", fmt(hoy.isoformat())).replace("{HASTA}", fmt(hasta.isoformat()))
                  .replace("{MES}", f"{meses[hoy.month - 1]} {hoy.year}")
                  .replace("{CONOCIDOS}", "; ".join(p["nombre"] for p in fijos))
                  .replace("{TIPOS}", " | ".join(TIPOS)))
        bruto = llamar_api(prompt)

    vistos = {clave(p["nombre"]) for p in fijos}
    eventos, descartados = [], 0
    for c in extraer_objetos(bruto):
        p = normalizar(c, hoy, hasta)
        k = (clave(p["nombre"]), p.get("fechaISO", "")) if p else None
        if not p or k in vistos or (p["esPermanente"] and clave(p["nombre"]) in vistos):
            descartados += 1
            continue
        vistos.add(k)
        if not a.sin_verificar:
            for campo in ("urlEntradas", "urlInfo"):
                if p[campo] and not verificar_url(p[campo]):
                    print(f"  enlace roto descartado ({campo}): {p[campo]}")
                    p[campo] = ""
        eventos.append(p)

    print(f"{len(eventos)} planes válidos, {descartados} descartados (sin fecha, fuera de rango o repetidos).")
    if len(eventos) < MIN_EVENTOS:
        sys.exit(f"Solo {len(eventos)} planes válidos (mínimo {MIN_EVENTOS}). No se guarda nada: se conservan los datos anteriores.")

    eventos.sort(key=lambda p: (p.get("fechaISO") or "9999", p["nombre"]))
    todos = fijos + eventos
    for p in todos:
        p["estado"] = estados.get(clave(p["nombre"]), p.get("estado", "Pendiente"))
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps({"actualizado": hoy.isoformat(), "rango": {"desde": hoy.isoformat(), "hasta": hasta.isoformat()},
                                  "planes": todos}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Guardado {SALIDA.relative_to(RAIZ)} con {len(todos)} planes.")


if __name__ == "__main__":
    main()
