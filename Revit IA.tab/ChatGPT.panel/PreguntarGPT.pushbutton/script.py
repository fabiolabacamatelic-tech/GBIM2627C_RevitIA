# -*- coding: utf-8 -*-
"""Revit IA: chat con ChatGPT dentro de Revit (pyRevit, IronPython 2.7, Revit 2025-2027).

Cómo funciona:
  1. El usuario escribe en la ventana de chat.
  2. ChatGPT (API de OpenAI) decide qué herramientas necesita y las pide.
  3. Este script ejecuta esas herramientas sobre el modelo y le devuelve los resultados.
  4. Las herramientas que modifican el modelo piden confirmación y se deshacen con Ctrl+Z.
ChatGPT nunca ejecuta código: solo puede usar las herramientas definidas aquí.

Organización del archivo:
  - Configuración y API key
  - JSON (IronPython no maneja bien tildes con el módulo json)
  - Utilidades de lectura del modelo
  - Herramientas de consulta (h_*)
  - Herramientas de cambio (h_*)
  - Definición de herramientas para OpenAI (TOOLS) e instrucciones (SISTEMA)
  - Llamada a la API
  - Ventana de chat
"""
__title__ = "Preguntar\nGPT"
__doc__ = ("Chat con ChatGPT sobre el modelo abierto. Puede consultar el modelo y "
           "proponer cambios (siempre pide confirmación; se deshacen con Ctrl+Z).\n"
           "Shift+Click para cambiar la API key.")

import os
import re
import json
import math
import time
import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System import Int64, Action
from System.Collections.Generic import List
from System.Net import WebClient, WebException, ServicePointManager, SecurityProtocolType
from System.IO import StreamReader, File
from System.Text import Encoding
from System.Windows import Thickness, CornerRadius, HorizontalAlignment, TextWrapping, FontStyles
from System.Windows.Controls import Border, TextBox, ComboBoxItem
from System.Windows.Media import BrushConverter, Brushes
from System.Windows.Threading import DispatcherPriority
from System.Windows.Input import Key, Keyboard, ModifierKeys
from Autodesk.Revit.DB import (
    # Elementos, filtros y parámetros
    FilteredElementCollector, FilteredWorksetCollector, WorksetKind, ElementId, ElementType,
    Family, Level, CategoryType, BuiltInCategory, BuiltInParameter, StorageType, SpecTypeId,
    UnitUtils, UnitTypeId,
    # Geometría y transformaciones
    XYZ, UV, Line, Transform, BoundingBoxXYZ, LocationPoint, LocationCurve, ElementTransformUtils,
    # Vistas, planos y gráficos
    View, View3D, ViewPlan, ViewSection, ViewSheet, ViewSchedule, ViewType, ViewFamily,
    ViewFamilyType, ViewDuplicateOption, ViewDetailLevel, Viewport, OverrideGraphicSettings,
    Color, FillPatternElement, LinePatternElement,
    # Etiquetas
    IndependentTag, LinkElementId, Reference, TagMode, TagOrientation,
    # Tablas de planificación
    ScheduleFilter, ScheduleFilterType, ScheduleSortGroupField, ScheduleSortOrder, SectionType,
    # Exportación
    PDFExportOptions, ImageExportOptions, ImageFileType, ImageResolution, ExportRange,
    FitDirectionType, ZoomType, ViewSet)
from pyrevit import revit, forms, script

# ================================================================ Configuración

# Modelos del desplegable del chat: (id de OpenAI, descripción). Se pueden añadir o quitar.
MODELOS = [
    ("gpt-4.1-mini", u"rápido y barato"),
    ("gpt-4.1", u"equilibrado (recomendado)"),
    ("gpt-5-mini", u"razona más, más lento"),
    ("gpt-5", u"el más capaz, más lento y caro"),
]
MODELO_POR_DEFECTO = "gpt-4.1"
URL = "https://api.openai.com/v1/chat/completions"
MAX_CONSULTAS = 15        # consultas seguidas al modelo por cada mensaje
MAX_TEXTO = 15000         # recorte de cada resultado de herramienta (caracteres)
MAX_TEXTO_ANTIGUO = 1500  # recorte de resultados de turnos anteriores del chat
REINTENTOS = 4            # reintentos automáticos si OpenAI responde "Too Many Requests"

cfg = script.get_config()
doc = revit.doc
uidoc = revit.uidoc


def obtener_api_key():
    # 1) Variable de entorno; 2) clave guardada en la config de pyRevit; 3) pedirla
    key = os.environ.get("OPENAI_API_KEY")
    if key and not __shiftclick__:
        return key
    key = cfg.get_option("api_key", "")
    if key and not __shiftclick__:
        return key
    key = forms.ask_for_string(
        prompt="Pega tu API key de OpenAI (empieza por sk-...)",
        title="ChatGPT - API key")
    if not key:
        script.exit()
    cfg.api_key = key.strip()
    script.save_config()
    return key.strip()


API_KEY = obtener_api_key()


# ================================================================ JSON

def json_str(texto):
    # json.dumps de IronPython falla con tildes/ñ: escapamos a mano a \uXXXX
    partes = []
    for ch in texto:
        o = ord(ch)
        if ch == u'"':
            partes.append(u'\\"')
        elif ch == u'\\':
            partes.append(u'\\\\')
        elif o < 0x20 or o > 0x7e:
            partes.append(u'\\u%04x' % o)
        else:
            partes.append(ch)
    return u'"' + u''.join(partes) + u'"'


def a_json(o):
    if o is None:
        return u"null"
    if o is True:
        return u"true"
    if o is False:
        return u"false"
    if isinstance(o, (int, long)):
        return u"{}".format(o)
    if isinstance(o, float):
        return repr(o)
    if isinstance(o, basestring):
        return json_str(o)
    if isinstance(o, dict):
        return u"{" + u", ".join(json_str(k) + u": " + a_json(v) for k, v in o.items()) + u"}"
    if isinstance(o, (list, tuple)):
        return u"[" + u", ".join(a_json(x) for x in o) + u"]"
    return json_str(u"{}".format(o))


def a_python(e):
    # Convierte un JsonElement de .NET en dict/list/str/número de Python
    from System.Text.Json import JsonValueKind
    k = e.ValueKind
    if k == JsonValueKind.Object:
        return dict((p.Name, a_python(p.Value)) for p in e.EnumerateObject())
    if k == JsonValueKind.Array:
        return [a_python(x) for x in e.EnumerateArray()]
    if k == JsonValueKind.String:
        return e.GetString()
    if k == JsonValueKind.Number:
        d = e.GetDouble()
        return int(d) if d == int(d) else d
    if k == getattr(JsonValueKind, "True"):
        return True
    if k == getattr(JsonValueKind, "False"):
        return False
    return None


def leer_json(texto):
    try:
        # Parser de .NET: maneja bien tildes y ñ
        clr.AddReference("System.Text.Json")
        from System.Text.Json import JsonDocument
        return a_python(JsonDocument.Parse(texto).RootElement)
    except Exception:
        return json.loads(texto)


# ================================================================ Lectura del modelo

def nombre_de(el):
    try:
        return el.Name
    except Exception:
        return u"?"


def a_id(n):
    return ElementId(Int64(int(n)))


def nombre_tipo(el):
    # "Familia: Tipo [tipo id N]" para que ChatGPT pueda usar el id del tipo
    t = doc.GetElement(el.GetTypeId())
    if t is None:
        return u""
    familia = getattr(t, "FamilyName", u"") or u""
    nombre = u"{}: {}".format(familia, nombre_de(t)) if familia else nombre_de(t)
    return u"{} [tipo id {}]".format(nombre, t.Id)


def nivel_de(el):
    try:
        if el.LevelId != ElementId.InvalidElementId:
            return nombre_de(doc.GetElement(el.LevelId))
    except Exception:
        pass
    for nombre in (u"Nivel", u"Nivel de referencia", u"Restricción de base", u"Nivel base",
                   u"Level", u"Reference Level", u"Base Constraint", u"Base Level"):
        p = el.LookupParameter(nombre)
        if p is not None and p.HasValue:
            return p.AsValueString() or u""
    return u""


def texto_param(p):
    if p is None or not p.HasValue:
        return None
    v = p.AsValueString() or p.AsString()
    if v is None and p.StorageType == StorageType.Integer:
        v = u"{}".format(p.AsInteger())
    return v


def workset_de(el):
    if not doc.IsWorkshared:
        return u""
    try:
        return doc.GetWorksetTable().GetWorkset(el.WorksetId).Name
    except Exception:
        return u""


def valor_param(el, nombre):
    # "Workset"/"Subproyecto" se resuelve directamente (su nombre varía según el idioma)
    if (nombre or u"").strip().lower() in (u"workset", u"subproyecto", u"conjunto de trabajo"):
        return workset_de(el) or None
    # Busca primero en el ejemplar y luego en el tipo
    v = texto_param(el.LookupParameter(nombre))
    if v is None:
        t = doc.GetElement(el.GetTypeId())
        if t is not None:
            v = texto_param(t.LookupParameter(nombre))
    return v


def params_de(el, prefijo=u""):
    res = []
    for p in el.Parameters:
        v = texto_param(p)
        if v:
            ro = u" (solo lectura)" if p.IsReadOnly else u""
            res.append(u"{}{}={}{}".format(prefijo, p.Definition.Name, v, ro))
    return sorted(res)


def elementos_categoria(nombre_cat):
    buscada = (nombre_cat or u"").strip().lower()
    return [e for e in FilteredElementCollector(doc).WhereElementIsNotElementType()
            if e.Category is not None and e.Category.Name.lower() == buscada]


def categorias_modelo():
    cats = set()
    for e in FilteredElementCollector(doc).WhereElementIsNotElementType():
        if e.Category is not None and e.Category.CategoryType == CategoryType.Model:
            cats.add(e.Category.Name)
    return sorted(cats)


def sin_categoria(nombre_cat):
    return (u"No hay elementos de la categoría '{}'. Categorías con elementos: {}"
            .format(nombre_cat, u", ".join(categorias_modelo())))


def elementos_destino(args):
    if args.get("ids"):
        els = [doc.GetElement(a_id(i)) for i in args["ids"]]
        return [e for e in els if e is not None]
    if args.get("seleccion"):
        return list(revit.get_selection().elements)
    if args.get("categoria"):
        return elementos_categoria(args["categoria"])
    return []


# ================================================================ Herramientas de consulta

