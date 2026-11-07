#!/usr/bin/env python3
"""Busca planes con niños (1 a 4 años) en la Región de Murcia y actualiza docs/planes.json.

Cada ejecución:
  1. Busca eventos nuevos en internet.
  2. Los ACUMULA con los de búsquedas anteriores (un plan se queda hasta que pasa su fecha).
  3. Abre la ficha de los eventos que no tienen edad, precio o entradas confirmados y los completa.
  4. Añade los eventos de eventos_manuales.json (los que apuntas tú a mano).

Uso normal (lo hace GitHub):   python buscar_planes.py
Pruebas sin gastar API:        python buscar_planes.py --respuesta prueba.txt --respuesta-ficha ficha.txt --hoy 2026-11-07 --sin-verificar
Solo fusionar, sin API:        python buscar_planes.py --solo-fusionar
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
MAX_BUSQUEDAS = int(os.environ.get("MAX_BUSQUEDAS", "12"))              # búsquedas web de la pasada principal
MAX_BUSQUEDAS_FICHA = int(os.environ.get("MAX_BUSQUEDAS_FICHA", "8"))   # búsquedas web por cada lote de fichas
MAX_ENRIQUECER = int(os.environ.get("MAX_ENRIQUECER", "18"))            # eventos que se completan por ejecución
LOTE = 6                                                                # eventos por llamada de fichas
DIAS = int(os.environ.get("DIAS", "14"))                                # cuántos días hacia delante se buscan
MIN_EVENTOS = 3                                                         # menos que esto = fallo y no se guarda nada

TIPOS = ["Música/Concierto", "Teatro/Musical", "Mercadillo/Mercado", "Naturaleza/Granja", "Parque/Infantil",
         "Festival", "Taller", "Zoo/Animales", "Cultura/Museo", "Magia/Circo", "Deporte", "Gastronomía",
         "Exposición", "Otro"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

PROMPT = """Hoy es {HOY_TXT}. Busca en internet planes y actividades para niños de 1 a 4 años en la Región de Murcia (España), entre el {DESDE} y el {HASTA}. Da prioridad a los fines de semana.

FUENTES: agendas municipales (Murcia, Cartagena, Molina de Segura, Lorca, Alcantarilla y otros municipios), La Verdad, 7 Región, Murcia Turismo, webs de teatros y auditorios (Teatro Romea, Teatro Circo Murcia, Teatro Circo Apolo, Auditorio El Batel), Eventbrite, El Corte Inglés Entradas, Atrápalo, webs oficiales de festivales, y webs de parques, granjas y mercadillos. Haz búsquedas concretas (por ejemplo "teatro infantil Murcia {MES}", "mercadillo Murcia fin de semana", "festival infantil Murcia {MES}", "talleres infantiles gratis Murcia") y abre la web oficial del evento cuando puedas.

TIPOS: mercadillos y mercados, conciertos familiares, teatro infantil, títeres y circo, festivales (si tienen varias funciones, cada función apta para 1-4 años es un plan aparte), talleres, naturaleza y granjas, parques, museos y exposiciones con programación familiar, magia, actividades gratuitas en la calle, eventos de temporada.

YA CONOZCO ESTOS LUGARES FIJOS, NO LOS REPITAS: {CONOCIDOS}.
Solo incluye un lugar permanente si es NUEVO y claramente apto para niños pequeños.

REGLAS:
- Descarta resultados de otras ciudades o países, páginas antiguas y eventos ya pasados o fuera del rango.
- No inventes nada. Si la fuente no confirma el precio escribe "Consultar"; si no confirma la edad usa edadMin 0, edadMax 99 y edadConfirmada false. Un evento sin fecha confirmada NO se incluye.
- edadConfirmada es true SOLO si una página escribe la edad recomendada. No la deduzcas por el tipo de espectáculo.
- urlEntradas y urlInfo: SOLO enlaces reales que hayas visto en tus búsquedas. Si no hay, cadena vacía. Nunca construyas un enlace a mano.
- urlEntradas: enlace directo para comprar o reservar (taquilla oficial, Eventbrite, El Corte Inglés, Atrápalo...). Vacío si es gratis o no lo encuentras.
- urlInfo: la página donde confirmaste el evento (web oficial o agenda).
- Entre 15 y 25 planes, con variedad de tipos, municipios y precios.

