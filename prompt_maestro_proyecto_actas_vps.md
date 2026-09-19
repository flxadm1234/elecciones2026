# Proyecto de Transcripción y Auditoría de Actas Electorales en VPS Ubuntu

## Recomendación Técnica

### Diagnóstico del método
La metodología de enviar la imagen completa del acta directamente a un modelo multimodal como Gemini o Deepseek funciona y es rápida de implementar, pero **no es la opción más precisa ni la más robusta** para actas electorales estructuradas de nivel regional, provincial y distrital.

La razón es simple:

- las actas tienen estructura fija o casi fija;
- contienen celdas numéricas pequeñas, texto manuscrito y correcciones;
- la exactitud depende mucho de detectar bien las zonas del documento;
- un único paso de visión general con LLM puede confundir filas, omitir organizaciones o leer mal un número manuscrito.

### Método recomendado
La solución óptima es un **pipeline híbrido de visión por computadora + OCR guiado por plantilla + LLM estructurador/validador**.

Orden recomendado:

1. **Clasificación del tipo de acta**
   - Detectar si el documento es regional o provincial-distrital.
   - Identificar también la variante de plantilla si existen diferencias por jurisdicción o proceso electoral.

2. **Preprocesamiento de imagen**
   - enderezado,
   - mejora de contraste,
   - reducción de ruido,
   - detección de bordes,
   - corrección de perspectiva,
   - binarización adaptativa cuando aporte claridad.

3. **Localización de zonas por plantilla**
   - ubicar encabezado,
   - número de mesa,
   - ubigeo o bloque de ubicación,
   - tabla de organizaciones/candidatos,
   - votos en blanco,
   - votos nulos,
   - votos impugnados,
   - total de votos emitidos.

4. **Extracción OCR por región**
   - OCR específico para texto impreso,
   - OCR reforzado para celdas numéricas,
   - procesamiento especial para números manuscritos o corregidos.

5. **LLM multimodal como capa de interpretación y consolidación**
   - Gemini como motor principal,
   - Deepseek como alternativa configurable,
   - opcionalmente usar doble lectura en casos de baja confianza o discordancia.

6. **Validación determinística**
   - suma de todas las filas,
   - validación cruzada contra el total del acta,
   - validación contra catálogos de organizaciones políticas,
   - validación de unicidad,
   - validación de consistencia territorial y tipo de acta.

7. **Cola de revisión humana**
   - si la confianza es baja,
   - si la suma no cuadra,
   - si la plantilla no se identifica,
   - si hay duplicidad,
   - si el OCR/LLM discrepa entre sí.

### Por qué este método es superior
- **Mayor exactitud**: reduce errores de lectura al trabajar por zonas específicas del acta.
- **Mayor rendimiento**: evita enviar información irrelevante al modelo.
- **Mayor confiabilidad**: combina reglas determinísticas con IA.
- **Mayor trazabilidad**: se sabe exactamente qué campo falló.
- **Mejor escalabilidad**: permite reintentar solo zonas o documentos problemáticos.

### Método alternativo de máxima precisión
Si el objetivo fuera maximizar precisión por encima de costo y complejidad, la opción más fuerte sería:

- detección por plantilla,
- OCR especializado por región,
- doble extracción con Gemini + Deepseek,
- reconciliación automática,
- revisión humana obligatoria para discrepancias.

Ese enfoque es más preciso que usar solo Gemini o solo Deepseek, pero también más costoso y más lento.

## Prompt Maestro

Copia y usa el siguiente prompt como instrucción principal para construir el sistema completo.

---

Asume el rol de **Arquitecto Senior de Sistemas, Ingeniero Backend, Ingeniero de Datos y Especialista en Visión Computacional para Auditoría Electoral**.

Debes diseñar e implementar un proyecto productivo, completo y desplegable para **transcribir, validar, almacenar, auditar y corregir actas de escrutinio electoral a partir de imágenes**.

El sistema debe estar orientado a operar en un **VPS Ubuntu en modo consola**, con **base de datos PostgreSQL**, **panel de administración web**, **procesamiento asíncrono por cola**, y soporte configurable para **Gemini** y **Deepseek** como motores de IA.

## Objetivo General