def h_resumen_modelo(args):
    L = []
    L.append(u"Proyecto: {}".format(doc.Title))
    L.append(u"Archivo: {}".format(doc.PathName or u"(sin guardar)"))
    L.append(u"Colaborativo (worksharing): {}".format(u"sí" if doc.IsWorkshared else u"no"))
    L.append(u"Vista activa: {} [id {}]".format(doc.ActiveView.Name, doc.ActiveView.Id))

    internas = (ViewType.ProjectBrowser, ViewType.SystemBrowser,
                ViewType.Internal, ViewType.Undefined, ViewType.DrawingSheet)
    por_tipo = {}
    plantillas = 0
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate:
            plantillas += 1
            continue
        if v.ViewType in internas:
            continue
        por_tipo.setdefault(str(v.ViewType), []).append(u"{} [id {}]".format(v.Name, v.Id))
    total = sum(len(x) for x in por_tipo.values())
    L.append(u"\nVISTAS: {} en total (sin contar planos ni plantillas)".format(total))
    for tipo in sorted(por_tipo):
        nombres = sorted(por_tipo[tipo])
        extra = u" ..." if len(nombres) > 40 else u""
        L.append(u"- {} ({}): {}{}".format(tipo, len(nombres), u", ".join(nombres[:40]), extra))
    L.append(u"Plantillas de vista: {}".format(plantillas))

    planos = sorted(FilteredElementCollector(doc).OfClass(ViewSheet), key=lambda s: s.SheetNumber)
    L.append(u"\nPLANOS: {}".format(len(planos)))
    for s in planos[:60]:
        L.append(u"- {} - {} [id {}]".format(s.SheetNumber, s.Name, s.Id))
    if len(planos) > 60:
        L.append(u"- ... ({} más)".format(len(planos) - 60))

    niveles = sorted(FilteredElementCollector(doc).OfClass(Level), key=lambda n: n.Elevation)
    L.append(u"\nNIVELES: {}".format(len(niveles)))
    for n in niveles:
        cota = UnitUtils.ConvertFromInternalUnits(n.Elevation, UnitTypeId.Meters)
        L.append(u"- {}: {:.2f} m [id {}]".format(n.Name, cota, n.Id))

    cuenta = {}
    tipos = {}
    cache_tipo = {}
    por_workset = {}
    tabla_ws = doc.GetWorksetTable() if doc.IsWorkshared else None
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        cat = el.Category
        if cat is None or cat.CategoryType != CategoryType.Model:
            continue
        cuenta[cat.Name] = cuenta.get(cat.Name, 0) + 1
        clave = str(el.GetTypeId())
        if clave not in cache_tipo:
            cache_tipo[clave] = nombre_tipo(el)
        if cache_tipo[clave]:
            d = tipos.setdefault(cat.Name, {})
            d[cache_tipo[clave]] = d.get(cache_tipo[clave], 0) + 1
        if tabla_ws:
            ws = tabla_ws.GetWorkset(el.WorksetId).Name
            por_workset[ws] = por_workset.get(ws, 0) + 1
    L.append(u"\nELEMENTOS DE MODELO POR CATEGORÍA (tipos usados y cuántos hay):")
    for nombre, n in sorted(cuenta.items(), key=lambda x: -x[1])[:50]:
        L.append(u"- {}: {}".format(nombre, n))
        lista = sorted(tipos.get(nombre, {}).items(), key=lambda x: -x[1])
        for nombre_t, nt in lista[:30]:
            L.append(u"    · {} ({})".format(nombre_t, nt))
        if len(lista) > 30:
            L.append(u"    · ... y {} tipos más".format(len(lista) - 30))
    if tabla_ws:
        L.append(u"\nWORKSETS (elementos de modelo en cada uno):")
        for nombre, n in sorted(por_workset.items(), key=lambda x: -x[1]):
            L.append(u"- {}: {}".format(nombre, n))
    L.append(u"\nElementos seleccionados ahora: {}".format(len(revit.get_selection().elements)))
    return u"\n".join(L)


def h_listar_parametros(args):
    els = elementos_categoria(args.get("categoria"))
    if not els:
        return sin_categoria(args.get("categoria"))
    ejemplar = {}
    de_tipo = {}
    for el in els[:10]:
        for p in el.Parameters:
            ejemplar.setdefault(p.Definition.Name, texto_param(p))
        t = doc.GetElement(el.GetTypeId())
        if t is not None:
            for p in t.Parameters:
                de_tipo.setdefault(p.Definition.Name, texto_param(p))
    L = [u"Categoría {} ({} elementos).".format(args.get("categoria"), len(els)),
         u"PARÁMETROS DE EJEMPLAR (nombre = valor de ejemplo):"]
    L += [u"- {} = {}".format(k, v or u"") for k, v in sorted(ejemplar.items())]
    L.append(u"PARÁMETROS DE TIPO (no modificables con cambiar_parametro):")
    L += [u"- {} = {}".format(k, v or u"") for k, v in sorted(de_tipo.items())]
    return u"\n".join(L)


def h_buscar_elementos(args):
    els = elementos_categoria(args.get("categoria"))
    if not els:
        return sin_categoria(args.get("categoria"))
    familia = (args.get("familia") or u"").strip().lower()
    tipo = (args.get("tipo") or u"").strip().lower()
    nivel = (args.get("nivel") or u"").strip().lower()
    if familia:
        els = [e for e in els
               if familia in (getattr(doc.GetElement(e.GetTypeId()), "FamilyName", u"") or u"").lower()]
    if tipo:
        els = [e for e in els
               if tipo in (nombre_de(doc.GetElement(e.GetTypeId())) or u"").lower()]
    if nivel:
        els = [e for e in els if nivel in (nivel_de(e) or u"").lower()]
    fp = args.get("filtro_parametro")
    fv = (args.get("filtro_valor") or u"").lower()
    if fp:
        els = [e for e in els if fv in (valor_param(e, fp) or u"").lower()]
    limite = max(1, min(int(args.get("limite") or 200), 500))
    pedidos = args.get("parametros") or []
    L = [u"{} elementos encontrados{}.".format(
        len(els), u" (se muestran {})".format(limite) if len(els) > limite else u"")]
    for e in els[:limite]:
        partes = [u"id {}".format(e.Id), nombre_tipo(e) or nombre_de(e)]
        nivel = nivel_de(e)
        if nivel:
            partes.append(u"nivel " + nivel)
        ws = workset_de(e)
        if ws:
            partes.append(u"workset " + ws)
        for nombre in pedidos:
            partes.append(u"{}={}".format(nombre, valor_param(e, nombre) or u"(vacío)"))
        L.append(u" | ".join(partes))
    return u"\n".join(L)


def h_listar_recursos(args):
    tipo = args.get("tipo")
    limite = max(1, min(int(args.get("limite") or 200), 500))
    filas = []

    if tipo in (u"vistas", u"plantillas"):
        for v in FilteredElementCollector(doc).OfClass(View):
            if v.IsTemplate != (tipo == u"plantillas"):
                continue
            plantilla = (doc.GetElement(v.ViewTemplateId)
                         if v.ViewTemplateId != ElementId.InvalidElementId else None)
            detalle = u"{} | {}".format(v.ViewType, v.Name)
            if plantilla is not None:
                detalle += u" | plantilla: {}".format(plantilla.Name)
            filas.append(u"{} [id {}]".format(detalle, v.Id))
    elif tipo == u"niveles":
        for nivel in FilteredElementCollector(doc).OfClass(Level):
            cota = UnitUtils.ConvertFromInternalUnits(nivel.Elevation, UnitTypeId.Meters)
            filas.append(u"{} [id {}] | cota {:.3f} m".format(nivel.Name, nivel.Id, cota))
        filas.sort()
    elif tipo == u"habitaciones":
        habitaciones = (FilteredElementCollector(doc)
                        .OfCategory(BuiltInCategory.OST_Rooms)
                        .WhereElementIsNotElementType())
        for habitacion in habitaciones:
            nivel = doc.GetElement(habitacion.LevelId) if habitacion.LevelId else None
            area = UnitUtils.ConvertFromInternalUnits(habitacion.Area, UnitTypeId.SquareMeters)
            filas.append(u"{} [id {}] | {} | nivel {} | {:.2f} m²".format(
                valor_param(habitacion, u"Número") or nombre_de(habitacion),
                habitacion.Id,
                valor_param(habitacion, u"Nombre") or u"(sin nombre)",
                nombre_de(nivel) if nivel else u"(sin nivel)",
                area))
    elif tipo == u"tablas":
        for tabla in FilteredElementCollector(doc).OfClass(ViewSchedule):
            campos = []
            definicion = tabla.Definition
            for i in range(definicion.GetFieldCount()):
                campo = definicion.GetField(i)
                try:
                    campos.append(campo.GetName())
                except Exception:
                    campos.append(campo.ColumnHeading)
            filas.append(u"{} [id {}] | campos: {}".format(
                tabla.Name, tabla.Id, u", ".join(campos)))
    elif tipo == u"patrones":
        for patron in FilteredElementCollector(doc).OfClass(FillPatternElement):
            definicion = patron.GetFillPattern()
            filas.append(u"Relleno: {} [id {}] | {} | sólido: {}".format(
                patron.Name, patron.Id, definicion.Target, definicion.IsSolidFill))
        for patron in FilteredElementCollector(doc).OfClass(LinePatternElement):
            filas.append(u"Línea: {} [id {}]".format(
                patron.Name, patron.Id))
        filas.sort()
    elif tipo in (u"tipos", u"familias"):
        grupos = {}
        for elemento_tipo in FilteredElementCollector(doc).WhereElementIsElementType():
            if not isinstance(elemento_tipo, ElementType):
                continue
            categoria = (elemento_tipo.Category.Name
                         if elemento_tipo.Category is not None else u"(sin categoría)")
            familia = getattr(elemento_tipo, "FamilyName", u"") or u""
            clave = familia or categoria
            if tipo == u"familias":
                grupos.setdefault((categoria, clave), set()).add(nombre_de(elemento_tipo))
            else:
                filas.append(u"{} | {}: {} [tipo id {}]".format(
                    categoria, clave, nombre_de(elemento_tipo), elemento_tipo.Id))
        if tipo == u"familias":
            for (categoria, familia), nombres in sorted(grupos.items()):
                filas.append(u"{} | {} | tipos: {}".format(
                    categoria, familia, u", ".join(sorted(nombres))))
    else:
        return u"Tipo de recurso no válido. Usa: vistas, plantillas, niveles, habitaciones, tablas, patrones, familias o tipos."

    encabezado = u"{} {} encontrados".format(len(filas), tipo)
    if len(filas) > limite:
        encabezado += u" (se muestran {})".format(limite)
    return encabezado + u".\n" + u"\n".join(filas[:limite])


def h_ver_elemento(args):
    el = doc.GetElement(a_id(args.get("id")))
    if el is None:
        return u"No existe el elemento con id {}".format(args.get("id"))
    cat = el.Category.Name if el.Category else u"Sin categoría"
    L = [u"Id {}: {} / {} / nivel {} / workset {}".format(
        el.Id, cat, nombre_tipo(el) or nombre_de(el), nivel_de(el), workset_de(el) or u"-"),
         u"EJEMPLAR:"] + params_de(el)
    t = doc.GetElement(el.GetTypeId())
    if t is not None:
        L += [u"TIPO:"] + params_de(t)
    return u"\n".join(L)


def h_ver_seleccion(args):
    sel = revit.get_selection().elements
    if not sel:
        return u"No hay nada seleccionado en Revit."
    L = [u"{} elementos seleccionados.".format(len(sel))]
    for el in sel[:20]:
        cat = el.Category.Name if el.Category else u"Sin categoría"
        L.append(u"* Id {}: {} / {}".format(el.Id, cat, nombre_tipo(el) or nombre_de(el)))
        L.append(u"  " + u"; ".join(params_de(el)[:40]))
    if len(sel) > 20:
        L.append(u"(... y {} más)".format(len(sel) - 20))
    return u"\n".join(L)


# ================================================================ Herramientas de cambio