FORMATO: al terminar responde SOLO con un array JSON, sin texto antes ni después ni bloques de código. Cada plan:
{"nombre": "...", "fechaISO": "YYYY-MM-DD", "fechaFinISO": "YYYY-MM-DD (igual que fechaISO si es un solo día)", "hora": "HH:MM o franja", "tipo": "uno de: {TIPOS}", "lugar": "...", "municipio": "...", "precio": "Gratis | 8€ | Desde 5€ | Consultar", "urlEntradas": "", "urlInfo": "", "edadMin": 0, "edadMax": 99, "edadConfirmada": false, "esPermanente": false, "descripcion": "1-2 frases útiles"}"""

PROMPT_FICHA = """Hoy es {HOY_TXT}. Tengo esta lista de planes para niños en la Región de Murcia (España). Para CADA uno, abre su web (urlInfo) o busca su ficha oficial y confirma estos datos SOLO si aparecen escritos en una página:
- edad recomendada (edadMin y edadMax; usa 99 si no hay máximo)
- precio de la entrada
- enlace directo para comprar o reservar entradas (urlEntradas)
- hora, si falta

REGLAS: no deduzcas la edad por el tipo de espectáculo; si ninguna página la dice, edadConfirmada es false. No inventes enlaces: solo URLs que hayas visto. Si no encuentras un dato, usa null.

LISTA:
{LISTA}

