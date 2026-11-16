# 🎬 ShortsFactory

**Crea y sube automáticamente YouTube Shorts e historias por partes, en español e inglés.**
Trocea un vídeo largo en partes de 3 minutos o en Shorts de 15–60 s, lo pasa a vertical 9:16 con
subtítulos, música y textos, y lo sube a YouTube Studio según una cola programada, siempre con
revisión humana antes de publicar. 100 % local y gratuito: sin APIs de IA ni servicios de pago.

## ⚡ Instalación en Windows (1 clic)

1. **Descarga** este proyecto: botón verde **Code › Download ZIP** y descomprímelo.
   (También puedes descargar solo [`INSTALAR.bat`](INSTALAR.bat): abre el archivo, pulsa
   «Download raw file» y él descarga todo lo demás.)
2. **Doble clic en `INSTALAR.bat`.** Si Windows muestra «Windows protegió su PC», pulsa
   *Más información › Ejecutar de todas formas*.
3. Espera a que termine (unos minutos). Se crea el icono **ShortsFactory** en el Escritorio y en el
   menú Inicio.

El instalador lo hace todo solo, sin permisos de administrador:

| Paso | Qué instala |
|---|---|
| 1 | Python 3.12 (solo si no lo tienes) |
| 2 | La aplicación, en `%LOCALAPPDATA%\ShortsFactory\app` |
| 3 | FFmpeg completo (con NVENC/QuickSync/AMF, libass y vidstab) |
| 4 | Las dependencias de Python en un entorno propio |
| 5 | El navegador Chromium para la subida |
| 6 | Los accesos directos del Escritorio y del menú Inicio |

- **Actualizar**: vuelve a ejecutar `INSTALAR.bat`. Tus datos, tu cola y tus sesiones se conservan.
- **Desinstalar**: `%LOCALAPPDATA%\ShortsFactory\Desinstalar ShortsFactory.bat` (te pregunta si
  quieres borrar también tus datos).
- Si algo falla, el registro está en `%LOCALAPPDATA%\ShortsFactory\instalacion.log`.

Después de instalar: abre la app, sigue el asistente y, en **Ajustes › Canales**, pulsa
**«Iniciar sesión en Studio»** en cada canal para iniciar sesión a mano (una sola vez).

---

## Principios y restricciones

- **Sin APIs de IA ni servicios de pago.** Sin YouTube Data API.
- **Todo el procesamiento es local** con FFmpeg/ffprobe y OpenCV (solo el Haar Cascade incluido).
- **La única conexión a internet** es la del navegador Chromium que sube los vídeos.
- **Sin contraseñas**: inicias sesión a mano una vez por canal y la sesión queda en un perfil
  persistente de Playwright dentro de la carpeta de datos de la app.
- **Sin telemetría.** No hay funciones para evadir la detección de la plataforma ni para generar
  visualizaciones o interacciones falsas.

> ⚠ **La subida automática depende de la interfaz web de YouTube Studio y puede romperse cuando YouTube
> la cambie.** Si una subida falla se guarda una captura y el HTML en la carpeta `diagnostico/`
> para poder actualizar los selectores (`shortsfactory/upload/studio.py`).

---

## Instalación manual (Linux o desarrolladores)

En Windows usa `INSTALAR.bat` (arriba). Para instalar a mano hacen falta **Python 3.11+** y **FFmpeg** (binario libre).

### 1. Instalar FFmpeg

- **Windows**: descarga una build "release full" de <https://www.gyan.dev/ffmpeg/builds/> o
  <https://github.com/BtbN/FFmpeg-Builds/releases>, descomprímela (p. ej. en `C:\ffmpeg`) y añade
  `C:\ffmpeg\bin` al `PATH`, o indica la ruta de `ffmpeg.exe` y `ffprobe.exe` en **Ajustes › FFmpeg**.
  Las builds "full" incluyen libass, drawtext, vidstab y los codificadores NVENC/QuickSync/AMF.
