# CLAUDE.md

Notas sobre cosas de este repo que no son obvias mirando el código o el `package.json`.

## Deploy es 100% manual, sin build automático

`npm run deploy` es literalmente `gh-pages -d dist`. No hay `predeploy`, no hay CI/GitHub
Actions. Si corres `deploy` sin haber corrido `npm run build` antes, publicas el contenido
viejo que ya esté en `dist/` (o fallas si no existe). El flujo correcto siempre es:

```
npm run build && npm run deploy
```

`dist/` está en `.gitignore` — no busques el output de build en `main`, vive únicamente en
el historial del branch `gh-pages` (que `gh-pages` gestiona y sobreescribe por su cuenta).

## `base: '/dashboard/'` en vite.config.js está hardcodeado al nombre del repo

Es necesario porque GitHub Pages sirve el sitio en `swetro.github.io/dashboard/`, no en la
raíz. Si el repo cambiara de nombre, o corres `vite preview` esperando que las rutas de
assets se comporten como en local sin ese prefijo, vas a ver 404 de assets.

## El dashboard elige el usuario por query param `?u=`, no por routing

`getUserId()` en `src/App.jsx` lee `?u=<valor>` de la URL (default `"demo"`) y hace fetch de
`data/<valor>.json`. El frontend no distingue token de ID secuencial — es un fetch directo
por nombre de archivo, no hay lista de usuarios ni build-time generation ni lookup alguno.
No hay validación si el archivo no existe más que un mensaje de error genérico
("Usuario no encontrado").

## Los datos de cada usuario NO se generan como parte de `npm run build`

`transformar_json.py` es un pipeline separado, corrido a mano, que no toca Vite ni Node.
Convierte un JSON exportado manualmente desde la base de datos (formato de la app del
socio) al formato que espera el dashboard, y escribe el resultado en
`public/data/<token>.json` (no `<userId>.json` — ver "IDs secuenciales → tokens" más abajo).
Si cambias el código del dashboard pero no vuelves a correr este script, los datos de
producción quedan desactualizados sin que nada te avise.

- El userId sale de `input_data`, no lo eliges vos; el nombre de archivo de salida es el
  *token* asociado a ese userId (`obtener_token()`), no el `--output` que le pases salvo que
  lo pases explícitamente.
- `--meta` acepta tanto un archivo de un solo usuario (`meta_alejandro.json`, con
  `metaCarrera` al nivel raíz) como un archivo multi-usuario (`metas_usuarios.json`, keyed
  por id) — son dos formatos distintos, no intercambiables entre sí sin adaptar el JSON.

## IDs secuenciales → tokens (`private/tokens.csv`)

`obtener_token(user_id)` en `transformar_json.py` reemplaza el `userId` secuencial por un
token opaco (`secrets.token_urlsafe(8)`) como nombre de archivo público
(`public/data/<token>.json`) y como valor de `?u=`. El mapeo `userId -> token` vive en
`private/tokens.csv` (gitignored, nunca se versiona) — es la única forma de saber, puertas
adentro, a quién pertenece cada archivo. Es idempotente: correr el pipeline de nuevo para el
mismo usuario reutiliza su token existente en vez de invalidar el link que ya le mandaste.

- **No todos los usuarios están tokenizados todavía.** Solo los 6 pilotos comunicados a
  agosto 2026 (`2`, `5225`, `12833`, `32005`, `41572`, `46234` → sus tokens en
  `private/tokens.csv`) tienen archivo por token. El resto de `public/data/*.json` que ya
  existía (`9`, `24`, `8648`, `9860`, `30065`, `30560`, `33866`, `43718`) se quedó con su
  nombre de archivo secuencial original — siguen siendo IDs adivinables hasta que alguien
  decida tokenizarlos también (o retirarlos si son datos de prueba).
- Los links viejos con el ID secuencial (`?u=2`) dejan de funcionar en cuanto se renombra el
  archivo — no hay redirect ni alias.

## `--con-pulse` requiere `ANTHROPIC_API_KEY` y falla en silencio

Si corres `transformar_json.py --con-pulse` sin instalar `pip install anthropic` o sin la
env var seteada, el script **no truena**: imprime un warning y sigue, generando el JSON sin
el bloque "Pulse". Fácil no notar que faltó. Por ahora la key se exporta a mano en la shell
cada vez que se necesita (no hay `.env` ni gestor de secretos en este repo).

## `taper` siempre sale vacío desde el generador

`transformar_json.py` siempre escribe `"taper": []` — no hay lógica que lo pueble. La UI
(`src/App.jsx`, sección "Plan taper") sí sabe renderizarlo si tuviera contenido, pero con
los datos que produce el pipeline actual esa sección nunca aparece. No asumas que es un bug
de la UI si no ves esa sección.

## `meta.metaCarrera.fecha === "2027-01-01"` es un sentinel, no una fecha real

`src/App.jsx` usa esa fecha exacta como señal de "el usuario no configuró meta de carrera"
(`hasMeta = meta.metaCarrera.fecha !== "2027-01-01"`). Si algún día una meta real cae en esa
fecha, la UI la tratará como "sin meta".

## `public/data/*.json` y los `meta_*.json` en la raíz están commiteados con datos reales

No son fixtures ni demos (salvo `demo.json`) — son datos reales de usuarios (entrenamiento,
nombres) versionados en git y servidos tal cual por GitHub Pages sin autenticación. Es un
riesgo aceptado a propósito en esta etapa de MVP: el sitio es público pero no está indexado
ni difundido, y solo lo conocen los usuarios a quienes se les envía su enlace directo.
Los 6 pilotos tokenizados (ver "IDs secuenciales → tokens") ya no son adivinables por
nombre de archivo, pero el resto de `public/data/*.json` (`9.json`, `24.json`, `8648.json`...)
sigue con nombre de ID secuencial — revisa ese punto antes de escalar a más usuarios o de
asumir que algo ahí es privado por default.