def confirmar(descripcion):
    return forms.alert(u"ChatGPT quiere hacer este cambio:\n\n{}\n\n¿Aplicarlo?".format(descripcion),
                       yes=True, no=True)


def con_errores(texto, errores):
    # Añade al resultado los errores por elemento (máximo 20)
    if errores:
        texto += u" Errores ({}): {}".format(len(errores), u"; ".join(errores[:20]))
    return texto


def poner_valor(p, valor):
    valor = u"{}".format(valor).strip()
    st = p.StorageType
    if st == StorageType.String:
        return p.Set(valor)
    if st == StorageType.Integer:
        try:
            es_si_no = p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo
        except Exception:
            es_si_no = False
        if es_si_no:
            return p.Set(1 if valor.lower() in (u"sí", u"si", u"yes", u"true", u"1") else 0)
        return p.Set(int(valor))
    if st == StorageType.Double:
        return p.SetValueString(valor)
    return False


def h_cambiar_parametro(args):
    els = elementos_destino(args)
    nombre = args.get("parametro")
    if not els:
        return u"No se encontraron elementos de destino."
    if not confirmar(u"Poner '{}' = '{}' en {} elementos".format(nombre, args.get("valor"), len(els))):
        return u"El usuario ha cancelado el cambio. No se ha modificado nada."
    hechos = 0
    errores = []
    with revit.Transaction("Revit IA: cambiar parámetro"):
        for el in els:
            p = el.LookupParameter(nombre)
            if p is None or p.IsReadOnly:
                errores.append(u"id {}: no existe o es de solo lectura".format(el.Id))
                continue
            try:
                if poner_valor(p, args.get("valor")) is False:
                    errores.append(u"id {}: valor no válido".format(el.Id))
                else:
                    hechos += 1
            except Exception as ex:
                errores.append(u"id {}: {}".format(el.Id, ex))
    return con_errores(u"Cambiados {} de {} elementos.".format(hechos, len(els)), errores)


def h_renombrar(args):
    el = doc.GetElement(a_id(args.get("id")))
    if el is None:
        return u"No existe el id {}".format(args.get("id"))
    desc = u"Renombrar '{}' a '{}'".format(nombre_de(el), args.get("nombre"))
    if args.get("numero"):
        desc += u" (número '{}')".format(args["numero"])
    if not confirmar(desc):
        return u"El usuario ha cancelado el cambio."
    try:
        with revit.Transaction("Revit IA: renombrar"):
            if args.get("numero") and isinstance(el, ViewSheet):
                el.SheetNumber = args["numero"]
            if args.get("nombre"):
                el.Name = args["nombre"]
        return u"Hecho: " + desc
    except Exception as ex:
        return u"Error al renombrar: {}".format(ex)


def tipo_por_id(tid):
    t = doc.GetElement(a_id(tid))
    return t if isinstance(t, ElementType) else None


def instancias_de_tipo(t):
    return [e for e in FilteredElementCollector(doc).WhereElementIsNotElementType()
            if e.GetTypeId() == t.Id]


def h_duplicar_tipo(args):
    t = tipo_por_id(args.get("tipo_id"))
    if t is None:
        return u"No existe un tipo con id {}".format(args.get("tipo_id"))
    nuevo = args.get("nuevo_nombre")
    if not confirmar(u"Duplicar el tipo '{}' como '{}'".format(nombre_de(t), nuevo)):
        return u"El usuario ha cancelado el cambio."
    try:
        with revit.Transaction("Revit IA: duplicar tipo"):
            copia = t.Duplicate(nuevo)
        return u"Tipo duplicado: '{}' [tipo id {}]".format(nuevo, copia.Id)
    except Exception as ex:
        return u"Error al duplicar: {}".format(ex)


def h_cambiar_tipo(args):
    t = tipo_por_id(args.get("tipo_id"))
    if t is None:
        return u"No existe un tipo con id {}".format(args.get("tipo_id"))
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos de destino."
    if not confirmar(u"Cambiar {} elementos al tipo '{}'".format(len(els), nombre_de(t))):
        return u"El usuario ha cancelado el cambio."
    hechos = 0
    errores = []
    with revit.Transaction("Revit IA: cambiar tipo"):
        for el in els:
            try:
                el.ChangeTypeId(t.Id)
                hechos += 1
            except Exception as ex:
                errores.append(u"id {}: {}".format(el.Id, ex))
    return con_errores(u"Cambiados {} de {} elementos al tipo '{}'.".format(
        hechos, len(els), nombre_de(t)), errores)


def h_cambiar_parametro_tipo(args):
    t = tipo_por_id(args.get("tipo_id"))
    if t is None:
        return u"No existe un tipo con id {}".format(args.get("tipo_id"))
    nombre = args.get("parametro")
    p = t.LookupParameter(nombre)
    if p is None or p.IsReadOnly:
        return u"El tipo no tiene el parámetro '{}' o es de solo lectura.".format(nombre)
    n = len(instancias_de_tipo(t))
    if not confirmar(u"Poner '{}' = '{}' en el TIPO '{}'.\nAfecta a {} elementos de ese tipo."
                     .format(nombre, args.get("valor"), nombre_de(t), n)):
        return u"El usuario ha cancelado el cambio."
    try:
        with revit.Transaction("Revit IA: parámetro de tipo"):
            ok = poner_valor(p, args.get("valor"))
        if ok is False:
            return u"Valor no válido para '{}'.".format(nombre)
        return u"Hecho: '{}' = '{}' en el tipo '{}' ({} elementos).".format(
            nombre, args.get("valor"), nombre_de(t), n)
    except Exception as ex:
        return u"Error: {}".format(ex)


def h_cambiar_workset(args):
    if not doc.IsWorkshared:
        return u"El modelo no tiene worksets (no es colaborativo)."
    buscado = (args.get("workset") or u"").strip().lower()
    disponibles = list(FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset))
    ws = [w for w in disponibles if w.Name.lower() == buscado]
    if not ws:
        return u"No existe el workset '{}'. Worksets disponibles: {}".format(
            args.get("workset"), u", ".join(w.Name for w in disponibles))
    ws = ws[0]
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos de destino."
    if not confirmar(u"Mover {} elementos al workset '{}'".format(len(els), ws.Name)):
        return u"El usuario ha cancelado el cambio."
    hechos = 0
    errores = []
    with revit.Transaction("Revit IA: cambiar workset"):
        for el in els:
            p = el.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
            if p is None or p.IsReadOnly:
                errores.append(u"id {}: su workset no se puede cambiar".format(el.Id))
                continue
            try:
                p.Set(ws.Id.IntegerValue)
                hechos += 1
            except Exception as ex:
                errores.append(u"id {}: {}".format(el.Id, ex))
    return con_errores(u"Movidos {} de {} elementos al workset '{}'.".format(
        hechos, len(els), ws.Name), errores)


def h_visibilidad_categorias(args):
    v = vista_objetivo(args)
    if v is None:
        return u"No existe la vista con id {}".format(args.get("vista_id"))
    plantilla = doc.GetElement(v.ViewTemplateId) if v.ViewTemplateId != ElementId.InvalidElementId else None
    if plantilla is not None and not args.get("quitar_plantilla"):
        return (u"La vista '{}' usa la plantilla '{}', que controla la visibilidad. Pregunta al "
                u"usuario si quiere quitarla y vuelve a llamar con quitar_plantilla=true."
                .format(v.Name, plantilla.Name))

    solo = set(s.strip().lower() for s in args.get("mostrar_solo") or [])
    ocultar = set(s.strip().lower() for s in args.get("ocultar") or [])
    mostrar = set(s.strip().lower() for s in args.get("mostrar") or [])
    plan = []  # (categoría, oculta)
    encontradas = set()
    for cat in doc.Settings.Categories:
        try:
            if not v.CanCategoryBeHidden(cat.Id):
                continue
        except Exception:
            continue
        nombre = cat.Name.lower()
        oculta = None
        if solo:
            if cat.CategoryType == CategoryType.Model:
                oculta = nombre not in solo
            elif args.get("ocultar_anotaciones") and cat.CategoryType == CategoryType.Annotation:
                oculta = True
        if nombre in ocultar:
            oculta = True
        if nombre in mostrar:
            oculta = False
        if nombre in solo | ocultar | mostrar:
            encontradas.add(nombre)
        if oculta is not None and v.GetCategoryHidden(cat.Id) != oculta:
            plan.append((cat, oculta))

    no_existen = (solo | ocultar | mostrar) - encontradas
    if no_existen:
        return u"Estas categorías no existen o no se pueden ocultar en esta vista: {}".format(
            u", ".join(sorted(no_existen)))
    if not plan:
        return u"La vista '{}' ya está así; no hay nada que cambiar.".format(v.Name)

    n_ocultas = len([1 for _, o in plan if o])
    desc = u"En la vista '{}': ocultar {} categorías y mostrar {}".format(
        v.Name, n_ocultas, len(plan) - n_ocultas)
    if plantilla is not None:
        desc += u"\n(se quitará la plantilla '{}')".format(plantilla.Name)
    if not confirmar(desc):
        return u"El usuario ha cancelado el cambio."
    with revit.Transaction("Revit IA: visibilidad de categorías"):
        if plantilla is not None:
            v.ViewTemplateId = ElementId.InvalidElementId
        for cat, oculta in plan:
            v.SetCategoryHidden(cat.Id, oculta)
    return u"Hecho. " + desc


def vista_objetivo(args):
    v = doc.GetElement(a_id(args["vista_id"])) if args.get("vista_id") else doc.ActiveView
    if not isinstance(v, View):
        return None
    return v


def h_configurar_vista(args):
    v = vista_objetivo(args)
    if v is None:
        return u"No existe la vista indicada."
    accion = args.get("accion")
    valor = args.get("valor")

    if accion == u"nivel_detalle":
        niveles = {u"bajo": ViewDetailLevel.Coarse, u"coarse": ViewDetailLevel.Coarse,
                   u"medio": ViewDetailLevel.Medium, u"medium": ViewDetailLevel.Medium,
                   u"alto": ViewDetailLevel.Fine, u"fine": ViewDetailLevel.Fine}
        detalle = niveles.get((valor or u"").strip().lower())
        if detalle is None:
            return u"Nivel de detalle no válido. Usa bajo, medio o alto."
        descripcion = u"Cambiar el nivel de detalle de '{}' a {}".format(v.Name, valor)
    elif accion == u"escala":
        try:
            escala = int(valor)
        except (TypeError, ValueError):
            return u"La escala debe ser un número entero positivo."
        if escala < 1 or escala > 10000:
            return u"La escala debe estar entre 1 y 10000."
        descripcion = u"Cambiar la escala de '{}' a 1:{}".format(v.Name, escala)
    elif accion == u"plantilla":
        if args.get("quitar"):
            plantilla = None
            descripcion = u"Quitar la plantilla de vista de '{}'".format(v.Name)
        else:
            plantilla = doc.GetElement(a_id(args.get("plantilla_id")))
            if not isinstance(plantilla, View) or not plantilla.IsTemplate:
                return u"No existe una plantilla de vista con ese id."
            if plantilla.Id == v.Id:
                return u"Una vista no puede aplicarse a sí misma como plantilla."
            descripcion = u"Aplicar la plantilla '{}' a la vista '{}'".format(
                plantilla.Name, v.Name)
    else:
        return u"Acción no válida. Usa nivel_detalle, escala o plantilla."

    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."
    with revit.Transaction("Revit IA: configurar vista"):
        if accion == u"nivel_detalle":
            v.DetailLevel = detalle
        elif accion == u"escala":
            v.Scale = escala
        elif plantilla is None:
            v.ViewTemplateId = ElementId.InvalidElementId
        else:
            v.ViewTemplateId = plantilla.Id
    return u"Hecho: " + descripcion