- **Linux (Debian/Ubuntu)**: `sudo apt install ffmpeg`.

En **Ajustes › FFmpeg › Comprobar FFmpeg** la app muestra la versión, los codificadores por hardware
disponibles y si falta algún filtro (`ass`, `drawtext`, `vidstabdetect`, `sidechaincompress`…).

### 2. Instalar la app

**Linux**

```bash
./scripts/install_linux.sh
```

```bash
./scripts/run_linux.sh
```

Los scripts crean un entorno virtual `.venv`, instalan `requirements.txt` y descargan Chromium para
Playwright (`python -m playwright install chromium`).

Instalación manual equivalente:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m playwright install chromium
```

```bash
.venv/bin/python -m shortsfactory
```

### Carpeta de datos

| Sistema | Ruta |
|---|---|
| Windows | `%APPDATA%\ShortsFactory` |
| Linux | `~/.local/share/shortsfactory` |
| Cualquiera | variable `SHORTSFACTORY_DATA` si está definida |

Contiene `shortsfactory.db` (SQLite: cola, estados, historial, ajustes), `profiles/` (un perfil de
navegador por canal), `exports/`, `backups/`, `diagnostico/`, `logs/` y `tmp/`.

---

## Primer inicio

Al abrir la app por primera vez aparece un **asistente**:

1. **FFmpeg**: detecta o pide la ruta y la comprueba.
2. **Canales**: crea el canal en español y, opcionalmente, clona sus ajustes para el canal en inglés.
3. **Carpetas**: carpeta de exportación.

### Primer inicio de sesión por canal

1. Ve a **Ajustes › Canales**, selecciona el canal y pulsa **«Iniciar sesión en Studio (manual)»**.
2. Se abre Chromium con el perfil propio de ese canal. Inicia sesión en tu cuenta de Google y elige el
   canal de YouTube correcto. Escribes la contraseña tú, en la web de Google; la app no la ve ni la guarda.
3. Cierra la ventana. La sesión queda guardada en `profiles/<canal>`.
4. Repite para el otro canal (cada uno tiene su perfil aislado).
5. **«Comprobar sesión»** confirma que sigue activa.

Si durante la cola la sesión caduca, **el canal se pone en pausa** y recibes una notificación: vuelve a
iniciar sesión a mano y la cola continúa.

Configura también, por canal: idioma de Studio (`es`/`en`, debe coincidir con el idioma de tu cuenta
de Studio), franjas horarias preferidas (`18:00-23:00, 09:00-11:00`), separación mínima entre
publicaciones del canal, límite diario, días de descanso y lista de reproducción (nombre exacto de
una lista que ya exista).

---

## Flujo de trabajo

### Pestaña Vídeos

- Importa vídeos largos; se comprueba su integridad con ffprobe.
- **Modo de un clic**: trocea con los ajustes por defecto (cortes limpios en silencios), renderiza,
  ejecuta las comprobaciones técnicas y encola en el canal elegido. Si existe un `.srt` con el mismo
  nombre que el vídeo, se usa para los subtítulos.
- **Biblioteca de activos** (fondos, música, overlays, efectos, fuentes) con etiquetas.

### Pestaña Edición

- **Línea de tiempo con forma de onda**, segmentos, cambios de plano y marcas IN/OUT.
- Trocear en partes (180 s por defecto) o en cortes para Shorts (15–60 s). Con *cortes limpios*, cada
  corte se mueve al centro del silencio más cercano (`silencedetect`) dentro de la tolerancia.
- **Sugerir cortes**: candidatos entre cambios de plano (filtro `scene`) puntuados por volumen,
  movimiento y duración, ordenados en un ranking.
- Importar capítulos TXT (`00:00:00 Título`, ver `samples/capitulos_ejemplo.txt`) o marcas CSV
  (`inicio,fin,titulo`).
- Recorte fotograma a fotograma con atajos (por defecto `←/→` fotograma, `Shift+←/→` segundo,
  `I`/`O` marcas, `Ctrl+Enter` añadir segmento, `Espacio` reproducir). Se cambian en Ajustes.
- Duplicar un segmento con otra duración, **ajustar a una duración exacta** (p. ej. 59 s, tiene en
  cuenta pausas eliminadas y velocidad), **eliminar pausas largas**, quitar silencios de inicio/fin,
  velocidad 0,5x–2x y volumen por segmento (columnas editables de la tabla).
- Opciones de render:
  - **Formato**: plantillas 9:16, 1:1 y 4:5; encuadre *fondo difuminado*, *recorte centrado*,
    *seguimiento de cara* (Haar Cascade suavizado), *manual* (arrastra sobre la previsualización) o
    *letterbox* con color o imagen; marco PNG transparente; zoom Ken Burns; FPS y calidad (CRF).
  - **Color y efectos**: brillo, contraste, saturación, temperatura, estabilización (vidstab, dos
    pasadas), quitar barras negras (`cropdetect`), fundidos y **congelar el último fotograma** (cliffhanger).
  - **Audio**: `loudnorm` EBU R128 con objetivo configurable, música en bucle con volumen y **ducking**
    (`sidechaincompress`), efectos de sonido en marcas de tiempo (`ruta|segundo|volumen; …`), voz
    grabada mezclada o sustituyendo al original.
  - **Textos**: subtítulos SRT/VTT quemados con estilo (fuente, tamaño, color, contorno, posición,
    animación de fundido o deslizamiento), **karaoke palabra a palabra** a partir de un CSV
    `palabra,inicio,fin` (ver `samples/tiempos_palabras_ejemplo.csv`), numeración «Parte 1/5», barra de
    progreso de la historia y firma del canal (texto o logotipo, posición y duración).
- **Previsualizar fotograma** con todos los filtros antes de exportar.
- Exportar con barra de progreso y cancelación, en paralelo, con **caché por hash** (no se recodifica
  lo que no ha cambiado), **aceleración por hardware** (NVENC/QuickSync/AMF con respaldo en CPU) y
  comprobación previa de espacio libre.

Los subtítulos se recortan y reubican por segmento teniendo en cuenta pausas eliminadas y velocidad,
de modo que audio, vídeo y subtítulos permanecen sincronizados.

**Editor de subtítulos** (sub-pestaña): edición de texto y tiempos, dividir/unir líneas, desfase global,
convertir SRT↔VTT, avisos de longitud de línea y número de líneas, y comprobación contra el
**glosario de nombres propios** (Ajustes).

### Pestaña Planillas

Cada planilla es una **fuente independiente**, normalmente una por canal. Columnas mínimas:

| Columna | Descripción |
|---|---|
| `archivo_video` | Nombre o ruta del vídeo (relativo a la planilla o a la carpeta del canal) |
| `canal` | Nombre del canal tal como está en Ajustes (`Español`, `Inglés`) |
| `titulo` | Máx. 100 caracteres |
| `descripcion` | Máx. 5000 caracteres |
| `etiquetas` | Separadas por comas |
| `fecha_hora_publicacion` | `AAAA-MM-DD HH:MM` (también `DD/MM/AAAA HH:MM`). Vacía = publicar al subir |
| `estado` | `pendiente`, `esperando_aprobacion`, `subiendo`, `publicado`, `error`, `reintentando` |
| `url_publicado` | La rellena la app |

Opcionales: `grupo` (empareja la versión ES con la EN), `miniatura`, `subtitulos`.

- **Plantilla oficial** descargable desde la app; ejemplos en `samples/planilla_es.csv` y
  `samples/planilla_en.csv`.
- Acepta CSV (`,` `;` o tabulador) y XLSX, incluidas las **exportadas desde Google Sheets**
  (Archivo › Descargar › CSV/XLSX; no se usa ninguna API). También reconoce cabeceras en inglés
  (`title`, `description`, `tags`, `publish_at`…).
- **Validación por fila**: archivo existente, longitudes, fecha válida, duplicados, caracteres no
  permitidos (`<` `>`). Las filas con error se marcan en rojo y **no bloquean al resto**.
- **Recargar** sincroniza sin perder estado ni URL (los gestiona la app) y guarda un **historial de
  cambios por fila**.
- **Reglas desde el nombre de archivo**: `la_casa_parte3_de7.mp4` → `{titulo}`=«la casa»,
  `{parte}`=3, `{total}`=7. Las **plantillas por canal** (título, descripción, etiquetas) usan estas
  variables y **bloques reutilizables** (`{bloque:suscripcion}`, `{bloque:enlaces}`, `{bloque:creditos}`).
- Detección de **títulos duplicados** contra el historial, **vista previa tipo tarjeta**, filtros y
  orden por estado, canal o fecha, **emparejar una carpeta** de vídeos por nombre y **exportar** con
  estados y URLs.
- **Alertas multi-idioma**: grupo en español sin fila en inglés, o sin título traducido.

### Pestaña Cola

- Intervalo entre subidas (3 h por defecto) **guardado en SQLite**: tras reiniciar la app se respeta
  contando desde la última subida real.
- Franjas preferidas, separación mínima por canal, límite diario, días de descanso.
- Pausa global y por canal; **reordenar arrastrando**; **desplazar en bloque** las fechas (+2 h…).
- **Calendario por canal** y **modo simulación** (qué se publicaría y cuándo, sin subir nada).
- Reintentos con **espera exponencial** (máx. 3) y motivo del fallo registrado. Si Studio ya asignó un
  ID al vídeo, el reintento **retoma** desde la página de edición de ese vídeo en lugar de volver a subirlo.
- Tras un cierre inesperado, los elementos en «subiendo» vuelven a «pendiente».
- **Iniciar cola**: la app comprueba cada minuto si toca subir el siguiente elemento.

### Pestaña Revisión

- Con el **modo de aprobación** activo, ningún vídeo se sube sin aprobarlo.
- Vista previa **en bucle** del vídeo final con título, descripción y etiquetas, y **comparación** con
  otra versión.
- **Lista de comprobación** personalizable (Ajustes).
- **Comprobaciones técnicas**: resolución, duración máxima de 60 s para Shorts, bitrate, audio saturado
  o demasiado bajo, fotogramas negros o congelados (se ignora el congelado final intencionado).
- Cada aprobación registra fecha, versión y **SHA-256**; si el archivo cambia después, la subida se
  bloquea hasta volver a aprobar.

### Subida con Playwright

Por cada elemento: abre Studio con el perfil del canal → selecciona el archivo → rellena título,
descripción y etiquetas → marca «No es para niños» → sube la miniatura → añade a la lista de
reproducción → sube el SRT si Studio lo permite → programa la fecha de la planilla (o público
inmediato) → **espera a que termine el procesamiento** → guarda → registra el ID y la URL.

- Selectores robustos (atributos `name`/`id`, roles ARIA y texto visible según el idioma de Studio) y
  **esperas explícitas**, sin tiempos fijos.
- **Modo de prueba**: sube como privado o no listado (Ajustes).
- Al fallar, captura de pantalla + HTML en `diagnostico/<canal>/`.

### Pestaña Registro

Registros de la app y de las subidas con filtro, **exportar** a TXT/CSV, **resumen diario** y panel de
**estadísticas** (vídeos por día, tasa de errores, tiempo medio de procesado y estadísticas por canal).

### Ajustes

FFmpeg y aceleración por hardware, hilos, caché, parámetros de corte, cola y revisión, canales
(crear, **clonar para otro idioma**, sesión), tema claro/oscuro, atajos, glosario, **copias de
seguridad** (automáticas cada 6 h y al cerrar) y **exportar/importar proyectos JSON**.

Búsqueda global: `Ctrl+F` (vídeos, filas, cola y registros).

---

## Línea de comandos

```bash
python -m shortsfactory --cli one-click historia.mp4 --canal Español --modo parts
```

```bash
python -m shortsfactory --cli planilla samples/planilla_es.csv --canal Español
```

```bash
python -m shortsfactory --cli simular
```

```bash
python -m shortsfactory --cli tick
```

## Pruebas

Toda la suite con un solo comando:

```bash
python -m pytest
```

(o `scripts/run_tests.sh` / `scripts\run_tests.bat`). Incluye pruebas unitarias de validación de
planillas, cálculo de cortes, intervalos de la cola, generación de comandos FFmpeg, títulos duplicados y
hashes, y pruebas de integración que generan un vídeo corto con FFmpeg, lo renderizan y verifican con
ffprobe resolución, duración y sincronía audio/vídeo (se omiten si FFmpeg no está instalado). Durante
la prueba de render se bloquean los sockets de Python para comprobar que el procesamiento no abre
conexiones de red.

Prueba de aceptación completa (lenta, varios minutos): un vídeo de 20 minutos → 7 partes de 3 minutos
en 1080x1920, sincronizadas:

```bash
python scripts/acceptance_20min.py
```

## Arquitectura

```
shortsfactory/
  config.py            rutas y ajustes por defecto
  core/                modelos (Segment, RenderOptions, estados) y utilidades de tiempo
  persistence/db.py    SQLite: esquema, ajustes, registros, copias de seguridad
  ingest/              cálculo de cortes (puro) y análisis con FFmpeg
  ffmpeg/              ejecución con progreso/cancelación, grafo de filtros, comando de render (puro)
  framing/             filtros de encuadre/color/Ken Burns y seguimiento de cara (OpenCV)
  audio/               filtros de audio: loudnorm, ducking, efectos, voz
  subtitles/           SRT/VTT/ASS, edición, karaoke, glosario y overlays de texto
  sheets/              planillas: lectura, validación, reglas y sincronización con la BD
  scheduling/          planificador (puro) y cola persistente con estados y reintentos
  review/              comprobaciones técnicas
  upload/              subida con Playwright y ejecutor de la cola
  channels/            perfiles de canal y multi-idioma
  library/             hashes, caché, espacio en disco, biblioteca de activos
  processing.py        servicio de render (caché, hardware con respaldo, paralelo)
  pipeline.py          flujo completo / modo de un clic
  app_context.py       contenedor de servicios, estadísticas, exportaciones, proyectos
  ui/                  interfaz PySide6 (solo presentación; sin lógica de procesamiento)
