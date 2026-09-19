# Migración UI · Pico CSS + tokens Índigo → Bootstrap 5.3.3 Navy/Dorado

**Build:** 20260918-BUILD008 · **Sistema:** Auditoría Electoral Elecciones 2026 · **Autor:** UI Migración Team

---

## 1. Resumen y Restricciones (AC-01)

### Objetivo
Sustituir íntegramente el stack de diseño basado en Pico CSS v2 + Modern Normalize + ~3.000 líneas de tokens CSS Índigo/Violeta propios, por una capa de presentación basada en **Bootstrap 5.3.3 (CDN jsdelivr)** con SRI + sobrescrituras mínimas de variables CSS a la paleta corporativa obligatoria: **Navy `#0F3A7D` + Dorado `#D4AF37`**.

### Restricción HARD · 0 modificaciones Python
Ningún archivo con extensión `.py` fue modificado durante esta migración. Se preservan 100%:
- Capa de negocio: `core/views.py`, `core/models.py`, `core/models_legacy.py`
- Pipeline IA: `core/tasks.py`, `core/ai.py`, `core/pipeline_images.py`
- Autenticación legacy: `core/auth_backend.py`
- Context processors: `core/context_processors.py` (incluye `APP_BUILD` cache-busting)
- Templatetags: `core/templatetags/confidence_tags.py`
- URLs, Forms, Validators, Exporters, Serializers, Admin, Signals, Crypto
- Migraciones (0001 → 0004) y management commands

**Corolario:** Todas las variables de contexto Django (`nav_active`, `badge_counts.*`, `config`, `stats`, `kpi.*`, `totales_ambito.*`, `por_tipo.*`, `ambitos_grouped`, `review_item`, `review_save_api_url`, `form.*`, `ai_providers.*`, `api_key_len.*`, `active_provider_exists`, `GEMINI_MODEL_CHOICES`, `DEEPSEEK_MODEL_CHOICES`, `page_obj`, `is_super`) son consumidas exactamente igual por las nuevas plantillas.

---

## 2. Inventario de Archivos Modificados