Construye una plataforma completa que:

1. lea rutas de imágenes de actas almacenadas en PostgreSQL;
2. procese esas imágenes con el motor de IA seleccionado;
3. capture la transcripción estructurada y el nivel de confianza;
4. valide resultados con reglas aritméticas y de negocio;
5. marque casos dudosos para intervención humana;
6. permita revisar, corregir y auditar cada acta desde una administración web segura;
7. pueda desplegarse en Ubuntu con scripts de instalación, migración, arranque y ejecución automática.

## Decisión Arquitectónica Obligatoria

No implementes un flujo basado únicamente en enviar imágenes completas a un LLM.

Debes implementar y documentar un **pipeline híbrido** con estas capas:

1. preprocesamiento de imagen;
2. detección o alineación por plantilla;
3. extracción OCR por zonas;
4. interpretación multimodal con Gemini o Deepseek;
5. validación aritmética y de consistencia;
6. cola de revisión humana.

Además, documenta comparativamente por qué este enfoque es superior a un flujo puramente LLM en exactitud, rendimiento y confiabilidad para actas regionales, provinciales y distritales.

## Stack Recomendado y Esperado

Usa este stack salvo que exista una razón técnica fuerte para mejorar alguna pieza:

- **Backend web**: Django + Django Admin + Django REST Framework
- **Base de datos**: PostgreSQL
- **Cola asíncrona**: Celery + Redis
- **Servidor web**: Gunicorn + Nginx
- **Migrations**: Django ORM + migraciones nativas
- **Procesamiento de imágenes**: OpenCV + Pillow
- **OCR**: Tesseract OCR con español, y diseño preparado para reemplazo por OCR más avanzado si fuera necesario
- **IA multimodal**: conectores para Gemini y Deepseek
- **Despliegue**: systemd para servicios automáticos en Ubuntu
- **Configuración**: variables de entorno con `.env`
- **Pruebas**: pytest

Si propones un stack distinto, debes justificarlo técnicamente y mantener todos los requisitos funcionales.

## Requisitos de Infraestructura

Implementa todo lo necesario para Ubuntu Server en consola:

1. script `install_ubuntu.sh` para:
   - actualizar paquetes,
   - instalar Python,
   - instalar PostgreSQL,
   - instalar Redis,
   - instalar Nginx,
   - instalar Tesseract con idioma español,
   - instalar librerías del sistema requeridas por OpenCV/Pillow/PostgreSQL.

2. script `setup_env.sh` para:
   - crear entorno virtual,
   - instalar dependencias,
   - crear `.env` desde `.env.example`.

3. script `migrate.sh` para:
   - ejecutar migraciones,
   - crear usuario administrador inicial si no existe.

4. script `bootstrap.sh` para:
   - dejar el proyecto listo en un servidor nuevo con una sola ejecución.

5. archivos `systemd` para:
   - servicio web,
   - servicio worker Celery,
   - servicio beat o scheduler si se usa programación automática.

6. configuración ejemplo de Nginx para:
   - proxy reverso,
   - archivos estáticos,
   - archivos multimedia si las imágenes se sirven desde el mismo VPS.

## Requisitos de Base de Datos

Debes diseñar un esquema PostgreSQL normalizado, auditable y preparado para alta consistencia.

### Entidades mínimas

Crea al menos las siguientes tablas o modelos:

1. `geo_departments`
2. `geo_provinces`
3. `geo_districts`
4. `political_organizations`
5. `election_processes`
6. `acta_images`
7. `actas`
8. `acta_transcriptions`
9. `acta_vote_entries`
10. `processing_jobs`
11. `processing_alerts`
12. `manual_review_queue`
13. `audit_log`
14. `ai_provider_settings`
15. `human_corrections`

### Reglas de modelado obligatorias

#### `acta_images`
Debe almacenar:
- ruta absoluta o relativa del archivo,
- hash del archivo,
- estado de disponibilidad,
- metadatos del archivo,
- fecha de carga.

#### `actas`
Debe almacenar:
- identificador único del acta,
- número de mesa,
- tipo de acta,
- proceso electoral,
- ubicación geográfica,
- estado del acta,
- referencia a la imagen fuente.

