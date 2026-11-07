# Planes Murcia · búsqueda semanal automática

Cada lunes busca planes para niños de 1 a 4 años en la Región de Murcia, los guarda y tu web se actualiza sola.

## Puesta en marcha (una sola vez, unos 15 minutos)

1. **Clave de API.** Entra en platform.claude.com, crea una clave de API y copia el texto. Añade saldo (con 5 $ tienes para meses) y, en los ajustes de la cuenta, pon un límite de gasto mensual bajo (por ejemplo 10 $).
2. **Repositorio.** En github.com crea un repositorio **público** llamado `planes-murcia` y sube todo el contenido de esta carpeta. Ojo: en Mac la carpeta `.github` está oculta; en Finder pulsa Cmd + Mayús + punto para verla.
3. **Guardar la clave.** En el repositorio: Settings → Secrets and variables → Actions → New repository secret. Nombre: `ANTHROPIC_API_KEY`. Valor: tu clave.
4. **Activar la web.** Settings → Pages → Source: Deploy from a branch → Branch: `main` y carpeta `/docs` → Save.
5. **Primera búsqueda.** Pestaña Actions → "Buscar planes" → Run workflow. Si GitHub pide activar las Actions, acepta. Tarda unos minutos.
6. **Tu web:** `https://TU-USUARIO.github.io/planes-murcia/` (la primera vez tarda uno o dos minutos en aparecer).

## Uso diario

- **Buscar a mano:** Actions → Buscar planes → Run workflow (también desde la app de GitHub en el móvil).
- **Marcar Hecho o Comprado:** edita `estados.json` en GitHub con el nombre exacto del plan.
- **Añadir lugares fijos:** edita `lugares_fijos.json`. Ya lleva los 21 de tu lista.
- **Excel:** botón de descarga arriba a la derecha de la web.

## Coste y seguridad

- Una búsqueda cuesta aproximadamente entre 0,5 y 1 $ (estimación). `MAX_BUSQUEDAS` (12 por defecto, en `buscar_planes.py`) limita las búsquedas web.
- La clave vive solo en los Secrets de GitHub, nunca en los archivos.
- Si una búsqueda falla o trae menos de 3 planes válidos, **no se borra nada**: se conservan los datos anteriores y GitHub te avisa por email.
- Si algún día dejan de ejecutarse los lunes, entra en Actions y pulsa "Enable workflow".

## Cómo comprueba los datos

- Descarta eventos sin fecha confirmada, pasados o fuera de rango.
- Borra los enlaces de entradas que no existen (404 o dominio inexistente).
- Si un enlace de entradas no aparece, la web ofrece "Buscar entradas" en Google.
- Aun así, confirma fecha y precio en la web oficial antes de comprar.