def h_visibilidad_elementos(args):
    v = vista_objetivo(args)
    if v is None:
        return u"No existe la vista indicada."
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos. Indica ids o usa seleccion=true."

    ocultar = args.get("accion") == u"ocultar"
    if args.get("accion") not in (u"ocultar", u"mostrar"):
        return u"Acción no válida. Usa ocultar o mostrar."
    validos = []
    errores = []
    for el in els:
        try:
            if ocultar:
                puede = v.CanElementBeHidden(el.Id)
                ya_esta = v.IsElementHidden(el.Id)
            else:
                ya_esta = not v.IsElementHidden(el.Id)
                puede = True
            if not puede:
                errores.append(u"id {}: no se puede ocultar en esta vista".format(el.Id))
            elif not ya_esta:
                validos.append(el)
        except Exception as ex:
            errores.append(u"id {}: {}".format(el.Id, ex))
    if not validos:
        res = u"No hay elementos que cambiar en la vista '{}'.".format(v.Name)
        if errores:
            res += u" " + u"; ".join(errores[:20])
        return res

    verbo = u"Ocultar" if ocultar else u"Mostrar"
    descripcion = u"{} {} elementos en la vista '{}'".format(verbo, len(validos), v.Name)
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    ids = List[ElementId]([el.Id for el in validos])
    with revit.Transaction("Revit IA: visibilidad de elementos"):
        if ocultar:
            v.HideElements(ids)
        else:
            v.UnhideElements(ids)
    res = u"Hecho: {}.".format(descripcion)
    if errores:
        res += u" No procesados: " + u"; ".join(errores[:20])
    return res


def h_sobrescribir_graficos(args):
    v = vista_objetivo(args)
    if v is None:
        return u"No existe la vista indicada."
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos. Indica ids o usa seleccion=true."

    color = args.get("color")
    transparencia = args.get("transparencia")
    grosor = args.get("grosor_linea")
    patron_id = args.get("patron_id")
    if color:
        try:
            rgb = [int(c) for c in color]
        except (TypeError, ValueError):
            return u"El color debe ser una lista RGB de tres enteros."
        if len(rgb) != 3 or any(c < 0 or c > 255 for c in rgb):
            return u"El color RGB debe contener tres valores entre 0 y 255."
    try:
        if transparencia is not None:
            transparencia = int(transparencia)
        if grosor is not None:
            grosor = int(grosor)
    except (TypeError, ValueError):
        return u"Transparencia y grosor de línea deben ser números enteros."
    if transparencia is not None and not 0 <= transparencia <= 100:
        return u"La transparencia debe estar entre 0 y 100."
    if grosor is not None and not 1 <= grosor <= 16:
        return u"El grosor de línea debe estar entre 1 y 16."
    if patron_id is not None:
        patron = doc.GetElement(a_id(patron_id))
        if not isinstance(patron, FillPatternElement):
            return u"No existe un patrón de relleno con id {}.".format(patron_id)
    elif color:
        solidos = [p for p in FilteredElementCollector(doc).OfClass(FillPatternElement)
                   if p.GetFillPattern().IsSolidFill]
        if not solidos:
            return u"No se encontró un patrón de relleno sólido para aplicar el color."
        patron = solidos[0]
    else:
        patron = None

    cambios = []
    if color:
        cambios.append(u"color RGB {}".format(tuple(rgb)))
    if patron is not None:
        cambios.append(u"patrón '{}'".format(patron.Name))
    if transparencia is not None:
        cambios.append(u"transparencia {}%".format(transparencia))
    if grosor is not None:
        cambios.append(u"grosor de línea {}".format(grosor))
    if not cambios:
        return u"Indica al menos un cambio de color, patrón, transparencia o grosor de línea."

    descripcion = u"Aplicar {} a {} elementos en la vista '{}'".format(
        u", ".join(cambios), len(els), v.Name)
    descripcion += u". Se reemplazarán las sobrescrituras gráficas existentes de esos elementos."
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    with revit.Transaction("Revit IA: sobrescritura gráfica"):
        for el in els:
            ogs = OverrideGraphicSettings()
            if color:
                revit_color = Color(rgb[0], rgb[1], rgb[2])
                ogs.SetProjectionLineColor(revit_color)
                ogs.SetCutLineColor(revit_color)
                ogs.SetSurfaceForegroundPatternId(patron.Id)
                ogs.SetSurfaceForegroundPatternColor(revit_color)
                ogs.SetCutForegroundPatternId(patron.Id)
                ogs.SetCutForegroundPatternColor(revit_color)
            elif patron is not None:
                ogs.SetSurfaceForegroundPatternId(patron.Id)
                ogs.SetCutForegroundPatternId(patron.Id)
            if transparencia is not None:
                ogs.SetSurfaceTransparency(int(transparencia))
            if grosor is not None:
                ogs.SetProjectionLineWeight(int(grosor))
                ogs.SetCutLineWeight(int(grosor))
            v.SetElementOverrides(el.Id, ogs)
    return u"Hecho: " + descripcion


def h_transformar_elementos(args):
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos. Indica ids, categoría o seleccion=true."
    accion = args.get("accion")
    if accion == u"mover":
        try:
            delta = XYZ(
                UnitUtils.ConvertToInternalUnits(float(args.get("dx_m") or 0), UnitTypeId.Meters),
                UnitUtils.ConvertToInternalUnits(float(args.get("dy_m") or 0), UnitTypeId.Meters),
                UnitUtils.ConvertToInternalUnits(float(args.get("dz_m") or 0), UnitTypeId.Meters))
        except (TypeError, ValueError):
            return u"Las distancias de movimiento deben ser números en metros."
        if delta.GetLength() == 0:
            return u"Indica un desplazamiento distinto de cero."
        descripcion = u"Mover {} elementos ({:.3f}, {:.3f}, {:.3f}) m".format(
            len(els), float(args.get("dx_m") or 0), float(args.get("dy_m") or 0),
            float(args.get("dz_m") or 0))
    elif accion == u"rotar":
        try:
            grados = float(args.get("grados"))
        except (TypeError, ValueError):
            return u"El ángulo debe ser un número en grados."
        try:
            centro = None
            if any(args.get(k) is not None for k in ("centro_x_m", "centro_y_m", "centro_z_m")):
                centro = XYZ(
                    UnitUtils.ConvertToInternalUnits(float(args.get("centro_x_m") or 0),
                                                     UnitTypeId.Meters),
                    UnitUtils.ConvertToInternalUnits(float(args.get("centro_y_m") or 0),
                                                     UnitTypeId.Meters),
                    UnitUtils.ConvertToInternalUnits(float(args.get("centro_z_m") or 0),
                                                     UnitTypeId.Meters))
        except (TypeError, ValueError):
            return u"Las coordenadas del centro deben ser números en metros."
        if grados == 0:
            return u"Indica un ángulo distinto de cero."
        descripcion = u"Rotar {} elementos {}°".format(len(els), grados)
        descripcion += u" alrededor del centro indicado" if centro else u" sobre su propio eje vertical"
    else:
        return u"Acción no válida. Usa mover o rotar."

    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."
    hechos = 0
    errores = []
    with revit.Transaction("Revit IA: {}".format(accion)):
        for el in els:
            try:
                if accion == u"mover":
                    ElementTransformUtils.MoveElement(doc, el.Id, delta)
                else:
                    origen = centro
                    if origen is None:
                        ubicacion = el.Location
                        if isinstance(ubicacion, LocationPoint):
                            origen = ubicacion.Point
                        elif isinstance(ubicacion, LocationCurve):
                            origen = ubicacion.Curve.Evaluate(0.5, True)
                        else:
                            caja = el.get_BoundingBox(doc.ActiveView)
                            if caja is None:
                                raise Exception(u"no tiene una ubicación ni una caja delimitadora")
                            origen = (caja.Transform.OfPoint(caja.Min)
                                      + caja.Transform.OfPoint(caja.Max)) / 2
                    eje = Line.CreateBound(origen, origen + XYZ(0, 0, 1))
                    ElementTransformUtils.RotateElement(doc, el.Id, eje, math.radians(grados))
                hechos += 1
            except Exception as ex:
                errores.append(u"id {}: {}".format(el.Id, ex))
    return con_errores(u"Procesados {} de {} elementos.".format(hechos, len(els)), errores)


def h_cambiar_cota_nivel(args):
    nivel = doc.GetElement(a_id(args.get("nivel_id")))
    if not isinstance(nivel, Level):
        return u"No existe el nivel indicado."
    try:
        cota_m = float(args.get("cota_m"))
    except (TypeError, ValueError):
        return u"La cota debe ser un número en metros."
    parametro = nivel.get_Parameter(BuiltInParameter.LEVEL_ELEV)
    if parametro is None or parametro.IsReadOnly:
        return u"La cota del nivel no se puede modificar."
    descripcion = u"Cambiar la cota de '{}' de {:.3f} m a {:.3f} m".format(
        nivel.Name,
        UnitUtils.ConvertFromInternalUnits(nivel.Elevation, UnitTypeId.Meters),
        cota_m)
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."
    with revit.Transaction("Revit IA: cambiar cota de nivel"):
        parametro.Set(UnitUtils.ConvertToInternalUnits(cota_m, UnitTypeId.Meters))
    return u"Hecho: " + descripcion


def h_etiquetar_elementos(args):
    v = vista_objetivo(args)
    if v is None or v.IsTemplate:
        return u"Indica una vista válida para colocar etiquetas."
    els = elementos_destino(args)
    if not els:
        return u"No se encontraron elementos. Indica ids o usa seleccion=true."
    if args.get("tipo_id") is not None and tipo_por_id(args["tipo_id"]) is None:
        return u"No existe el tipo de etiqueta indicado."

    descripcion = u"Crear etiquetas para {} elementos en la vista '{}'".format(len(els), v.Name)
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    hechos = 0
    errores = []
    with revit.Transaction("Revit IA: etiquetar elementos"):
        for el in els:
            try:
                caja = el.get_BoundingBox(v)
                if caja is None:
                    raise Exception(u"no tiene caja delimitadora en la vista")
                centro = caja.Transform.OfPoint((caja.Min + caja.Max) / 2)
                # Revit 2026+ ya no tiene ElementId.IntegerValue
                es_habitacion = (el.Category is not None
                                 and el.Category.BuiltInCategory == BuiltInCategory.OST_Rooms)
                if es_habitacion:
                    etiqueta = doc.Create.NewRoomTag(
                        LinkElementId(el.Id), UV(centro.X, centro.Y), v.Id)
                else:
                    etiqueta = IndependentTag.Create(
                        doc, v.Id, Reference(el), False, TagMode.TM_ADDBY_CATEGORY,
                        TagOrientation.Horizontal, centro)
                if args.get("tipo_id") is not None:
                    etiqueta.ChangeTypeId(a_id(args["tipo_id"]))
                hechos += 1
            except Exception as ex:
                errores.append(u"id {}: {}".format(el.Id, ex))
    return con_errores(u"Etiquetados {} de {} elementos.".format(hechos, len(els)), errores)


