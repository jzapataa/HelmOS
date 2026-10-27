# I4-B2: diagnóstico local de published_at

Investigación del 27 de octubre de 2026, sin Meta, credenciales, ejecución de
sincronizaciones reales ni cambios en Task Scheduler o JSON históricos.

## Evidencia antes de cualquier conciliación

El intento `27f0af695f5a44b2ab3cb9f90472d811`, iniciado a las
`2026-10-27T17:21:29.246089+00:00`, falló en `observations_write`,
`publication_metadata_conflict`, posición 9 del lote, campo `published_at`.
Constan 25 publicaciones consultadas y 152 peticiones. El registro no conserva
la identidad, ninguna de las dos fechas ni el lote recibido. La posición no es
un identificador y no permite reconstruir el orden de Instagram.

**Publicación afectada, fecha histórica concreta y fecha nueva: no recuperables
de este intento con la evidencia local encontrada.** No se inventa el lote.
El conflicto entre los valores canónicos está demostrado; su causa temporal
(zona horaria, error del CSV u otra diferencia) sigue sin determinarse.

En `var/social/posts` hay 47 publicaciones: 22 Instagram, 15 TikTok y 10
LinkedIn. Las 47 fechas coinciden con sus filas originales y con el CSV local
(ruta privada excluida de Git). No se detectaron
discrepancias entre esas fuentes. Todas las publicaciones Instagram proceden
de ese CSV, con días entre 2026-08-12 y 2026-10-25, sin hora ni zona.

Existe una observación API adicional capturada el día anterior al diagnóstico.
Sus identificadores y fechas concretas quedan en la evidencia privada. La observación conserva procedencia `official_api` y versión
`v26.0`, pero no timestamp de publicación ni published_at recibido. Su existencia
no demuestra la fecha del lote fallido. Los registros Core y programados locales
inspeccionados no conservan un preview ni timestamps de ese lote.

Hay al menos un conflicto detectado en el intento. El total de conflictos es
desconocido porque el preflight se detiene en el primero. Las 22 publicaciones
Instagram son el conjunto histórico pendiente de contraste, no 22 conflictos
confirmados; tampoco se demuestra cuántas pertenecían al lote de 25.

## Contrato e impacto

- Identidad: pareja `(platform, post_id)`, ruta SHA-256 de ambos separados por
  NUL. No depende del orden, fecha o clasificación.
- CSV: `_day` valida estrictamente `YYYY-MM-DD` y conserva la fecha civil
  literal. No interpreta zona ni hora (`docs/I3_A_SOCIAL_IMPORT.md`).
- API: `SocialConnector.collect` exige un instante con zona, lo convierte a UTC
  y extrae el día. Offsets distintos del mismo instante dan el mismo día UTC.
  Una fecha civil CSV y el día UTC de un instante no son necesariamente iguales.
- Persistencia: published_at es inmutable en las rutas actuales de importación
  y sincronización. No existe un procedimiento aprobado de corrección. Se valida
  el lote antes de escribir; este conflicto no guarda publicaciones ni
  observaciones de ese lote. No es un problema de reautorización.
- Consumo: análisis filtra por published_at y conserva la fecha en evidencia;
  recomendaciones conservan ese análisis. Evaluaciones guardan published_at y
  snapshots de observaciones. Su ID no incluye published_at: una corrección
  aislada podría devolver una evaluación previa al reevaluar. CSV usa la fecha
  para validar observed_at y ventanas de medición; evolución usa las fechas de
  observación, sin recalcular días desde la publicación.

## Política recomendada

Aplicar **C** ahora: bloquear fechas distintas sin evidencia suficiente. R05 de
SYSTEM_SPEC_V1.0.md exige conservar versiones incompatibles; R10 conserva
recomendaciones y evaluaciones, y R11 obliga a explicitar incertidumbre.

**A** solo cubre equivalencia demostrable dentro del contrato. El conector ya
normaliza instantes a UTC. No se añade tolerancia de un día, zona Madrid por
defecto ni conversión del CSV a un instante inventado.

**B**, propuesta pendiente de decisión explícita y evidencia: identificar la
pareja exacta; conservar valor original, fila/archivo origen y huella; registrar
valor propuesto, evidencia API disponible, regla temporal, motivo, actor y
aprobación; comprobar observaciones y ventanas; definir la vigencia de la fecha
y de futuras evaluaciones sin reescribir snapshots o evaluaciones anteriores.
Preparar y revisar el cambio por publicación antes de aplicarlo, con comprobación
de que el valor histórico no cambió desde el diagnóstico. No se implementa ni
aplica una migración ni una excepción por publicación sin esos datos.

## Cambio acotado

Conservar el bloqueo y enriquecer su diagnóstico con la referencia SHA-256 que
identifica el JSON canónico, fechas ISO validadas y origen enumerado
(`csv`, `official_api`, `unknown`). No registrar filas, temas, rutas CSV, cuerpos
remotos ni texto de excepciones. No reconstruye intentos anteriores ni conserva
el lote entero. No modifica el esquema de las publicaciones u observaciones.
Las pruebas usan exclusivamente datos ficticios y directorios temporales.

## Validación realizada

- `python -m unittest discover -s tests`: 189 pruebas correctas (186 anteriores
  y 3 nuevas; también se amplía una regresión programada existente).
- `git diff --check`: sin errores de whitespace.
- Huella SHA-256 conjunta, calculada sobre nombres y contenido de 90 JSON bajo
  `var` y el CSV original, idéntica antes y después:
  `ae4ff71688458da73d64b5bcae56236c344d4ed5b7410faea2193bea71a19c53`.
- Sin conciliación aplicada, commit, push ni avance a I4-B3. El bloqueo real
  permanece pendiente de evidencia y aprobación de una corrección concreta.