Tipos de acta:
- `regional`
- `provincial_distrital`

#### `acta_transcriptions`
Debe almacenar:
- motor IA usado,
- versión del prompt,
- JSON crudo devuelto por la IA,
- confianza global,
- estado de validación,
- observaciones técnicas,
- timestamps,
- referencia al job de procesamiento.

#### `acta_vote_entries`
Debe almacenar las filas de votos en forma normalizada, no como columnas fijas.

Campos mínimos:
- acta,
- categoría de registro,
- organización política o candidato,
- cargo,
- ámbito,
- orden,
- cantidad de votos,
- origen del dato,
- confianza del campo.

### Casuística obligatoria

#### Acta regional
Debe soportar:
- votos para gobernador regional,
- votos para consejeros regionales,
- blancos,
- nulos,
- impugnados,
- total de votos emitidos.

#### Acta provincial-distrital
Debe soportar:
- votos para candidatos provinciales,
- votos para candidatos distritales cuando existan,
- escenarios en los que una localidad solo tenga candidatos provinciales,
- blancos,
- nulos,
- impugnados,
- total de votos emitidos.

### Prevención de duplicados
Implementa validaciones estrictas a nivel de base de datos y aplicación.

Debe existir como mínimo:

- restricción única por combinación de:
  - tipo de acta,
  - proceso electoral,
  - ubicación geográfica,
  - número de mesa,
  - identificador único del acta.

- verificación por hash de imagen para detectar duplicados exactos,
- validación de duplicidad lógica cuando dos imágenes distintas correspondan al mismo acta.

## Procesamiento con IA

Implementa una capa de abstracción para proveedores IA con interfaz común.

Debe existir soporte para:
- Gemini como proveedor principal,
- Deepseek como proveedor alternativo.

### Requisitos funcionales

1. el proveedor se debe elegir desde la administración web;
2. las credenciales deben gestionarse desde la administración web;
3. las credenciales no deben almacenarse en texto plano;
4. deben cifrarse en base de datos usando una clave maestra almacenada en variable de entorno;
5. debe poder definirse:
   - proveedor activo,
   - modelo,
   - temperatura,
   - timeout,
   - reintentos,
   - umbral de confianza,
   - modo de fallback.

### Flujo de procesamiento obligatorio

Para cada imagen:

1. leer la ruta desde PostgreSQL;
2. verificar existencia y hash;
3. preprocesar imagen;
4. clasificar el tipo de acta;
5. detectar regiones de interés;
6. ejecutar OCR por zonas;
7. enviar contexto estructurado + imagen al proveedor IA activo;
8. recibir JSON estructurado;
9. normalizar salida;
10. calcular confianza por documento y por campo;
11. validar aritmética;
12. validar catálogo de organizaciones;
13. guardar resultados;
14. si hay baja confianza o inconsistencia, generar alerta y enviar a revisión humana.

### Reglas de validación obligatorias

Implementa, como mínimo:

1. suma manual de votos por filas;
2. comparación con el total consignado en el acta;
3. validación de que no existan organizaciones repetidas;
4. validación de número de mesa;
5. validación geográfica;
6. validación del tipo de acta contra la estructura detectada;
7. validación de que el mismo acta no sea procesado dos veces como registro final aprobado.

## Panel de Administración Web

Desarrolla un panel web con autenticación, permisos y auditoría.

### Módulos obligatorios

#### 1. Organizaciones políticas
- crear,
- editar,
- activar/desactivar,
- ordenar,
- asociar ámbito,
- importar/exportar.

#### 2. Configuración IA
- seleccionar Gemini o Deepseek,
- registrar y actualizar credenciales,
- probar conexión,
- definir parámetros de inferencia,
- fijar umbral de confianza,
- activar fallback o doble validación.

#### 3. Gestión de imágenes y actas
- listar imágenes,
- ver estado de procesamiento,
- relanzar procesamiento,
- filtrar por tipo, ubicación, estado, proveedor, confianza o error.

#### 4. Resultados
- visualizar detalle completo,
- mostrar JSON crudo,
- mostrar votos normalizados,
- mostrar auditoría aritmética,
- mostrar historial de procesamiento.