def h_cargar_familia(args):
    if doc.IsFamilyDocument:
        return u"Abre un proyecto de Revit antes de cargar una familia."
    ruta = forms.pick_file(file_ext="rfa", title="Selecciona una familia de Revit")
    if not ruta:
        return u"Carga cancelada: no se eligió ningún archivo."
    descripcion = u"Cargar la familia local '{}' en el proyecto.".format(os.path.basename(ruta))
    if not confirmar(descripcion):
        return u"El usuario ha cancelado la carga."
    # Cargar una familia modifica el documento: necesita transacción
    with revit.Transaction("Revit IA: cargar familia"):
        resultado = doc.LoadFamily(ruta)
    if resultado is False or resultado is None:
        return u"Revit no cargó la familia '{}'. Comprueba si ya estaba cargada o si el archivo es válido.".format(
            os.path.basename(ruta))
    if isinstance(resultado, Family):
        return u"Familia '{}' cargada [id {}].".format(resultado.Name, resultado.Id)
    return u"Familia '{}' cargada correctamente.".format(os.path.basename(ruta))


def h_seleccionar(args):
    ids = List[ElementId]([e.Id for e in elementos_destino(args)])
    uidoc.Selection.SetElementIds(ids)
    return u"Seleccionados {} elementos en Revit.".format(ids.Count)


PALETA = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48),
          (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212),
          (0, 128, 128), (220, 190, 255), (170, 110, 40), (255, 250, 200), (128, 0, 0),
          (170, 255, 195), (128, 128, 0), (255, 215, 180), (0, 0, 128), (128, 128, 128)]


def clave_color(el, args):
    por = args.get("por")
    if por == "workset":
        return doc.GetWorksetTable().GetWorkset(el.WorksetId).Name
    if por == "categoria":
        return el.Category.Name
    return valor_param(el, args.get("parametro")) or u"(sin valor)"


def h_crear_vista_color(args):
    if args.get("por") == "workset" and not doc.IsWorkshared:
        return u"El modelo no tiene worksets (no es colaborativo)."
    criterio = args.get("por")
    if criterio == "parametro":
        criterio = u"parámetro '{}'".format(args.get("parametro"))
    base = doc.ActiveView
    if not confirmar(u"Crear la vista '{}' (copia de '{}') coloreada por {}".format(
            args.get("nombre"), base.Name, criterio)):
        return u"El usuario ha cancelado el cambio."

    with revit.Transaction("Revit IA: vista coloreada"):
        if base.CanViewBeDuplicated(ViewDuplicateOption.Duplicate):
            nv = doc.GetElement(base.Duplicate(ViewDuplicateOption.Duplicate))
        else:
            vft = [t for t in FilteredElementCollector(doc).OfClass(ViewFamilyType)
                   if t.ViewFamily == ViewFamily.ThreeDimensional][0]
            nv = View3D.CreateIsometric(doc, vft.Id)
        # Sin plantilla, para que no bloquee los colores
        nv.ViewTemplateId = ElementId.InvalidElementId
        nombre = args.get("nombre") or u"Colores IA"
        for intento in range(1, 20):
            try:
                nv.Name = nombre if intento == 1 else u"{} ({})".format(nombre, intento)
                break
            except Exception:
                continue
        doc.Regenerate()

        solido = [f for f in FilteredElementCollector(doc).OfClass(FillPatternElement)
                  if f.GetFillPattern().IsSolidFill][0]
        grupos = {}
        for el in FilteredElementCollector(doc, nv.Id).WhereElementIsNotElementType():
            if el.Category is None or el.Category.CategoryType != CategoryType.Model:
                continue
            try:
                grupos.setdefault(clave_color(el, args), []).append(el.Id)
            except Exception:
                continue
        leyenda = []
        for i, clave in enumerate(sorted(grupos)):
            rgb = PALETA[i % len(PALETA)]
            ogs = OverrideGraphicSettings()
            ogs.SetSurfaceForegroundPatternId(solido.Id)
            ogs.SetSurfaceForegroundPatternColor(Color(*rgb))
            ogs.SetCutForegroundPatternId(solido.Id)
            ogs.SetCutForegroundPatternColor(Color(*rgb))
            for eid in grupos[clave]:
                try:
                    nv.SetElementOverrides(eid, ogs)
                except Exception:
                    pass
            leyenda.append(u"- {} ({} elementos): RGB {}".format(clave, len(grupos[clave]), rgb))
    uidoc.RequestViewChange(nv)
    return (u"Vista '{}' [id {}] creada (se abrirá en Revit). Leyenda de colores:\n{}"
            .format(nv.Name, nv.Id, u"\n".join(leyenda)))


def h_crear_vista(args):
    tipo = args.get("tipo")
    nombre = (args.get("nombre") or u"").strip()
    if not nombre:
        return u"Indica el nombre de la vista."
    tipos_vista = {"planta": ViewFamily.FloorPlan,
                   "seccion": ViewFamily.Section,
                   "3d": ViewFamily.ThreeDimensional}
    familia_vista = tipos_vista.get(tipo)
    if familia_vista is None:
        return u"Tipo no válido. Usa planta, seccion o 3d."

    nivel = None
    elementos = []
    if tipo == u"planta":
        nivel = doc.GetElement(a_id(args.get("nivel_id")))
        if not isinstance(nivel, Level):
            return u"Para crear una planta, indica un nivel_id válido."
    elif tipo == u"seccion":
        elementos = elementos_destino(args)
        if not elementos:
            return u"Para crear una sección, indica ids de elementos o seleccion=true."
        try:
            margen = float(args.get("margen_m") or 0.5)
        except (TypeError, ValueError):
            return u"El margen de sección debe ser un número en metros."
        if margen <= 0 or margen > 100:
            return u"El margen de sección debe ser mayor que 0 y no superior a 100 m."

    descripcion = u"Crear una vista de tipo '{}' llamada '{}'".format(tipo, nombre)
    if nivel is not None:
        descripcion += u" en el nivel '{}'".format(nivel.Name)
    if elementos:
        descripcion += u" alrededor de {} elementos".format(len(elementos))
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    with revit.Transaction("Revit IA: crear vista"):
        tipos = [t for t in FilteredElementCollector(doc).OfClass(ViewFamilyType)
                 if t.ViewFamily == familia_vista]
        if not tipos:
            return u"No hay un tipo de vista '{}' disponible en este proyecto.".format(tipo)
        if tipo == u"planta":
            vista = ViewPlan.Create(doc, tipos[0].Id, nivel.Id)
        elif tipo == u"3d":
            vista = View3D.CreateIsometric(doc, tipos[0].Id)
        else:
            puntos = []
            for elemento in elementos:
                caja = elemento.get_BoundingBox(doc.ActiveView)
                if caja is None:
                    caja = elemento.get_BoundingBox(None)
                if caja is None:
                    continue
                for x in (caja.Min.X, caja.Max.X):
                    for y in (caja.Min.Y, caja.Max.Y):
                        for z in (caja.Min.Z, caja.Max.Z):
                            puntos.append(caja.Transform.OfPoint(XYZ(x, y, z)))
            if not puntos:
                return u"No se pudo obtener la geometría de los elementos para encuadrar la sección."
            xs = [p.X for p in puntos]
            ys = [p.Y for p in puntos]
            zs = [p.Z for p in puntos]
            margen = UnitUtils.ConvertToInternalUnits(margen, UnitTypeId.Meters)
            ancho = max(max(xs) - min(xs), margen * 2)
            fondo = max(max(ys) - min(ys), margen * 2)
            alto = max(max(zs) - min(zs), margen * 2)
            caja_seccion = BoundingBoxXYZ()
            transformacion = Transform.Identity
            transformacion.Origin = XYZ((min(xs) + max(xs)) / 2,
                                        (min(ys) + max(ys)) / 2,
                                        (min(zs) + max(zs)) / 2)
            transformacion.BasisX = XYZ(1, 0, 0)
            transformacion.BasisY = XYZ(0, 0, 1)
            transformacion.BasisZ = XYZ(0, -1, 0)
            caja_seccion.Transform = transformacion
            caja_seccion.Min = XYZ(-ancho / 2 - margen, -alto / 2 - margen,
                                   -fondo / 2 - margen)
            caja_seccion.Max = XYZ(ancho / 2 + margen, alto / 2 + margen,
                                   fondo / 2 + margen)
            vista = ViewSection.CreateSection(doc, tipos[0].Id, caja_seccion)
        vista.Name = nombre
    uidoc.RequestViewChange(vista)
    return u"Vista '{}' [id {}] creada.".format(vista.Name, vista.Id)


def h_crear_plano(args):
    numero =(args.get("numero") or u"").strip()
    nombre = (args.get("nombre") or u"").strip()
    if not numero or not nombre:
        return u"Indica el número y el nombre del plano."
    titulo_id = ElementId.InvalidElementId
    if args.get("titulo_bloque_id") is not None:
        titulo = doc.GetElement(a_id(args["titulo_bloque_id"]))
        if (titulo is None or titulo.Category is None
                or titulo.Category.BuiltInCategory != BuiltInCategory.OST_TitleBlocks):
            return u"El id indicado no corresponde a un tipo de cuadro de rotulación."
        titulo_id = titulo.Id

    vista = None
    if args.get("vista_id") is not None:
        vista = doc.GetElement(a_id(args["vista_id"]))
        if not isinstance(vista, View) or vista.IsTemplate or isinstance(vista, ViewSheet):
            return u"La vista indicada no se puede colocar en un plano."

    try:
        x_mm = float(args.get("x_mm") or 150.0)
        y_mm = float(args.get("y_mm") or 105.0)
    except (TypeError, ValueError):
        return u"Las coordenadas para colocar la vista deben ser números en milímetros."
    if x_mm < 0 or y_mm < 0 or x_mm > 10000 or y_mm > 10000:
        return u"Las coordenadas deben estar entre 0 y 10000 mm."
    descripcion = u"Crear el plano '{}' - '{}'".format(numero, nombre)
    if vista is not None:
        descripcion += u" y colocar la vista '{}'".format(vista.Name)
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    with revit.Transaction("Revit IA: crear plano"):
        plano = ViewSheet.Create(doc, titulo_id)
        plano.SheetNumber = numero
        plano.Name = nombre
        if vista is not None:
            if not Viewport.CanAddViewToSheet(doc, plano.Id, vista.Id):
                raise Exception(u"La vista no se puede colocar en este plano.")
            punto = XYZ(UnitUtils.ConvertToInternalUnits(x_mm, UnitTypeId.Millimeters),
                        UnitUtils.ConvertToInternalUnits(y_mm, UnitTypeId.Millimeters), 0)
            Viewport.Create(doc, plano.Id, vista.Id, punto)
    uidoc.RequestViewChange(plano)
    return u"Plano '{}' [id {}] creado.".format(plano.SheetNumber, plano.Id)