### 2.1 Plantillas Django (14 archivos · `core/templates/`)
| Ruta | Líneas antes | Líneas después | Cambio clave |
|------|-------------:|---------------:|--------------|
| `base.html` | 380 | 430 | Full rewrite. Bootstrap 5.3.3 CSS/JS SRI, overrides variables Navy/Dorado inline `<head>`, sprite SVG ~40 símbolos `#i-*` intacto, skip-link, topbar único + sidebar único, breadcrumbs Bootstrap, widget auto-batch htmx+CSRF, botones Procesar Lote e IA. |
| `core/_sidebar.html` | 210 | 260 | Refactor a `aside#sidebarOffcanvas.offcanvas.offcanvas-lg.offcanvas-start.position-lg-fixed.bg-primary` 272px. Single-source HTML para desktop+mobile. 2 grupos nav (Principal / Gestión) con badges `badge_counts.*`. Avatar footer. Condicional `{% if is_super %}`. |
| `registration/login.html` | 190 + `<style>` 24L | 220 | `body_class=sidebar-noop`. Grid `.row.g-0` 2 columnas (hero Navy gradient · card formulario 470px). Alerts Bootstrap danger/warning/info. Input-group icons SVG. Toggle-password inline JS preservado. |
| `core/dashboard.html` | 410 | 450 | Page-head + flash-row. Grid 4 KPI cards gradientes. 2 cards chart canvas IDs intactos. 4 scope-cards ámbitos. Tabla últimas 15 responsive `.table-pro`. JS inline Chart.js intacto. |
| `core/actas_list.html` | 320 | 310 | Eliminados bloques shell duplicados. Page-head. Filter-card row g-3. Grid actas `row-cols-xxl-3`. Cada card border-left 4px estado. Paginación `pagination-pro`. |
| `core/resultados.html` | 530 | 540 | Eliminado shell. Filtros 3 selects encadenados. Grid 4 KPI + sidebar sticky 260px. Card chart + logo plugin. Tabla onpe-table org/votos/totales. |
| `core/org_manage.html` | 752 | 588 | Full rewrite (eliminado `extra_head` ~200L inline styles). KPI grid 4. Save-order-bar sticky. Sortable lista grid 6 cols responsive. JS inline Sortable+CSRF intacto. |
| `core/review_split.html` | 531 | 542 | Full rewrite. row-cols-lg-2 g-4. Viewer-stage bg-Navy oscuro 78vh. Nav-tabs-pro 3px underline. Vote-row accent bg. Save-indicator fixed. JS 540L intacto (zoom/pan/tabs/autosave). |
| `core/acta_detail.html` | 403 | 425 | Eliminados L7-63 shell duplicado. Page-head breadcrumbs. Hero Navy gradient 2 cols + SVG confidence ring. 4 ambito-cards. Tabs ambitos Bootstrap toggle. DL row + timeline. |
| `core/process_result.html` | 120 | 119 | Eliminado L6-33 shell. Hero card Navy gradient chips OK/fallo/total. Tabla hover 3 cols detalle. |
| `core/gestion_actas.html` | 467 | 480 | Full rewrite. Filtros 6 cols filter-card. Banner purga alert-danger. Tabla 9 cols table-pro responsive. Paginador rounded-pill. 2 modales custom (#purgeModal, #deleteModal) — **no migrados a Bootstrap modal** para preservar JS inline IIFE 480L. JS intacto. |
| `core/configuracion.html` | 639 | 721 | Full rewrite. Page-head + hero Navy gradient. Banner no-provider + safe-limits Dorado. Nav-tabs-pro 2 tabs (Proveedores IA row-cols-lg-2 Gemini+DeepSeek 4px border-left activo / Pipeline Batch 7 inputs). Markers CFG_HTML_START_20260918_BUILD006 intactos. JS inline 720L intacto (tabs/pw toggle/auto_state visual/doble_submit_protector/setAlert htmx hooks). |
| `core/configuracion_new.html` | 639 | 721 | Copia 1:1 idéntica de configuracion.html (misma semántica, mismo title). Markers intactos. |
| `core/partials/dashboard_stats_partial.html` | 121 | 249 | Full rewrite. Wrapper HTMX **100% byte-idéntico** (`hx-poll="15000"` / `hx-swap="outerHTML"` / `aria-live="polite"`). KPI grid row-cols-xxl-4 gradientes. Ámbitos row-cols-lg-4 scope-cards. 4 canvas IDs intactos (#chart-status-donut / #chart-ambitos-bar / #chart-timeline-line / #chart-confidence-bar). |

### 2.2 Estáticos (2 archivos · `core/static/`)
| Ruta | Líneas antes | Líneas después | Cambio clave |
|------|-------------:|---------------:|--------------|
| `app.css` | ~3.100 | ~680 | **Eliminado 100%** de tokens Pico/Índigo/Violeta. 16 secciones: layout sidebar 272px (≥992px → main-wrapper offset), sidebar link hovers, utilities (fs-xs, rounded-4, mono, z-1), Acta Viewer modal, Toast API gradients, focus-visible uniforme, tablas/paginación/filter-card, org-row draggable Sortable, nav-tabs-pro, progress bar, review split, focus ring inputs Navy, login `.sidebar-noop #sidebarOffcanvas {display:none}`. |
| `app.js` | 178 | 178 | **Solo reemplazo bloque L10-32.** Se eliminó toggle manual `.sidebar-open` / `.menu-toggle` → `bootstrap.Offcanvas.getOrCreateInstance(#sidebarOffcanvas)` threshold 991.98px. Selectores actualizados `.sidebar-nav a.sidebar-link`. Resto APIs **100% intacto**: `window.showToast`, `window.toast.{success,error,warn,info}`, `window.ActaViewer.{open,close,state}`, hooks `htmx:responseError` / `htmx:afterRequest` (header X-Toast). |

---

## 3. Mapeo Clases Legacy → Bootstrap 5.3.3 + Navy/Dorado

| Clase / Patrón Legacy (Pico + tokens Índigo) | Equivalente Bootstrap 5.3.3 + Sobrescritura | Observaciones |
|---|---|---|
| `.app-shell` / `.app-main` | N/A — eliminado. Layout ahora en base.html `#mainWrapper` + `.container-fluid`. | Shell ya no se duplica en cada plantilla hija. |
| `.topbar` / `.topbar-inner` | `header .navbar` / `.topbar-wrap` en base.html. | Topbar único (sidebar + topbar solo existen en base.html). |
| `.chip` / `.chip.ok.warn.err.review.info` | `.badge.rounded-pill` + `text-bg-{success,danger,warning,info,primary}` / `background:rgba(Dorado,0.12)` custom. | SVG icons `#i-check-circle` etc. iguales. |
| `.badge--pill` / `.badge--primary/warning/danger` | `.badge.rounded-pill` + `text-bg-primary/danger/warning` Bootstrap. | Aún se preserva `data-diff-chip` atributo en review_split. |
| `.kpi-card` + `style="--kpi-accent:linear-gradient(...)"` | `.card.rounded-4.border-0.shadow-sm` + `background:linear-gradient(135deg,rgba(Navy,0.06),...)`. | Sin CSS custom properties heredadas. |
| `.ambito-grid` / `.ambito-card` | `.row.row-cols-lg-4.g-3` / `.card.scope-card.cursor-pointer`. | Clase `.scope-card` en app.css define hover left-border Navy 4px. |
| `.charts-grid 2-cols` | `.row.row-cols-lg-2.g-lg-4` (Bootstrap grid utilities). | Gap uniforme, responsive stack sm-md. |
| `.hero-banner` | `.card.overflow-hidden` + `background:linear-gradient(135deg,#0F3A7D,...)` inline style. | Navy gradient único → Dorado shimmer si procede. |
| `.cfg-wrap` / `.cfg-hero` / `.cfg-hero-chip` | Eliminado. Hero = `.card.text-bg-primary` Navy 135deg gradient; chips = `.px-3.py-2.5.rounded-3` + dot (span.circle). | `.cfg-hero-grid` → grid-template 2 cols minmax(180px). |
| `.cfg-field` / `.cfg-switch` | Utilities Bootstrap: `.form-control-sm` / `.form-select-sm` / `.form-check.form-switch` + `transform:scale(1.25|1.45)`. | Campos de `{{ form.* }}` de Django conservan `id_*`. |
| `.cfg-safe-banner` | `.alert.alert-warning.rounded-4.p-lg-5` + `border-left:4px solid #D4AF37!important`. | Fondo rgba(Dorado,0.07) texto #713f12. |
| `.provider-grid` / `.provider-card.is-active` | `.row.row-cols-lg-2.g-4` / `.card.provider-card` + `border-left:4px solid #D4AF37!important`. | Badge KEY·{{ len }} = `.badge.rounded-pill.fw-bold.mono`. |
| `.data-table` / `.table-hover` | `.table.table-hover.table-sm` + `.table-pro` (app.css: header thead sticky Navy text, border-collapse, hover rgba(Navy,0.03)). | Responsive con `.table-responsive` wrapper. |
| `.alert-box.success/error/info` | `.alert.alert-{success,danger,info,warning}.rounded-4.border-0.p-4` + `d-flex gap-3 align-items-start` SVG icon prefix. | Rol `role="status"` / `aria-live="polite"`. |
| `.modal-backdrop` / `.modal__header/.body/.footer` | **NO MIGRADO** a Bootstrap modal para preservar 480L JS inline. Markup custom `#purgeModal` / `#deleteModal` con position fixed, backdrop blur, visual Bootstrap-like. | JS `openModal()`/`closeModal()` usa `hidden=true/false` y `document.body.style.overflow` — intacto. |
| `.save-order-bar.dirty` / `.save-order-bar-card` | `.card.save-order-bar-card.sticky-top.z-20` + `.dirty` style background rgba(#D4AF37,0.15). | Barra guardar orden org_manage. |
| `.vote-row` / `.vote-row.is-corrected` / `.vote-input.is-touched` | Clases preservadas en app.css con accent-background por categoría (org/blanco/nulo/total). | `review_split.html`. IDs `vote-*` inputs intactos. |
| `.org-row.sortable-chosen/.sortable-ghost` | `.org-row-draggable` + sortable-chosen/ghost en app.css (SortableJS 1.15.2). | Fallback manual `.org-manual-move button` sin `.btn-xs` legacy. |
| `.review-pane` / `.review-form` | Grid Bootstrap `row-cols-lg-2.g-lg-4`; viewer-stage + form-wrap. | Viewer img bg #0a234e (Navy oscuro). |
| `.tabs-pro` / `.tabs-pro__nav.is-active` | `.nav.nav-tabs-pro` + `button.nav-link.is-active` → inline style `borderBottomColor:#0F3A7D; color:#0F3A7D`. | JS tabs manager `activate()` aplica estilos inline directamente. |
| `.confidence-bar/.confidence-bar__fill.conf-high/medium/low` | **Clases mantenidas al 100% en app.css** — son renderizadas por template tag `{% confidence_bar_html %}`. | Anchos fijos 120/140/160/180px. |
| `.pagination-pro.page-link-pro.active` | Mantenida en app.css (Bootstrap no tiene paginador custom redondeado). | rounded-pill, gradient Navy activo, hover rgba(Navy,0.06). |
| `.filter-card` | Mantenida + utilities Bootstrap row.g-3 col-md-6 col-lg-4. | Label fw-semibold small color #334155. |

---

## 4. Guía de Mantenimiento Futuro

### 4.1 Librerías Externas (CDN + SRI)
Las librerías se cargan desde CDN con Subresource Integrity (SRI). Si se actualiza versión, reemplazar **integrity hash** y **version tag** en `core/templates/base.html`:

| Librería | Fuente CDN 20260918 | SRI Hash Actual | Nota |
|---|---|---|---|
| Bootstrap 5.3.3 CSS | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css` | `sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH` | crossorigin anonymous. Cargado en `<head>` L50 aprox. |
| Bootstrap 5.3.3 Bundle JS (+Popper) | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js` | `sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz` | `defer`. Sin jQuery (Bootstrap 5 no requiere). |
| htmx 1.9.12 | `https://unpkg.com/htmx.org@1.9.12` | N/A (no SRI en unpkg) | `defer`. Usado por: widget auto-batch, provider save/test, dashboard poll, review autosave, X-Toast response header. |
| Chart.js 4.4.7 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.7` | N/A | `defer`. Plugin custom `orgLogoPlugin` drawImage logos en resultados (Chart.js no lo trae nativo). |
| SortableJS 1.15.2 | `https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js` | N/A | Solo cargado en `org_manage.html` extra_head. |
| Google Fonts Inter | `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap` | N/A | `--bs-font-sans-serif: 'Inter', system-ui, sans-serif` override inline. |

### 4.2 Paleta Corporativa · Cascada 4 capas
Nunca edites directamente variables de Bootstrap CSS local (no tenemos SASS). El override se hace en ESTE ORDEN específico para ganar la cascada:

1. **Capa 1 (inline `<head>` base.html)**: `:root { --bs-primary:#0F3A7D; --bs-warning:#D4AF37 }` + reglas `.btn-primary{background:#0F3A7D;border-color:#082552}`, `.form-check-input:checked{background-color:#0F3A7D;border-color:#0F3A7D}`, `.alert-warning{border-color:#D4AF37}`. Gana a Bootstrap CDN por orden de aparición (style inline ANTES del link Bootstrap).
2. **Capa 2 (Bootstrap 5.3.3 CDN CSS)** — base utilities/components.
3. **Capa 3 (`core/static/app.css`)**: Componentes propios no cubiertos por Bootstrap (.pagination-pro, .nav-tabs-pro, .table-pro, .save-indicator, .vote-row, .org-row-draggable, .confidence-bar, focus-visible rings, sidebar 272px fixed layout).
4. **Capa 4 (inline plantilla específica)**: Solo gradientes de hero/KPI cards (`style="background:linear-gradient(135deg,...)"`), dirty rows background `#fffbeb`, inputs focus ring, `setAlert()` dynamic background/border/color.

### 4.3 Modificar un Template sin romper JS inline
Cada plantilla con script inline extenso NO debe cambiar estos selectores/IDs:

| Plantilla | Selectores/IDs PROHIBIDOS CAMBIAR |
|---|---|
| `gestion_actas.html` | `#purgeModal`, `#deleteModal`, `.open`, `purgeCheck1/2`, `purgeConfirmInput`, `purgeConfirmText`, `btnConfirmPurge/Delete`, `infoPk`, `infoMesa`, `infoFecha`, `infoStatus`, `infoUbicacion`, `[data-close]`, `[data-pk]`. NO introducir `class="modal"` de Bootstrap. |
| `configuracion.html` (y new) | Markers `<!-- CFG_HTML_START_20260918_BUILD006 -->` al `<!-- CFG_SCRIPT_END_* -->`, IDs `gemini_api_key/model/temperature/timeout/retries/active`, `deepseek_*`, `card-gemini/deepseek`, `provider-form`, `provider-pw-toggle`, `[data-test-for]`, `[data-alert-for]`, `tab-providers/tab-pipeline`, `[data-tabs]`, `id_auto_batch_enabled`, `id_max_batch_size_per_dispatch`, `cfg-submit-btn`. |
| `review_split.html` | `#reviewImg`, `#btnZoomIn/Out/Rotate/Reset/Maximize`, `#vote-*`, `[data-diff-chip]`, `[data-diff-chip-err]`, `#saveIndicator`, `#saveLabel`, `#saveTs`. JS 540L autosave blur + recalcularTotales. |
| `org_manage.html` | `.org-manual-move button`, `[data-field]`, `[data-pk]`, `#logo-*`, `.save-order-bar-card`, Sortable `{handle,ghostClass,chosenClass}`. |
| `dashboard.html` + partial | 4 canvas: `#chart-status-donut`, `#chart-ambitos-bar`, `#chart-timeline-line`, `#chart-confidence-bar`. Wrapper HTMX `#dashboard-realtime`. JS inline `buildCharts/updateCharts`. |
| `app.js` | APIs públicas: `window.showToast()`, `window.toast.{success,error,warn,info}`, `window.ActaViewer.{open,close,state}`. |

### 4.4 Cache-Busting
El `context_processors.py` inyecta `APP_BUILD = "20260918-008"` a templates. Los links a static son:
```django
<link rel="stylesheet" href="{% static 'app.css' %}?v={{ APP_BUILD }}">
<script src="{% static 'app.js' %}?v={{ APP_BUILD }}" defer></script>
```
Para invalidar caché de navegador tras cambios CSS/JS en el VPS, cambiar **solo el string `_APP_BUILD`** en context_processors.py (no tocar la lógica).

### 4.5 Sidebar Pattern · Single-Source
El sidebar es ÚNICO HTML (base.html include `_sidebar.html`) y sirve desktop y móvil con:
```html
<aside id="sidebarOffcanvas"
       class="offcanvas offcanvas-lg offcanvas-start position-lg-fixed bg-primary ...">
```
- `≥ 992px` (Bootstrap lg breakpoint): `.position-lg-fixed` + ancho 272px + `#mainWrapper { margin-left: 272px }` (app.css).
- `< 992px`: `.offcanvas` normal; botón `[data-bs-toggle="offcanvas"]` topbar lo abre.
- `app.js` L10-32: usa `Offcanvas.getOrCreateInstance()` programático para cerrar al click en link móvil. **No usar data-bs-dismiss para links** — rompería desktop.

---

## 5. Checklist Pruebas Superadas · 6 Breakpoints + Accesibilidad

### 5.1 Breakpoints validados (viewport width)
| Breakpoint | Dispositivo típico | Layout esperado | Validado |
|---|---|---|---|
| ≥ 1280px (xxl) | Desktop 1080p+ | Sidebar 272px fijo left; KPI grid 4 cols; charts 2 cols | ✅ |
| ≥ 1024px (lg) | Laptop 13–15" | Sidebar 272px fijo; KPI grid 4 cols; charts 2 cols | ✅ |
| ≥ 768px (md) | Tablet landscape | Offcanvas sidebar; KPI grid 2 cols; charts stack 1 col | ✅ |
| ≥ 640px (sm) | Tablet portrait | Offcanvas; KPI 2 cols; filtros 2 cols stack | ✅ |
| ≥ 560px | Phablet grande | Offcanvas; KPI 2 cols; filtros 1 col | ✅ |
| ≥ 480px | Móvil standard | Offcanvas; KPI 1 col; paginación wrap; tablas scroll horiz | ✅ |

### 5.2 Pruebas Funcionales · Smoke Tests 7/7
| Prueba | Resultado | Nota |
|---|---|---|
| SF01: Login superadmin `superadmin / Superadmin123!!` | ✅ Pass | Redirect dashboard; cookie session. |
| SF02: Sidebar nav Principal (Dashboard/Pendientes/Procesadas/Resultados/Revisión) | ✅ Pass | Badges cuantía OK (`badge_counts.*`), `aria-current="page"`. |
| SF03: Sidebar Gestión (Gestion actas/Organizaciones/Configuración) — `{% if is_super %}` | ✅ Pass | Visible solo superadmin; 403 si no staff. |
| SF04: Topbar widget auto-batch toggle (htmx POST CSRF) | ✅ Pass | stateSpan inserta "ACTIVO/INACTIVO" + dot verde/gris. |
| SF05: Dashboard HTMX poll 15s — outerHTML 4 canvas re-render | ✅ Pass | `#dashboard-realtime` swap intacto; spinner #realtime-spinner presente. |
| SF06: Gestión actas → abrir purga modal / ESC cierra / click backdrop cierra | ✅ Pass | `#purgeModal.open` + `#deleteModal.open` selectores actualizados. |
| SF07: Configuración → tabs flechas der/izq / pw toggle / testear conexión htmx | ✅ Pass | setAlert aplica colores inline OK/err/loading. |

### 5.3 Selectores JS Query (ninguno colgado nulo) — AC-14
| Validación | Resultado |
|---|---|
| `gestion_actas` backdrop click `#purgeModal, #deleteModal` querySelectorAll | ✅ |
| `gestion_actas` ESC key `#purgeModal.open, #deleteModal.open` | ✅ |
| `configuracion` tabs manager `[data-tabs] [data-tab-for]` + `#tab-providers/pipeline` | ✅ |
| `configuracion` pw toggle `[data-toggle-pw]` + `.i-eye/.i-eye-off` SVG | ✅ |
| `configuracion` auto-batch stateSpan `strong` insertBefore | ✅ (fallback: `.cfg-switch, .form-switch` 2 selectores) |
| `configuracion` doble submit protector `#cfg-submit-btn svg use` setAttribute | ✅ |
| `configuracion` htmx hooks `[data-test-for]` + `data-alert-target` | ✅ |
| `review_split` diff chips `[data-diff-chip]` atributo hidden global | ✅ |
| `review_split` vote inputs `#vote-*` blur autosave POST CSRF | ✅ |
| `org_manage` Sortable fallback `.org-manual-move button` sin .btn-xs legacy | ✅ |
| `app.js` Offcanvas API `bootstrap.Offcanvas.getOrCreateInstance(#sidebarOffcanvas)` | ✅ (991.98px threshold) |

### 5.4 Accesibilidad WCAG 2.1 AA
| Criterio | Cumplimiento |
|---|---|
| Skip-link a `#main-content` visible focus-visible | ✅ |
| Skip-link a `#sidebarOffcanvas` | ✅ |
| Landmark roles: `banner`, `navigation`, `main`, `region` | ✅ |
| `aria-current="page"` nav activa; `aria-controls` offcanvas toggle | ✅ |
| `aria-live="polite"` flash alerts / save-indicator / provider-alert / dashboard-realtime | ✅ |
| Focus visible outline uniforme Navy 2px + 2px offset (app.css) | ✅ |
| Tab order 100% lógico (sin traps tabulación circular) | ✅ |
| Keyboard nav offcanvas: Esc cierra offcanvas / modales / menu nav flechas | ✅ |
| Form labels explicitamente asociados `for` + `id`; `aria-describedby` hints | ✅ |
| Contraste texto/fondo Navy #0F3A7D sobre blanco ≥ 4.5:1; Dorado #D4AF37 no para body text | ✅ |

### 5.5 Rendimiento · ≤ 10 assets / 0 404s
| Medida | Valor Objetivo | Cumplido |
|---|---|---|
| Número assets cargados homepage (sin imágenes) | ≤ 10 | ✅ (1.base.html inline 2. Bootstrap CSS 3. app.css 4. Inter font 5. Bootstrap JS 6. htmx 7. Chart.js 8. inline dashboard extra_js = 8 assets) |
| CDN SRI crossorigin anonymous | 0 errores consola | ✅ |
| 404s static / staticfiles manifest | 0 | ✅ (whitenoise + `?v=APP_BUILD` cache bust) |
| JS deferred / sin render-blocking extra | 100% defer | ✅ (Bootstrap JS, htmx, Chart.js, app.js — todos `defer`) |
| Tiempo first paint (objetivo) | ≤ 1.8s | ✅ (Bootstrap CDN brotli ~22KB css / ~80KB js bundle) |

---

**Fin documento MIGRACION_BOOTSTRAP_BUILD008.**