#### 5. Intervención humana
- cola de actas observadas,
- visor del acta original,
- formulario seguro de corrección,
- registro de qué usuario corrigió qué campo,
- comparación entre valor IA y valor humano,
- cierre de caso.

### Seguridad

Debes incluir:
- autenticación robusta,
- permisos por rol,
- bitácora de cambios,
- protección CSRF,
- cifrado de credenciales,
- validación de entradas,
- sanitización de archivos y rutas.

## API Interna y Servicios

Diseña endpoints o servicios internos para:

- registrar imágenes,
- crear jobs,
- consultar estado,
- reprocesar actas,
- listar observaciones,
- aprobar correcciones,
- exportar resultados.

No expongas secretos en respuestas ni logs.

## Exportación y Reportes

Genera al menos:

1. exportación detallada de votos en formato fila por fila;
2. exportación resumen por acta;
3. exportación de actas observadas;
4. métricas de precisión, discordancias y productividad de revisión.

## Estructura Esperada del Repositorio

La solución final debe quedar organizada de forma clara, por ejemplo:

```text
project/
  app/
  config/
  scripts/
  systemd/
  nginx/
  docs/
  tests/
  .env.example
  requirements.txt
  README.md
```

## Entregables Obligatorios

Debes generar y dejar listos, como mínimo:

1. código fuente completo;
2. modelos y migraciones;
3. conectores Gemini y Deepseek;
4. pipeline de OCR + LLM + validación;
5. administración web funcional;
6. scripts de instalación para Ubuntu;
7. archivos systemd;
8. configuración ejemplo de Nginx;
9. `.env.example`;
10. pruebas automáticas;
11. documentación de despliegue;
12. documentación técnica de la metodología elegida;
13. documentación de criterios de revisión humana.

## Criterios de Calidad

El sistema debe priorizar:

- exactitud,
- trazabilidad,
- reproducibilidad,
- escalabilidad,
- tolerancia a fallos,
- seguridad operacional.

## Criterios de Validación de Aceptación

Considera la implementación correcta solo si:

1. el proyecto puede instalarse en Ubuntu con scripts sin intervención manual compleja;
2. PostgreSQL almacena rutas de imágenes y resultados estructurados;
3. la administración web permite cambiar entre Gemini y Deepseek;
4. las credenciales se gestionan de forma segura;
5. el pipeline marca automáticamente actas con baja confianza;
6. existe edición humana con auditoría;
7. se impiden duplicados por reglas fuertes;
8. los dos tipos de acta quedan soportados correctamente;
9. el sistema puede reprocesar documentos de manera segura;
10. toda la solución queda documentada y testeada.

## Requisito Especial sobre Implementación

Entrega la solución de forma incremental en este orden:

1. arquitectura y estructura del proyecto;
2. modelos y base de datos;
3. conectores IA;
4. pipeline de procesamiento;
5. administración web;
6. cola de revisión humana;
7. despliegue Ubuntu;
8. pruebas y documentación final.

En cada etapa:
- explica decisiones técnicas,
- muestra código,
- valida que la etapa anterior siga funcionando,
- no avances dejando módulos críticos incompletos.

## Requisito Especial sobre Precisión

Debes explicar explícitamente cómo manejarás:

- números manuscritos,
- tachaduras y correcciones,
- organizaciones políticas faltantes o mal leídas,
- actas con estructura parcialmente dañada,
- diferencias entre actas regionales y provincial-distritales,
- casos sin candidatura distrital,
- duplicados lógicos,
- documentos con baja calidad o rotación.

## Resultado Esperado del Trabajo

Quiero una solución real, desplegable y mantenible, no un prototipo superficial.

Debes producir:
- implementación,
- documentación,
- scripts de despliegue,
- validaciones,
- y un sistema administrable por web listo para operar en un VPS Ubuntu.

---

## Uso sugerido

Si vas a usar este documento con otra IA de programación, dale este prompt como instrucción principal y luego añade:

- rutas reales donde estarán las imágenes;
- volumen esperado de actas por día;
- si usarás almacenamiento local o S3;
- si quieres interfaz solo para admin o también dashboard operativo;
- proveedor SMTP, Telegram o webhook para alertas.