def h_crear_tabla(args):
    categoria_nombre = (args.get("categoria") or u"").strip().lower()
    categoria = next((c for c in doc.Settings.Categories
                      if c.Name.lower() == categoria_nombre), None)
    # IsValidCategoryForSchedule solo recibe el id de la categoría (sin doc)
    if categoria is None or not ViewSchedule.IsValidCategoryForSchedule(categoria.Id):
        return u"La categoría indicada no permite crear una tabla de planificación."
    nombre = (args.get("nombre") or u"").strip()
    campos_pedidos = args.get("campos") or []
    if not nombre or not campos_pedidos:
        return u"Indica el nombre de la tabla y al menos un campo."

    descripcion = u"Crear la tabla '{}' para '{}' con los campos {}".format(
        nombre, categoria.Name, u", ".join(campos_pedidos))
    if args.get("filtros"):
        descripcion += u" y {} filtros".format(len(args["filtros"]))
    if not confirmar(descripcion):
        return u"El usuario ha cancelado el cambio."

    try:
        with revit.Transaction("Revit IA: crear tabla"):
            tabla = ViewSchedule.CreateSchedule(doc, categoria.Id)
            tabla.Name = nombre
            definicion = tabla.Definition
            disponibles = list(definicion.GetSchedulableFields())
            por_nombre = {}
            for campo in disponibles:
                por_nombre.setdefault(campo.GetName(doc).strip().lower(), campo)
            campos = []
            for pedido in campos_pedidos:
                clave = pedido.strip().lower()
                if clave not in por_nombre:
                    # Devolvemos los campos válidos para que ChatGPT corrija y reintente
                    raise Exception(u"No existe el campo '{}' en la categoría '{}'. Campos "
                                    u"disponibles: {}. Vuelve a intentarlo con nombres exactos de "
                                    u"esta lista.".format(pedido, categoria.Name,
                                                          u", ".join(sorted(por_nombre))))
                campos.append(definicion.AddField(por_nombre[clave]))

            campos_por_nombre = {}
            for campo in campos:
                try:
                    campos_por_nombre[campo.GetName().strip().lower()] = campo
                except Exception:
                    pass

            for filtro in args.get("filtros") or []:
                nombre_campo = (filtro.get("campo") or u"").strip().lower()
                campo = campos_por_nombre.get(nombre_campo)
                if campo is None:
                    raise Exception(u"El campo de filtro '{}' debe estar incluido en campos."
                                    .format(filtro.get("campo")))
                operadores = {u"igual": ScheduleFilterType.Equal,
                              u"distinto": ScheduleFilterType.NotEqual,
                              u"contiene": ScheduleFilterType.Contains,
                              u"no_contiene": ScheduleFilterType.NotContains}
                operador = operadores.get((filtro.get("operador") or u"igual").lower())
                if operador is None:
                    raise Exception(u"Operador de filtro no válido; usa igual, distinto, contiene o no_contiene.")
                definicion.AddFilter(ScheduleFilter(
                    campo.FieldId, operador, u"{}".format(filtro.get("valor", u""))))

            grupos = [n.strip().lower() for n in args.get("agrupar_por") or []]
            ordenados = [n.strip().lower() for n in args.get("ordenar_por") or []]
            nombres_orden = []
            for n in grupos + ordenados:
                if n not in nombres_orden:
                    nombres_orden.append(n)
            orden = (ScheduleSortOrder.Descending if args.get("orden") == u"descendente"
                     else ScheduleSortOrder.Ascending)
            for nombre_campo in nombres_orden:
                campo = campos_por_nombre.get(nombre_campo)
                if campo is None:
                    raise Exception(u"El campo de ordenación/agrupación '{}' debe estar incluido en campos."
                                    .format(nombre_campo))
                grupo = ScheduleSortGroupField(campo.FieldId, orden)
                if nombre_campo in grupos:
                    grupo.ShowHeader = True
                definicion.AddSortGroupField(grupo)
    except Exception as ex:
        return u"No se pudo crear la tabla; no se han conservado cambios parciales. {}".format(ex)

    uidoc.RequestViewChange(tabla)
    return u"Tabla '{}' [id {}] creada con {} campos.".format(
        tabla.Name, tabla.Id, len(campos_pedidos))


def h_exportar(args):
    formato =(args.get("formato") or u"").strip().lower()
    vista = doc.GetElement(a_id(args.get("vista_id"))) if args.get("vista_id") else doc.ActiveView
    if not isinstance(vista, View) or vista.IsTemplate:
        return u"Indica una vista o tabla válida para exportar."
    formatos = (u"png", u"jpg", u"bmp", u"tiff", u"pdf", u"csv")
    if formato not in formatos:
        return u"Formato no válido. Usa png, jpg, bmp, tiff, pdf o csv."
    if formato == u"csv" and not isinstance(vista, ViewSchedule):
        return u"CSV solo se puede exportar desde una tabla de planificación."

    carpeta = forms.pick_folder()
    if not carpeta:
        return u"Exportación cancelada: no se eligió una carpeta."
    base = re.sub(r'[<>:"/\\|?*]', u"_", args.get("nombre") or vista.Name).strip().rstrip(u" .")
    if not base:
        return u"El nombre de archivo no es válido."
    extension = {u"png": u".png", u"jpg": u".jpg", u"bmp": u".bmp",
                 u"tiff": u".tif", u"pdf": u".pdf", u"csv": u".csv"}[formato]
    destino = os.path.join(carpeta, base + extension)
    descripcion = u"Exportar '{}' como {} a '{}'".format(vista.Name, formato.upper(), destino)
    if not confirmar(descripcion):
        return u"El usuario ha cancelado la exportación."

    if formato == u"csv":
        filas = []
        for seccion in (SectionType.Header, SectionType.Body):
            datos = vista.GetTableData().GetSectionData(seccion)
            for fila in range(datos.FirstRowNumber, datos.LastRowNumber + 1):
                valores = []
                for columna in range(datos.FirstColumnNumber, datos.LastColumnNumber + 1):
                    texto = vista.GetCellText(seccion, fila, columna) or u""
                    texto = texto.replace(u'"', u'""')
                    if u"," in texto or u'"' in texto or u"\n" in texto or u"\r" in texto:
                        texto = u'"' + texto + u'"'
                    valores.append(texto)
                filas.append(u",".join(valores))
        File.WriteAllText(destino, u"\r\n".join(filas), Encoding.UTF8)
    elif formato == u"pdf":
        opciones = PDFExportOptions()
        opciones.Combine = True
        opciones.FileName = base
        ids = List[ElementId]([vista.Id])
        if not doc.Export(carpeta, ids, opciones):
            return u"Revit no pudo exportar la vista a PDF."
    else:
        tipo_imagen = {u"png": ImageFileType.PNG, u"jpg": ImageFileType.JPEG,
                       u"bmp": ImageFileType.BMP, u"tiff": ImageFileType.TIFF}[formato]
        opciones = ImageExportOptions()
        opciones.ExportRange = ExportRange.SetOfViews
        opciones.FilePath = os.path.join(carpeta, base + extension)
        opciones.ZoomType = ZoomType.FitToPage
        opciones.FitDirection = FitDirectionType.Horizontal
        opciones.ImageResolution = ImageResolution.DPI_300
        opciones.HLRandWFViewsFileType = tipo_imagen
        opciones.ShadowViewsFileType = tipo_imagen
        vistas = ViewSet()
        vistas.Insert(vista)
        opciones.SetViewsAndSheets(vistas)
        doc.ExportImage(opciones)
        # Al exportar un conjunto de vistas, Revit puede añadir el nombre de la vista al archivo
        return u"Exportación completada en la carpeta '{}' (archivo que empieza por '{}').".format(
            carpeta, base)
    return u"Exportación completada: {}".format(destino)


HERRAMIENTAS = {
    "resumen_modelo": h_resumen_modelo,
    "listar_recursos": h_listar_recursos,
    "listar_parametros": h_listar_parametros,
    "buscar_elementos": h_buscar_elementos,
    "ver_elemento": h_ver_elemento,
    "ver_seleccion": h_ver_seleccion,
    "cambiar_parametro": h_cambiar_parametro,
    "renombrar": h_renombrar,
    "duplicar_tipo": h_duplicar_tipo,
    "cambiar_tipo": h_cambiar_tipo,
    "cambiar_parametro_tipo": h_cambiar_parametro_tipo,
    "cambiar_workset": h_cambiar_workset,
    "visibilidad_categorias": h_visibilidad_categorias,
    "configurar_vista": h_configurar_vista,
    "visibilidad_elementos": h_visibilidad_elementos,
    "sobrescribir_graficos": h_sobrescribir_graficos,
    "transformar_elementos": h_transformar_elementos,
    "cambiar_cota_nivel": h_cambiar_cota_nivel,
    "etiquetar_elementos": h_etiquetar_elementos,
    "cargar_familia": h_cargar_familia,
    "seleccionar": h_seleccionar,
    "crear_vista_color": h_crear_vista_color,
    "crear_vista": h_crear_vista,
    "crear_plano": h_crear_plano,
    "crear_tabla": h_crear_tabla,
    "exportar": h_exportar,
}


# ================================================================ Definición para OpenAI

def fn(nombre, descripcion, props, requeridos=None):
    return {"type": "function", "function": {
        "name": nombre, "description": descripcion,
        "parameters": {"type": "object", "properties": props, "required": requeridos or []}}}


TXT = {"type": "string"}
IDS = {"type": "array", "items": {"type": "integer"}, "description": u"Ids de elementos"}
CAT = {"type": "string", "description": u"Nombre exacto de la categoría, p. ej. 'Muros'"}

