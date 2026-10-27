# I4-B2: conciliación limitada de cuatro fechas Instagram

Estado vigente: **aplicada**, según confirmación del usuario para este cierre.
La preparación y sus controles se documentan debajo como evidencia histórica. No cierra la validación operativa de I4-B2 ni
avanza a I4-B3. La operación es exclusivamente local y solo admite las cuatro
identidades, fechas y timestamps del informe aportado por el usuario el
27-10-2026. No llama a Meta, carga credenciales, modifica tareas programadas ni
se invoca desde la sincronización normal.

## Evidencia y decisión pendiente

La identidad es `(Instagram, post_id)`; la ruta canónica es el SHA-256 de ambos
separados por NUL. Se comprueba unicidad en el histórico, identidad dentro del
JSON, fecha histórica, fila original completa y coincidencia con el CSV fuente.
El plan contiene los bytes originales de cada JSON en base64 y su SHA-256,
ruta y SHA-256 del CSV, inventario de los JSON del estado y evidencia API.

La evidencia API procede del stdout de prevalidación suministrado por el
usuario, con cuenta, versión, instante de captura y timestamp por publicación.
No es una nueva consulta ni una certificación independiente del contenido remoto.
Se propone adoptar el día UTC oficial **mediante una decisión explícita**, sin
deducir una zona horaria para el CSV ni afirmar que se ha demostrado un error
en su fecha civil. La causa permanece `undetermined`.

Los únicos cambios de contenido en cada publicación son `published_at` y un
campo `date_reconciliation` que conserva fecha original, timestamp, operación,
fuente, cuenta, versión, captura, justificación e incertidumbre. `source_row`,
`initial_observation`, métricas y clasificación permanecen iguales. El CSV no
se edita. Los bytes originales también quedan en el recibo de auditoría.

## Preparación y aplicación

Desde `C:/workspace/HelmOS`:

```powershell
python -m helmos.instagram_date_reconciliation --state-dir C:\workspace\HelmOS\var --batch C:\workspace\HelmOS\.i4-b2-review\batch.json --plan C:\workspace\HelmOS\.i4-b2-review\plan.json --dry-run
```

Si no existe el plan, se prepara después de validar todas las precondiciones.
Si ya existe, se valida su vigencia sin regenerarlo ni cambiarlo silenciosamente.
El plan vive fuera del estado para que no altere su inventario. La carpeta
`.i4-b2-review/` está excluida de Git: contiene información privada.

El dry-run real preparado devuelve `ready`, `modified=0`; comprueba 90 JSON y
las cuatro filas originales entre 22 publicaciones Instagram. Su SHA-256 es:

```text
57b42a74a8c325695dafe82de5a8dae120c7e10f9650f01941deac5e72c81c8f
```

**Solo después de autorización explícita del usuario para este plan**, ejecutar:

```powershell
python -m helmos.instagram_date_reconciliation --state-dir C:\workspace\HelmOS\var --batch C:\workspace\HelmOS\.i4-b2-review\batch.json --plan C:\workspace\HelmOS\.i4-b2-review\plan.json --apply --approve 57b42a74a8c325695dafe82de5a8dae120c7e10f9650f01941deac5e72c81c8f --actor Jorge
```

La huella abarca el plan serializado canónicamente. Se rechaza una aprobación
distinta, actor vacío, otro directorio, identidad, fecha, timestamp, conjunto
incompleto, contenido histórico diferente o cualquier cambio en los JSON de
dependencia o en el CSV. Un plan desactualizado requiere nueva preparación,
revisión y autorización. No se amplía el lote ni se permite tolerancia de días.

La aplicación se hará en una ventana sin otros comandos Social en ejecución;
no se habilitará Task Scheduler para ello. El bloqueo del kernel se comparte
con sync manual/programado, importación CSV e importación de snapshots. Impide
escritores concurrentes que usen estas rutas; no protege frente a editores
externos que ignoren el bloqueo ni ofrece snapshots a lectores ya iniciados.
El archivo lateral del bloqueo puede existir incluso tras un dry-run.

## Atomicidad, recuperación e idempotencia

Antes de sustituir publicaciones se escribe y sincroniza a disco un diario en
`social/.i4-b2-date-transaction.json`, con el plan y la aprobación. Cada archivo
se escribe en un temporal del mismo directorio, con `flush`, `fsync` y
`os.replace`. No se afirma atomicidad indivisible de los cuatro archivos.

Si falla una escritura antes del recibo, se restauran los bytes originales de
las cuatro publicaciones. Si falla también la restauración o se mata el
proceso, permanece el diario: lectura de publicaciones, importación y sync
quedan bloqueadas hasta recuperación explícita. Un lector ya iniciado no
queda aislado; de ahí la ventana operativa exclusiva. No se promete protección
frente a fallos físicos del disco o pérdida de energía más allá de las garantías
del sistema de archivos.

Para recuperar **un lote interrumpido y sin recibo comprometido**, con la misma
autorización y el mismo plan:

```powershell
python -m helmos.instagram_date_reconciliation --state-dir C:\workspace\HelmOS\var --batch C:\workspace\HelmOS\.i4-b2-review\batch.json --plan C:\workspace\HelmOS\.i4-b2-review\plan.json --rollback --approve 57b42a74a8c325695dafe82de5a8dae120c7e10f9650f01941deac5e72c81c8f --actor Jorge
```

La recuperación valida las cuatro versiones antes de restaurar ninguna: solo
admite los bytes originales o los propuestos. Nunca pisa una tercera versión.
Este rollback recupera una transacción incompleta; no revierte una conciliación
confirmada ni borra su auditoría. Las pruebas de recuperación usan únicamente
datos sintéticos en directorios temporales.

Al terminar se conserva `social/reconciliations/i4-b2-instagram-dates-20261027.json`
con plan completo, huella aprobada, actor y momento de la invocación autorizada.
Un replay idéntico verifica recibo y las cuatro versiones finales y devuelve
`already_applied`, sin reescribir. Si el proceso terminó después del recibo y
antes de retirar el diario, el replay autorizado completa esa limpieza.

## Dependencias e impactos

- `social_analyze.prepare_dataset`: filtros inclusivos y evidencia usan
  `published_at`. Una selección que termina en el día CSV puede perder la
  publicación y una que empieza en el día API puede incorporarla. Orden y
  referencias locales `Pxxx` también pueden cambiar en análisis nuevos.
- Los análisis e informes guardados y las recomendaciones registradas conservan
  su evidencia original; no se regeneran ni se modifican retrospectivamente.
  Asociaciones por plataforma e ID mantienen su identidad.
- `recommendation_evaluations.evaluate_recommendation`: para una publicación
  conciliada se incluye operación y fecha vigente en la identidad de la nueva
  evaluación. Se evita reutilizar una evaluación previa con fecha CSV; esta
  sigue recuperable. Las evaluaciones ajenas a la conciliación conservan sus IDs.
- Observaciones y evolución: las métricas y sus fechas no se editan; los
  intervalos se calculan entre fechas de observación. Se comprueba que la fecha
  propuesta no supere `observed_at` ni `period_start` cuando existen. En las
  cuatro publicaciones reales solo consta la observación inicial sin fecha ni
  periodo: no hay cobertura temporal que recalcular.
- Importación/sync conservan el bloqueo estricto de metadatos. Reimportar el CSV
  original tras la conciliación seguirá produciendo conflicto de fecha: no hay
  excepción automática para esas filas ni para conflictos futuros.

## Verificación

Pruebas: dry-run sin escrituras, aprobación, cuatro precondiciones exactas,
fuente CSV, dependencias, idempotencia, bloqueo entre procesos y con CSV,
fallo de `os.replace`, fallo de escritura intermedia y de recibo, restauración
byte a byte, muerte real de un subproceso, recuperación explícita, fallo del
rollback, rechazo de versiones desconocidas, ventanas incompatibles, filtros,
conservación de recomendaciones/evaluaciones y bloqueo de conflictos futuros.

Los artefactos privados `dry-run.json`, `records.before.json` y
`records.after.json` permiten revisar el resultado y los cuatro JSON completos
antes de autorizar. La preparación real no ha aplicado la conciliación.

Validación final local: `python -m unittest discover -s tests`, **219 pruebas
correctas** (21 de conciliación); `git diff --check`, código 0, sin errores de
whitespace. La huella del inventario de los 90 JSON y la del CSV siguen
coincidiendo con el plan. Se ha localizado un análisis guardado y una
recomendación con referencias a estas publicaciones; se conservan sin cambios.

## Cierre técnico de la preparación

| Comprobación | Estado |
| --- | --- |
| Comportamiento solicitado | VERIFICADO: preparación y dry-run, sin aplicación real |
| Contratos | VERIFICADO: conflictos estrictos y datos históricos conservados |
| Tests/build/lint | VERIFICADO: 219 pruebas y diff check; proyecto Python sin build adicional |
| Migración y recuperación | VERIFICADO en fixtures; aplicación real PENDIENTE de autorización |
| Seguridad | VERIFICADO: operación offline, aprobación por huella y actor, rechazo de links |
| Observabilidad | VERIFICADO: informe, diario y recibo explícitos |
| Documentación | VERIFICADO: procedimiento, recuperación y límites documentados |
| Cambios accidentales | VERIFICADO: revisión del diff; cambios previos del workspace conservados |

## Privacidad del lote y cierre

Las cuatro identidades, fechas y evidencia API se cargan explícitamente con
`--batch` desde `.i4-b2-review/batch.json`, excluido de Git junto con el plan,
recibo privado y respaldos. El código versionado no contiene esas identidades;
las pruebas usan cuatro publicaciones ficticias. Sin lote privado se bloquea
la operación. La huella de aprobación del plan y los controles de coincidencia
se mantienen; cambiar el lote no autoriza un plan distinto.

La prevalidación posterior confirmó `clear`, con 22/22 publicaciones históricas
comprobadas. La sincronización manual terminó `succeeded`, con 25 observaciones
guardadas y tres publicaciones nuevas. Estos resultados fueron confirmados por
el usuario y no se han reproducido durante este cierre. Véase
[I4_B2_CLOSURE.md](I4_B2_CLOSURE.md).