FORMATO: responde SOLO con un array JSON, un objeto por plan, sin texto antes ni después ni bloques de código:
{"id": 0, "edadConfirmada": true, "edadMin": 0, "edadMax": 99, "precio": "Gratis | 8€ | Desde 5€ | null", "urlEntradas": "url o null", "hora": "HH:MM o null"}"""


# ---------- utilidades ----------
def sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn")


def clave(nombre):
    return re.sub(r"[^a-z0-9]+", " ", sin_acentos(nombre).lower()).strip()


def texto(v, n=220):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def muni(v):
    t = texto(v, 60)
    return "Murcia capital" if clave(t) in ("murcia", "murcia capital", "murcia ciudad") else t


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


def cargar_json(ruta, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


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


def extraer_objetos(txt, campo="nombre"):
    """Saca los objetos JSON que tengan ese campo, aunque haya texto alrededor o la respuesta esté cortada."""
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
        if isinstance(obj, dict) and campo in obj:
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


def clave_evento(p):
    return (clave(p["nombre"]), p.get("fechaISO", ""))


# ---------- normalización ----------
def normalizar(c, desde, hasta):
    nombre = texto(c.get("nombre"), 140)
    if not nombre:
        return None
    perm = bool(c.get("esPermanente"))
    mn = entero(c.get("edadMin"), 0, 0, 18)
    mx = max(mn, entero(c.get("edadMax"), 99, 0, 99))
    p = {"nombre": nombre, "hora": texto(c.get("hora"), 40) or "Consultar",
         "tipo": c.get("tipo") if c.get("tipo") in TIPOS else "Otro",
         "lugar": texto(c.get("lugar"), 120), "municipio": muni(c.get("municipio")),
         "precio": texto(c.get("precio"), 40) or "Consultar",
         "urlEntradas": url_ok(c.get("urlEntradas")), "urlInfo": url_ok(c.get("urlInfo")),
         "edadMin": mn, "edadMax": mx, "edadConfirmada": c.get("edadConfirmada") is True,
         "esPermanente": perm, "descripcion": texto(c.get("descripcion")), "estado": "Pendiente"}
    if texto(c.get("edadTexto"), 40):
        p["edadTexto"] = texto(c.get("edadTexto"), 40)
    if p["precio"].lower().startswith("grat"):
        p["precio"], p["urlEntradas"] = "Gratis", ""
    if perm:
        p.update(fecha="Cualquier finde", esFinde=True)
        return p
    fi = a_iso(c.get("fechaISO"))
    if not fi:
        return None   # sin fecha fiable no se publica
    ff = max(fi, a_iso(c.get("fechaFinISO")) or fi)
    if ff < desde.isoformat() or fi > hasta.isoformat():
        return None
    p.update(fechaISO=fi, fechaFinISO=ff, fecha=fmt(fi) if fi == ff else f"{fmt(fi)} al {fmt(ff)}",
             esFinde=hay_finde(fi, ff))
    return p


def verificar_enlaces(p, sin_verificar):
    if sin_verificar:
        return
    for campo in ("urlEntradas", "urlInfo"):
        if p.get(campo) and not verificar_url(p[campo]):
            print(f"  enlace roto descartado ({campo}): {p[campo]}")
            p[campo] = ""


# ---------- API ----------
def llamar_api(prompt, max_busquedas):
    import anthropic
    cliente = anthropic.Anthropic()   # lee ANTHROPIC_API_KEY
    mensajes = [{"role": "user", "content": prompt}]
    partes = []
    for _ in range(4):               # la búsqueda puede pausarse y continuar
        with cliente.messages.stream(
            model=MODELO, max_tokens=16000, messages=mensajes,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_busquedas}],
        ) as flujo:
            resp = flujo.get_final_message()
        partes += [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        if resp.stop_reason != "pause_turn":
            break
        mensajes = mensajes + [{"role": "assistant", "content": resp.content}]
    return "\n".join(partes)


def aplicar_ficha(p, o, sin_verificar):
    """Completa un evento con lo que ha confirmado la segunda pasada. Solo rellena huecos y confirmaciones."""
    if o.get("edadConfirmada") is True:
        mn = entero(o.get("edadMin"), None, 0, 18)
        if mn is not None:
            p["edadMin"], p["edadMax"], p["edadConfirmada"] = mn, max(mn, entero(o.get("edadMax"), 99, 0, 99)), True
    pr = texto(o.get("precio"), 40)
    if pr and pr.lower() not in ("null", "consultar") and p["precio"] == "Consultar":
        p["precio"] = "Gratis" if pr.lower().startswith("grat") else pr
        if p["precio"] == "Gratis":
            p["urlEntradas"] = ""
    u = url_ok(o.get("urlEntradas"))
    if u and p["precio"] != "Gratis" and not p.get("urlEntradas") and (sin_verificar or verificar_url(u)):
        p["urlEntradas"] = u
    h = texto(o.get("hora"), 40)
    if h and h.lower() != "null" and p["hora"] in ("Consultar", ""):
        p["hora"] = h


def enriquecer(eventos, hoy, hoy_txt, sin_verificar, ficha_simulada=None):
    pend = sorted([p for p in eventos if not p.get("enriquecido")], key=lambda p: p.get("fechaISO", ""))[:MAX_ENRIQUECER]
    print(f"Completando fichas: {len(pend)} eventos pendientes.")
    for i in range(0, len(pend), LOTE):
        lote = pend[i:i + LOTE]
        lista = "\n".join(json.dumps({"id": k, "nombre": p["nombre"], "fecha": p["fecha"], "lugar": p["lugar"],
                                      "municipio": p["municipio"], "urlInfo": p.get("urlInfo", "")}, ensure_ascii=False)
                          for k, p in enumerate(lote))
        try:
            bruto = ficha_simulada if ficha_simulada is not None else llamar_api(
                PROMPT_FICHA.replace("{HOY_TXT}", hoy_txt).replace("{LISTA}", lista), MAX_BUSQUEDAS_FICHA)
        except Exception as e:   # un fallo aquí no tumba la ejecución: se reintenta la próxima vez
            print(f"  aviso: no se pudo completar un lote ({type(e).__name__}: {e})")
            continue
        for o in extraer_objetos(bruto, "id"):
            k = o.get("id")
            if isinstance(k, int) and 0 <= k < len(lote):
                aplicar_ficha(lote[k], o, sin_verificar)
                lote[k]["enriquecido"] = hoy.isoformat()


# ---------- principal ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--respuesta", help="archivo con una respuesta de prueba de la búsqueda (no llama a la API)")
    ap.add_argument("--respuesta-ficha", help="archivo con una respuesta de prueba de las fichas")
    ap.add_argument("--hoy", help="fecha de hoy AAAA-MM-DD (solo para pruebas)")
    ap.add_argument("--sin-verificar", action="store_true", help="no comprobar los enlaces")
    ap.add_argument("--sin-enriquecer", action="store_true", help="no completar fichas")
    ap.add_argument("--solo-fusionar", action="store_true", help="no buscar: solo fusionar lo anterior con los manuales")
    a = ap.parse_args()

    hoy = date.fromisoformat(a.hoy) if a.hoy else datetime.now(ZoneInfo("Europe/Madrid")).date()
    hasta = hoy + timedelta(days=DIAS)
    hoy_txt = f"{hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}"

    fijos = cargar_json(RAIZ / "lugares_fijos.json", [])
    estados = {clave(k): v for k, v in cargar_json(RAIZ / "estados.json", {}).items() if not k.startswith("_")}
    previo = cargar_json(SALIDA, {}).get("planes", [])
    for p in fijos:
        p.setdefault("edadConfirmada", True)   # son lugares elegidos por ti para peques
    for p in previo:
        p["municipio"] = muni(p.get("municipio"))
        p.setdefault("edadConfirmada", False)  # datos de búsquedas anteriores a este campo
    claves_fijos = {clave(p["nombre"]) for p in fijos}
    prev_perm = [p for p in previo if p.get("esPermanente") and clave(p["nombre"]) not in claves_fijos]
    prev_ev = [p for p in previo if not p.get("esPermanente")
               and (p.get("fechaFinISO") or p.get("fechaISO", "")) >= hoy.isoformat()]

    manuales = []
    for c in cargar_json(RAIZ / "eventos_manuales.json", []):
        if isinstance(c, dict) and "nombre" in c:
            m = normalizar(c, hoy, hoy + timedelta(days=400))
            if m and not m["esPermanente"]:
                m["manual"], m["enriquecido"] = True, hoy.isoformat()
                manuales.append(m)

    nuevos, perm_nuevos, descartados = [], [], 0
    if not a.solo_fusionar:
        if a.respuesta:
            bruto = Path(a.respuesta).read_text(encoding="utf-8")
        else:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                sys.exit("Falta ANTHROPIC_API_KEY (Settings > Secrets and variables > Actions).")
            conocidos = "; ".join([p["nombre"] for p in fijos] + [p["nombre"] for p in prev_perm])
            prompt = (PROMPT.replace("{HOY_TXT}", hoy_txt).replace("{DESDE}", fmt(hoy.isoformat()))
                      .replace("{HASTA}", fmt(hasta.isoformat())).replace("{MES}", f"{MESES[hoy.month - 1]} {hoy.year}")
                      .replace("{CONOCIDOS}", conocidos).replace("{TIPOS}", " | ".join(TIPOS)))
            bruto = llamar_api(prompt, MAX_BUSQUEDAS)
        vistos_perm = claves_fijos | {clave(p["nombre"]) for p in prev_perm}
        vistos_ev = set()
        for c in extraer_objetos(bruto, "nombre"):
            p = normalizar(c, hoy, hasta)
            if not p:
                descartados += 1
                continue
            if p["esPermanente"]:
                if clave(p["nombre"]) in vistos_perm:
                    descartados += 1
                    continue
                vistos_perm.add(clave(p["nombre"]))
                verificar_enlaces(p, a.sin_verificar)
                perm_nuevos.append(p)
                continue
            if clave_evento(p) in vistos_ev:
                descartados += 1
                continue
            vistos_ev.add(clave_evento(p))
            verificar_enlaces(p, a.sin_verificar)
            nuevos.append(p)
        print(f"Búsqueda: {len(nuevos)} eventos válidos, {descartados} descartados (sin fecha, fuera de rango o repetidos).")
        if len(nuevos) < MIN_EVENTOS:
            sys.exit(f"Solo {len(nuevos)} eventos válidos (mínimo {MIN_EVENTOS}). No se guarda nada: se conservan los datos anteriores.")

    # Acumular: anteriores + nuevos (un evento ya completado no se pisa) + manuales (mandan siempre)
    eventos = {clave_evento(p): p for p in prev_ev}
    for p in nuevos:
        k = clave_evento(p)
        if k not in eventos or not eventos[k].get("enriquecido"):
            eventos[k] = p
    for m in manuales:
        eventos[clave_evento(m)] = m
    lista = list(eventos.values())

    if not (a.sin_enriquecer or a.solo_fusionar or (a.respuesta and not a.respuesta_ficha)):
        ficha = Path(a.respuesta_ficha).read_text(encoding="utf-8") if a.respuesta_ficha else None
        if ficha is not None or os.environ.get("ANTHROPIC_API_KEY"):
            enriquecer(lista, hoy, hoy_txt, a.sin_verificar, ficha)

    lista.sort(key=lambda p: (p.get("fechaISO") or "9999", p["nombre"]))
    todos = fijos + prev_perm + perm_nuevos + lista
    for p in todos:
        p["estado"] = estados.get(clave(p["nombre"]), p.get("estado", "Pendiente"))
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps({"actualizado": hoy.isoformat(),
                                  "rango": {"desde": hoy.isoformat(), "hasta": hasta.isoformat()},
                                  "planes": todos}, ensure_ascii=False, indent=1), encoding="utf-8")
    conf = sum(1 for p in lista if p.get("edadConfirmada"))
    print(f"Guardado {SALIDA.relative_to(RAIZ)}: {len(todos)} planes ({len(lista)} eventos, de ellos {conf} con edad confirmada; "
          f"{len(fijos) + len(prev_perm) + len(perm_nuevos)} lugares fijos).")


if __name__ == "__main__":
    main()