TOOLS = [
    fn("resumen_modelo", u"Resumen general del modelo abierto: vistas, planos, niveles, categorías "
       u"con los tipos usados y cantidades, worksets. Úsala primero si aún no conoces el modelo.", {}),
    fn("listar_recursos", u"Lista de forma paginada recursos del modelo: vistas, plantillas de vista, "
       u"niveles con cota, habitaciones con área, tablas de planificación con sus campos, patrones "
       u"de relleno/línea, familias o tipos. Usa el tipo exacto indicado.",
       {"tipo": {"type": "string", "enum": ["vistas", "plantillas", "niveles", "habitaciones",
                                              "tablas", "patrones", "familias", "tipos"]},
        "limite": {"type": "integer", "description": u"Máximo de resultados (por defecto 200, máximo 500)"}},
       ["tipo"]),
    fn("listar_parametros", u"Nombres de los parámetros de ejemplar y de tipo de una categoría, "
       u"con un valor de ejemplo. Úsala para saber el nombre exacto de un parámetro.",
       {"categoria": CAT}, ["categoria"]),
    fn("buscar_elementos", u"Lista elementos de una categoría (id, tipo, nivel, workset) con los "
       u"valores de los parámetros pedidos (de ejemplar o de tipo). Puede filtrar por familia, "
       u"tipo, nivel o el valor de un parámetro; para filtrar por workset usa "
       u"filtro_parametro='workset'.",
       {"categoria": CAT,
        "parametros": {"type": "array", "items": TXT, "description": u"Parámetros a devolver"},
        "familia": {"type": "string", "description": u"Filtrar por texto contenido en el nombre de familia"},
        "tipo": {"type": "string", "description": u"Filtrar por texto contenido en el nombre del tipo"},
        "nivel": {"type": "string", "description": u"Filtrar por texto contenido en el nombre del nivel"},
        "filtro_parametro": {"type": "string", "description": u"Parámetro por el que filtrar"},
        "filtro_valor": {"type": "string", "description": u"Texto que debe contener su valor"},
        "limite": {"type": "integer", "description": u"Máximo de elementos (por defecto 200, máximo 500)"}},
       ["categoria"]),
    fn("ver_elemento", u"Todos los parámetros (de ejemplar y de tipo) de un elemento.",
       {"id": {"type": "integer"}}, ["id"]),
    fn("ver_seleccion", u"Elementos seleccionados ahora en Revit con sus parámetros.", {}),
    fn("cambiar_parametro", u"Cambia un parámetro de EJEMPLAR en varios elementos. El usuario "
       u"confirma antes. Indica UNO de: ids, categoria o seleccion=true. Valores con unidades "
       u"como '3.50 m'; Sí/No como 'sí' o 'no'.",
       {"parametro": TXT, "valor": TXT, "ids": IDS, "categoria": CAT,
        "seleccion": {"type": "boolean"}}, ["parametro", "valor"]),
    fn("renombrar", u"Renombra cualquier elemento con nombre: vista, plano, nivel, TIPO (usa su "
       u"tipo id) o familia cargable. En planos, 'numero' cambia el número. Las familias de "
       u"sistema (p. ej. 'Muro básico') no se pueden renombrar. El usuario confirma antes.",
       {"id": {"type": "integer"}, "nombre": TXT, "numero": TXT}, ["id", "nombre"]),
    fn("duplicar_tipo", u"Duplica un tipo (muro, suelo, puerta, pilar...) con un nombre nuevo. "
       u"Devuelve el tipo id del tipo nuevo. El usuario confirma antes.",
       {"tipo_id": {"type": "integer"}, "nuevo_nombre": TXT}, ["tipo_id", "nuevo_nombre"]),
    fn("cambiar_tipo", u"Cambia el tipo de varios elementos a otro tipo de la misma categoría. "
       u"Indica tipo_id y UNO de: ids, categoria o seleccion=true. El usuario confirma antes.",
       {"tipo_id": {"type": "integer"}, "ids": IDS, "categoria": CAT,
        "seleccion": {"type": "boolean"}}, ["tipo_id"]),
    fn("cambiar_parametro_tipo", u"Cambia un parámetro de TIPO (afecta a todos los elementos de "
       u"ese tipo). El usuario confirma antes.",
       {"tipo_id": {"type": "integer"}, "parametro": TXT, "valor": TXT},
       ["tipo_id", "parametro", "valor"]),
    fn("cambiar_workset", u"Mueve elementos a otro workset (por su nombre). Indica UNO de: ids, "
       u"categoria o seleccion=true. El usuario confirma antes.",
       {"workset": TXT, "ids": IDS, "categoria": CAT, "seleccion": {"type": "boolean"}},
       ["workset"]),
    fn("visibilidad_categorias", u"Muestra u oculta categorías en una vista (como Visibilidad/"
       u"Gráficos). 'mostrar_solo' oculta todas las demás categorías de modelo. Si la vista tiene "
       u"plantilla, primero pregunta al usuario y luego usa quitar_plantilla=true. Sin vista_id usa "
       u"la vista activa. El usuario confirma antes.",
       {"vista_id": {"type": "integer"},
        "mostrar_solo": {"type": "array", "items": TXT, "description": u"Categorías a dejar visibles"},
        "ocultar": {"type": "array", "items": TXT},
        "mostrar": {"type": "array", "items": TXT},
        "ocultar_anotaciones": {"type": "boolean", "description": u"Con mostrar_solo, oculta también las anotaciones"},
        "quitar_plantilla": {"type": "boolean"}}),
    fn("configurar_vista", u"Cambia el nivel de detalle, escala o plantilla de una vista. Si no se "
       u"indica vista_id, usa la vista activa. Para quitar la plantilla, usa accion=plantilla y "
       u"quitar=true. El usuario confirma antes.",
       {"accion": {"type": "string", "enum": ["nivel_detalle", "escala", "plantilla"]},
        "vista_id": {"type": "integer"},
        "valor": {"type": "string", "description": u"bajo/medio/alto para nivel_detalle; número para escala"},
        "plantilla_id": {"type": "integer"},
        "quitar": {"type": "boolean"}},
       ["accion"]),
    fn("visibilidad_elementos", u"Oculta o muestra elementos concretos en una vista. Indica ids "
       u"o seleccion=true. Sin vista_id usa la vista activa. El usuario confirma antes.",
       {"accion": {"type": "string", "enum": ["ocultar", "mostrar"]},
        "vista_id": {"type": "integer"}, "ids": IDS,
        "seleccion": {"type": "boolean"}}, ["accion"]),
    fn("sobrescribir_graficos", u"Aplica color RGB, patrón de relleno, transparencia y/o grosor "
       u"de línea a elementos concretos en una vista. Indica ids o seleccion=true. Un color RGB "
       u"requiere tres enteros 0-255. El usuario confirma antes.",
       {"vista_id": {"type": "integer"}, "ids": IDS, "seleccion": {"type": "boolean"},
        "color": {"type": "array", "items": {"type": "integer"},
                  "description": u"Tres enteros RGB entre 0 y 255"},
        "patron_id": {"type": "integer", "description": u"Id de patrón de relleno"},
        "transparencia": {"type": "integer", "description": u"0-100"},
        "grosor_linea": {"type": "integer", "description": u"1-16"}}),
    fn("crear_vista", u"Crea una vista de planta en un nivel, una vista 3D isométrica o una "
       u"sección encuadrada alrededor de elementos seleccionados/indicados por id. Para planta, "
       u"indica nivel_id; para sección, ids o seleccion=true. El usuario confirma antes.",
       {"tipo": {"type": "string", "enum": ["planta", "seccion", "3d"]},
        "nombre": TXT, "nivel_id": {"type": "integer"}, "ids": IDS,
        "seleccion": {"type": "boolean"},
        "margen_m": {"type": "number", "description": u"Margen alrededor de la sección en metros"}},
       ["tipo", "nombre"]),
    fn("crear_plano", u"Crea un plano con número y nombre y opcionalmente coloca una vista en "
       u"las coordenadas de hoja x_mm/y_mm (por defecto 150/105 mm). titulo_bloque_id es opcional; "
       u"usa listar_recursos o consulta los tipos antes. El usuario confirma antes.",
       {"numero": TXT, "nombre": TXT, "titulo_bloque_id": {"type": "integer"},
        "vista_id": {"type": "integer"}, "x_mm": {"type": "number"}, "y_mm": {"type": "number"}},
       ["numero", "nombre"]),
    fn("crear_tabla", u"Crea una tabla de planificación para una categoría y configura campos, "
       u"filtros de texto, agrupación y orden. Los nombres de campos deben coincidir con los que "
       u"devuelve listar_recursos(tipo='tablas') para una tabla existente o listar_parametros para "
       u"la categoría. Solo se admiten campos de filtro/orden incluidos en campos. El usuario confirma.",
       {"categoria": CAT, "nombre": TXT,
        "campos": {"type": "array", "items": TXT},
        "filtros": {"type": "array", "items": {"type": "object", "properties": {
            "campo": TXT, "operador": {"type": "string",
                                       "enum": ["igual", "distinto", "contiene", "no_contiene"]},
            "valor": TXT}, "required": ["campo", "valor"]}},
        "agrupar_por": {"type": "array", "items": TXT},
        "ordenar_por": {"type": "array", "items": TXT},
        "orden": {"type": "string", "enum": ["ascendente", "descendente"]}},
       ["categoria", "nombre", "campos"]),
    fn("exportar", u"Exporta una vista o plano a PNG, JPG, BMP, TIFF o PDF. Para CSV, indica una "
       u"tabla de planificación. El usuario elige la carpeta y confirma el destino.",
       {"vista_id": {"type": "integer"},
        "formato": {"type": "string", "enum": ["png", "jpg", "bmp", "tiff", "pdf", "csv"]},
        "nombre": {"type": "string", "description": u"Nombre del archivo sin extensión"}},
       ["formato"]),
    fn("transformar_elementos", u"Mueve o rota elementos. Para mover, define dx_m/dy_m/dz_m. "
       u"Para rotar, define grados; si no se indica un centro en metros, cada elemento rota sobre "
       u"su propio eje vertical. Indica ids, categoría o seleccion=true. El usuario confirma antes.",
       {"accion": {"type": "string", "enum": ["mover", "rotar"]},
        "ids": IDS, "categoria": CAT, "seleccion": {"type": "boolean"},
        "dx_m": {"type": "number"}, "dy_m": {"type": "number"}, "dz_m": {"type": "number"},
        "grados": {"type": "number"}, "centro_x_m": {"type": "number"},
        "centro_y_m": {"type": "number"}, "centro_z_m": {"type": "number"}},
       ["accion"]),
    fn("cambiar_cota_nivel", u"Cambia la elevación de un nivel. Indica la cota objetivo en metros. "
       u"El usuario confirma antes.",
       {"nivel_id": {"type": "integer"}, "cota_m": {"type": "number"}},
       ["nivel_id", "cota_m"]),
    fn("etiquetar_elementos", u"Coloca etiquetas para elementos seleccionados o indicados por id "
       u"en una vista. Las habitaciones usan etiquetas de habitación; otros elementos usan "
       u"etiquetado por categoría. Se puede indicar tipo_id para cambiar el tipo de etiqueta. "
       u"Sin vista_id usa la vista activa. El usuario confirma antes.",
       {"vista_id": {"type": "integer"}, "ids": IDS, "seleccion": {"type": "boolean"},
        "tipo_id": {"type": "integer"}}),
    fn("cargar_familia", u"Abre el selector de archivos para elegir una familia .rfa local y "
       u"cargarla en el proyecto. El usuario revisa la ruta y confirma antes; no se ejecuta ni se "
       u"importa código.",
       {}),
    fn("seleccionar", u"Selecciona elementos en Revit. Indica ids, categoria o seleccion=true.",
       {"ids": IDS, "categoria": CAT}),
    fn("crear_vista_color", u"Crea una vista nueva (copia de la vista activa) con cada elemento "
       u"coloreado según su workset, su categoría o el valor de un parámetro. El usuario confirma antes.",
       {"nombre": TXT, "por": {"type": "string", "enum": ["workset", "categoria", "parametro"]},
        "parametro": {"type": "string", "description": u"Solo si por=parametro"}},
       ["nombre", "por"]),
]

