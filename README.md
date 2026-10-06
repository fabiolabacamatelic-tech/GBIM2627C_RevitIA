# Revit IA

Asistente de inteligencia artificial para Revit. Añade una pestaña **Revit IA** con un chat donde puedes preguntar por tu modelo y pedir cambios en lenguaje normal:

> *¿Cuántos muros hay de cada tipo?*
> *Crea una planta del Nivel 00 y colócala en un plano A-101.*
> *Crea una vista donde cada elemento tenga un color según su workset.*

Funciona con **pyRevit** y usa la API de **OpenAI** (ChatGPT).

---

## Requisitos

| Necesitas | Detalle |
|---|---|
| Revit | 2025, 2026 o 2027 |
| pyRevit | Instalado y funcionando en tu Revit ([descarga](https://github.com/pyrevitlabs/pyRevit/releases)) |
| Cuenta de OpenAI | Con una **API key** propia y saldo (ver más abajo). ChatGPT Plus **no** sirve: la API se paga aparte. |

---

## Instalación

### Opción A: con un comando (recomendada)

Abre **PowerShell** o **Símbolo del sistema** y ejecuta:

```
pyrevit extend ui RevitIA https://github.com/DemianRomero-1989/GBIM26271C_RevitIA.git
```

Después abre Revit (o, si ya está abierto, pulsa **pyRevit → Reload**). Aparecerá la pestaña **Revit IA**.

Para **actualizar** a la última versión:

```
pyrevit extensions update RevitIA
```

> Si Windows no reconoce el comando `pyrevit`, usa la ruta completa:
> `%APPDATA%\pyRevit-Master\bin\pyrevit.exe extend ui RevitIA https://github.com/DemianRomero-1989/GBIM26271C_RevitIA.git`

### Opción B: descargando el ZIP

1. En esta página de GitHub, pulsa **Code → Download ZIP** y descomprímelo.
2. Cambia el nombre de la carpeta descomprimida a `RevitIA.extension`.
3. Muévela a una carpeta fija, por ejemplo `C:\pyRevit\RevitIA.extension`.
4. En Revit: **pyRevit → Settings → Custom Extension Directories → Add folder** y elige la carpeta **que contiene** `RevitIA.extension` (en el ejemplo, `C:\pyRevit`).
5. Pulsa **Save Settings and Reload**.

---

## Configurar OpenAI (solo la primera vez)

1. Entra en [platform.openai.com](https://platform.openai.com) e inicia sesión.
2. **Añade saldo:** *Settings → Billing → Add payment details*. Con 5–10 $ hay de sobra para el curso.
3. **Desactiva la recarga automática** (*auto-recharge*) para no gastar más de lo que has cargado. Cuando el saldo llegue a 0, el asistente simplemente dejará de responder.
4. **Crea tu clave:** *Settings → API keys → Create new secret key*. Cópiala: empieza por `sk-` y solo se muestra una vez.
5. En Revit, pulsa el botón **Preguntar GPT**. La primera vez te pedirá la clave: pégala y pulsa OK.

> **Tu clave es personal.** Es como una contraseña: quien la tenga puede gastar tu saldo. No la compartas ni la subas a ningún sitio. Si la expones por error, bórrala en OpenAI y crea otra.
>
> Para cambiar la clave más adelante: **Shift + clic** en el botón.

---

## Uso

1. Abre un modelo y pulsa **Revit IA → Preguntar GPT**.
2. Escribe tu pregunta o petición. **Ctrl+Enter** envía; **Enter** hace salto de línea.
3. Es una conversación: puedes seguir preguntando sobre lo anterior (*"¿y cuáles son de 30 cm?"*).
4. En gris verás qué consulta hace la IA al modelo y qué resultado obtiene.
5. **Antes de cualquier cambio aparece una ventana de confirmación.** Todos los cambios se deshacen con **Ctrl+Z**.
6. **Nueva conversación** borra el historial.
7. Con el desplegable **Modelo** eliges qué modelo de ChatGPT responde. Se recuerda para la próxima vez.

| Modelo | Cuándo usarlo |
|---|---|
| `gpt-4.1-mini` | Preguntas sencillas. El más barato y con límites de uso más altos. |
| `gpt-4.1` | Uso general (recomendado). |
| `gpt-5-mini` | Peticiones con varios pasos. Razona más, pero tarda más. |
| `gpt-5` | Lo más complejo. El más lento y caro. |

### Qué puede hacer

**Consultar el modelo**
- Resumen general: vistas, planos, niveles, categorías, tipos y worksets
- Buscar elementos y filtrarlos por familia, tipo, nivel, workset o cualquier parámetro
- Ver todos los parámetros de un elemento o de la selección
- Listar plantillas de vista, habitaciones, tablas, patrones, familias y tipos

**Modificar el modelo** *(siempre con confirmación)*
- Cambiar parámetros de ejemplar y de tipo
- Renombrar vistas, planos, niveles y tipos; duplicar tipos; cambiar el tipo de elementos
- Mover elementos a otro workset
- Mover y rotar elementos; cambiar la cota de un nivel
- Cargar una familia `.rfa` desde tu ordenador

**Vistas y documentación** *(siempre con confirmación)*
- Crear plantas, secciones y vistas 3D; crear planos y colocar vistas
- Crear tablas de planificación con campos, filtros, agrupación y orden
- Mostrar u ocultar categorías o elementos; cambiar escala, nivel de detalle y plantilla
- Colorear elementos (color, patrón, transparencia, grosor de línea)
- Crear una vista coloreada por workset, categoría o valor de un parámetro, con leyenda
- Etiquetar elementos
- Exportar vistas a PDF, PNG, JPG, BMP o TIFF, y tablas a CSV

### Qué no puede hacer (todavía)

- Dibujar geometría nueva (muros, suelos, cubiertas...)
- Editar capas de muros/suelos o crear materiales
- Gestionar fases, grupos, opciones de diseño o parámetros compartidos
- Crear leyendas o editar anotaciones existentes

Si se lo pides, te dirá que esa acción no está disponible.

---

## Privacidad y buen uso

- **Los datos del modelo que consulta la IA se envían a OpenAI** (nombres, tipos, parámetros...). No lo uses con proyectos reales de un estudio o cliente sin su permiso.
- La IA puede equivocarse. **Lee siempre la ventana de confirmación** antes de aceptar un cambio y comprueba el resultado.
- Prueba primero en **una copia del modelo**.
- El asistente **no ejecuta código**: solo puede usar las acciones predefinidas de este botón.

---

## Problemas frecuentes

| Mensaje | Qué significa |
|---|---|
| `429 Too Many Requests` / *Límite de velocidad* | Has enviado mucho texto en poco tiempo. El asistente reintenta solo; si persiste, espera un minuto. |
| *Sin saldo en OpenAI* | Añade créditos en *Settings → Billing*. |
| `401 Unauthorized` | La clave no es válida. **Shift + clic** en el botón para pegar otra. |
| La pestaña no aparece | **pyRevit → Reload**, o reinicia Revit. |

Mientras la ventana del chat está abierta no puedes usar Revit. Ciérrala para seleccionar elementos y vuelve a abrirla (la conversación se reinicia).

---

## Para curiosos: cómo está hecho

```
RevitIA.extension/
└── Revit IA.tab/                     → pestaña de la cinta
    └── ChatGPT.panel/                → panel
        └── PreguntarGPT.pushbutton/  → botón
            ├── script.py             → todo el código
            └── icon.png
```

En `script.py`:

1. **Herramientas (`h_...`)**: funciones de Python que leen o modifican el modelo con la API de Revit.
2. **`TOOLS`**: la descripción de esas herramientas que se envía a OpenAI, para que ChatGPT sepa qué puede pedir.
3. **`SISTEMA`**: las instrucciones generales que recibe ChatGPT.
4. **Bucle del chat**: envía la conversación a OpenAI; si ChatGPT pide una herramienta, el script la ejecuta y le devuelve el resultado, hasta que responde al usuario.

Los modelos que aparecen en el desplegable están en la lista `MODELOS`, al principio de `script.py`. Puedes añadir o quitar modelos de OpenAI ahí.