```

Cada comando de FFmpeg se genera en una función pura (`ffmpeg/commands.py`, `framing/filters.py`,
`audio/filters.py`, `subtitles/overlays.py`) que se prueba sin ejecutar FFmpeg.

## Limitaciones conocidas

- **La automatización del navegador depende de la interfaz de YouTube Studio.** Cambios de diseño,
  textos o flujos (por ejemplo, el selector de fecha de programación o el paso de subtítulos) pueden
  romperla. La subida de subtítulos, miniatura y lista de reproducción se intenta como «mejor esfuerzo»:
  si falla se registra un aviso y la subida continúa.
- El **idioma de Studio** configurado en cada canal debe coincidir con el de la cuenta, porque parte de
  los selectores usan texto visible y el formato de la fecha de programación depende del idioma.
- YouTube puede pedir verificaciones adicionales (2FA, captcha, verificación del canal): resuélvelas a
  mano en la ventana del navegador; la app no intenta saltarlas.
- Las miniaturas personalizadas requieren un canal verificado en YouTube.
- El seguimiento de cara usa Haar Cascade (frontal); con caras de perfil o poca luz vuelve al centro.
- `zoompan` (Ken Burns) puede producir un leve temblor en zooms muy lentos; usa preferentemente zooms
  cortos o desactívalo.
- Las subidas se ejecutan de una en una; el procesamiento de vídeo sí es paralelo.