SISTEMA = (u"Eres un asistente experto en Revit y BIM integrado en Revit, conversando con el usuario. "
           u"No ves el modelo directamente: usa las herramientas para consultar los datos que necesites "
           u"ANTES de responder, y no inventes datos. Si aún no conoces el modelo, empieza por "
           u"resumen_modelo. Usa listar_recursos para consultar vistas, plantillas, habitaciones, "
           u"planificaciones, patrones, familias y tipos. Usa exportar para guardar vistas, planos o tablas. "
           u"Usa listar_parametros para conocer los nombres exactos de los parámetros "
           u"(Revit puede estar en español). Responde en español, claro y breve, con datos concretos. "
           u"Escribe en texto plano, sin markdown (usa guiones para las listas). Para modificar el "
           u"modelo usa solo las herramientas disponibles: cada cambio pide confirmación. Nunca generes "
           u"ni ejecutes código Python arbitrario en el equipo. Nunca digas que "
           u"has hecho un cambio si la herramienta no lo confirma. Los tipos aparecen como "
           u"'Familia: Tipo [tipo id N]': usa ese id con las herramientas de tipos. Si algo no se "
           u"puede hacer con tus herramientas, di simplemente que esa acción aún no está "
           u"disponible en este botón (no hables de permisos). Si una herramienta devuelve un "
           u"error, léelo: si indica cómo corregirlo (nombres válidos, ids...), corrige y vuelve a "
           u"intentarlo. Si aun así falla, explica al usuario el error concreto; nunca digas solo "
           u"'limitación técnica'.")


def compactar(mensajes):
    # Recorta los resultados de herramientas de turnos anteriores para gastar menos tokens.
    # Los del turno actual (desde el último mensaje del usuario) se envían completos.
    ultimo_usuario = max(i for i, m in enumerate(mensajes) if m["role"] in ("user", "system"))
    res = []
    for i, m in enumerate(mensajes):
        if i < ultimo_usuario and m["role"] == "tool" and len(m["content"]) > MAX_TEXTO_ANTIGUO:
            m = dict(m, content=m["content"][:MAX_TEXTO_ANTIGUO] + u"\n[... recortado ...]")
        res.append(m)
    return res


def segundos_espera(detalle):
    # OpenAI indica cuánto esperar: "Please try again in 2.58s" o "in 450ms"
    m = re.search(r"try again in ([\d.]+)\s*(ms|s)", detalle or u"")
    if not m:
        return 5.0
    valor = float(m.group(1))
    return (valor / 1000.0 if m.group(2) == "ms" else valor) + 0.5


def llamar_api(mensajes, modelo, esperar=None):
    # esperar(segundos, intento): callback para mostrar la espera sin congelar la ventana
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    body = a_json({"model": modelo, "messages": compactar(mensajes), "tools": TOOLS})
    for intento in range(1, REINTENTOS + 2):
        wc = WebClient()
        wc.Encoding = Encoding.UTF8
        wc.Headers.Add("Content-Type", "application/json")
        wc.Headers.Add("Authorization", "Bearer " + API_KEY)
        try:
            resp = wc.UploadString(URL, "POST", body)
            return leer_json(resp)["choices"][0]["message"]
        except WebException as e:
            detalle = u""
            codigo = None
            if e.Response is not None:
                codigo = int(e.Response.StatusCode)
                detalle = StreamReader(e.Response.GetResponseStream()).ReadToEnd()
            # 429 por límite de velocidad: esperar y reintentar (sin saldo no tiene sentido)
            if codigo == 429 and u"insufficient_quota" not in detalle and intento <= REINTENTOS:
                segundos = segundos_espera(detalle) * intento
                if esperar:
                    esperar(segundos, intento)
                else:
                    time.sleep(segundos)
                continue
            if codigo == 429 and u"insufficient_quota" in detalle:
                raise Exception(u"Sin saldo en OpenAI. Añade créditos en platform.openai.com "
                                u"(Settings > Billing).")
            raise Exception(u"Error al llamar a la API: {}\n{}".format(e.Message, detalle))


# ================================================================ Ventana de chat

XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Revit IA" Width="780" Height="680" MinWidth="620" MinHeight="400"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResizeWithGrip" ShowInTaskbar="False">
  <Grid Margin="10">
    <Grid.RowDefinitions>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <Border Grid.Row="0" BorderBrush="#DDDDDD" BorderThickness="1" CornerRadius="4">
      <ScrollViewer x:Name="scroll" VerticalScrollBarVisibility="Auto">
        <StackPanel x:Name="panel" Margin="6"/>
      </ScrollViewer>
    </Border>
    <TextBlock Grid.Row="1" x:Name="lbl_estado" Foreground="Gray" Margin="2,6,0,4"/>
    <TextBox Grid.Row="2" x:Name="caja" Height="90" AcceptsReturn="True" TextWrapping="Wrap"
             VerticalScrollBarVisibility="Auto" FontSize="14" Padding="4"/>
    <DockPanel Grid.Row="3" Margin="0,8,0,0" LastChildFill="False">
      <Button DockPanel.Dock="Left" x:Name="btn_nueva" Content="Nueva conversación" Padding="10,4"/>
      <TextBlock DockPanel.Dock="Left" Margin="12,0" VerticalAlignment="Center" Foreground="Gray"
                 Text="Ctrl+Enter: enviar"/>
      <Button DockPanel.Dock="Right" x:Name="btn_enviar" Content="Enviar" Width="100" Height="28"/>
      <ComboBox DockPanel.Dock="Right" x:Name="cmb_modelo" Width="260" Height="28" Margin="0,0,8,0"
                VerticalContentAlignment="Center" ToolTip="Modelo de ChatGPT que responde"/>
      <TextBlock DockPanel.Dock="Right" Text="Modelo:" VerticalAlignment="Center" Margin="0,0,6,0"/>
    </DockPanel>
  </Grid>
</Window>"""

COLORES = {"usuario": "#DCEBFF", "ia": "#F1F1F1"}


class Chat(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.btn_enviar.Click += self.enviar
        self.btn_nueva.Click += self.nueva
        self.caja.PreviewKeyDown += self.tecla
        self.Loaded += lambda s, e: self.caja.Focus()
        self.cargar_modelos()
        self.nueva(None, None)

    def cargar_modelos(self):
        # Rellena el desplegable y selecciona el último modelo usado (guardado en la config)
        guardado = cfg.get_option("modelo", MODELO_POR_DEFECTO)
        lista = list(MODELOS)
        if guardado not in [m for m, _ in lista]:
            lista.append((guardado, u"personalizado"))
        for modelo, descripcion in lista:
            item = ComboBoxItem()
            item.Content = u"{}  ·  {}".format(modelo, descripcion)
            item.Tag = modelo
            self.cmb_modelo.Items.Add(item)
            if modelo == guardado:
                self.cmb_modelo.SelectedItem = item
        self.cmb_modelo.SelectionChanged += self.cambiar_modelo

    def modelo_actual(self):
        item = self.cmb_modelo.SelectedItem
        return item.Tag if item is not None else MODELO_POR_DEFECTO

    def cambiar_modelo(self, sender, args):
        cfg.modelo = self.modelo_actual()
        script.save_config()
        self.burbuja(u"Modelo cambiado a {}.".format(self.modelo_actual()), "nota")

    def nueva(self, sender, args):
        self.mensajes = [{"role": "system", "content": SISTEMA}]
        self.panel.Children.Clear()
        self.burbuja(u"Hola. Pregúntame lo que quieras sobre el modelo '{}' o pídeme cambios. "
                     u"Te pediré confirmación antes de modificar nada.".format(doc.Title), "ia")

    def burbuja(self, texto, tipo):
        tb = TextBox()
        tb.Text = texto
        tb.IsReadOnly = True
        tb.TextWrapping = TextWrapping.Wrap
        tb.BorderThickness = Thickness(0)
        tb.Background = Brushes.Transparent
        tb.Foreground = Brushes.Black
        tb.FontSize = 14
        b = Border()
        b.Child = tb
        b.CornerRadius = CornerRadius(8)
        b.Padding = Thickness(10, 6, 10, 6)
        b.Margin = Thickness(4)
        b.MaxWidth = 560
        if tipo == "nota":
            tb.Foreground = Brushes.Gray
            tb.FontSize = 12
            tb.FontStyle = FontStyles.Italic
            b.HorizontalAlignment = HorizontalAlignment.Left
            b.Padding = Thickness(10, 0, 10, 0)
        else:
            b.Background = BrushConverter().ConvertFromString(COLORES[tipo])
            b.HorizontalAlignment = (HorizontalAlignment.Right if tipo == "usuario"
                                     else HorizontalAlignment.Left)
        self.panel.Children.Add(b)
        self.scroll.ScrollToEnd()
        self.refrescar()

    def refrescar(self):
        # Deja que la ventana se repinte antes de las operaciones largas
        self.Dispatcher.Invoke(Action(lambda: None), DispatcherPriority.Background)

    def estado(self, texto):
        self.lbl_estado.Text = texto
        self.refrescar()

    def tecla(self, sender, args):
        if args.Key == Key.Return and Keyboard.Modifiers == ModifierKeys.Control:
            args.Handled = True
            self.enviar(None, None)

    def enviar(self, sender, args):
        texto = self.caja.Text.strip()
        if not texto or not self.btn_enviar.IsEnabled:
            return
        self.caja.Text = u""
        self.burbuja(texto, "usuario")
        self.mensajes.append({"role": "user", "content": texto})
        self.btn_enviar.IsEnabled = False
        self.btn_nueva.IsEnabled = False
        try:
            self.responder()
        except Exception as ex:
            self.burbuja(u"Error: {}".format(ex), "nota")
        finally:
            self.estado(u"")
            self.btn_enviar.IsEnabled = True
            self.btn_nueva.IsEnabled = True
            self.caja.Focus()

    def esperar_limite(self, segundos, intento):
        # Espera en trozos cortos para que la ventana siga repintándose
        fin = time.time() + segundos
        while time.time() < fin:
            self.estado(u"Límite de velocidad de OpenAI: reintentando en {:.0f} s (intento {} de {})..."
                        .format(fin - time.time() + 0.5, intento, REINTENTOS))
            time.sleep(0.2)

    def responder(self):
        # Bucle: ChatGPT puede pedir varias consultas antes de contestar
        for _ in range(MAX_CONSULTAS):
            self.estado(u"Pensando...")
            msg = llamar_api(self.mensajes, self.modelo_actual(), self.esperar_limite)
            llamadas = msg.get("tool_calls")
            if not llamadas:
                contenido = msg.get("content") or u""
                self.mensajes.append({"role": "assistant", "content": contenido})
                self.burbuja(contenido, "ia")
                return
            self.mensajes.append({"role": "assistant", "content": msg.get("content"),
                                  "tool_calls": llamadas})
            for ll in llamadas:
                nombre = ll["function"]["name"]
                try:
                    argumentos = leer_json(ll["function"].get("arguments") or u"{}") or {}
                except Exception:
                    argumentos = {}
                detalle = u", ".join(u"{}={}".format(k, v) for k, v in sorted(argumentos.items()))
                self.burbuja(u"> {}({})".format(nombre, detalle), "nota")
                self.estado(u"Consultando el modelo...")
                funcion = HERRAMIENTAS.get(nombre)
                try:
                    resultado = funcion(argumentos) if funcion else u"Herramienta desconocida."
                except Exception as ex:
                    resultado = u"Error: {}".format(ex)
                self.mensajes.append({"role": "tool", "tool_call_id": ll["id"],
                                      "content": resultado[:MAX_TEXTO]})
                # Primera línea del resultado, para que el usuario vea qué ha pasado
                primera = (resultado.strip().splitlines() or [u""])[0]
                self.burbuja(u"   = " + (primera[:250] + u"..." if len(primera) > 250 else primera),
                             "nota")
        self.burbuja(u"He parado: demasiadas consultas seguidas. Prueba a concretar más la pregunta.",
                     "nota")


Chat().show_dialog()
