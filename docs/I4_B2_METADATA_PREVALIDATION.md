# I4-B2: prevalidación explícita de metadatos Instagram

Estado posterior confirmado por el usuario: `clear`, 22/22 publicaciones
históricas comprobadas tras la conciliación auditada. No se repitió la consulta
durante el [cierre](I4_B2_CLOSURE.md).

`--prevalidate-metadata` reutiliza `/me/media`, sus campos, paginación y límites
existentes. Verifica la cuenta por la ruta habitual de credenciales. No solicita
Insights, renueva credenciales, actualiza el almacén ni persiste publicaciones u
observaciones. Conserva el bloqueo compartido I4-B2: puede crear/utilizar su
archivo lateral. No crea un registro programado. Devuelve JSON por stdout.

## Ejecución única por la persona usuaria

Desde `C:/workspace/HelmOS`, con el intérprete Python y el almacén protegido ya
configurados, ejecutar una sola vez:

```powershell
python -m helmos --state-dir C:\workspace\HelmOS\var social sync instagram --prevalidate-metadata --api-version v26.0 --expected-username jorgebytes --page-size 25 --max-pages 4 --max-posts 100 --max-requests 20 --timeout 10 --deadline-seconds 120
```

Este comando **sí consulta Meta cuando lo ejecuta el usuario**; no se ha ejecutado
contra Meta durante el desarrollo. Para utilizar exclusivamente DPAPI, retirar
de esa sesión cualquier variable `HELMOS_INSTAGRAM_ACCESS_TOKEN` que pudiera
prevalecer sobre el almacén. No incluir tokens en argumentos ni informes. Ante
caducidad, gestionar la credencial conforme a I4-B1 en una operación separada.

No activar ni invocar Task Scheduler. Para conservar el informe, redirigir stdout
a un archivo local elegido por el usuario; no añadir datos reales a Git. Un
fallo devuelve error seguro por stderr, sin declarar `clear`. No se aplica una
conciliación al terminar.

## Informe y códigos

Ejemplo ficticio de un elemento de `conflicts`:

```json
{
  "platform": "Instagram",
  "post_id": "123",
  "field": "published_at",
  "historical": {
    "value": "2026-10-01", "source": "csv",
    "source_csv": "history.csv", "source_row_value": "2026-10-01",
    "timezone": "unknown"
  },
  "received": {
    "value": "2026-09-30", "source": "official_api",
    "timestamp": "2026-10-01T00:30:00+02:00", "canonical_timezone": "UTC",
    "account_id": "456", "api_version": "v26.0"
  },
  "difference": {
    "type": "different_civil_dates", "timezone_cause": "undetermined",
    "offset_changes_received_day": true,
    "explanation": "A historical day alone cannot establish timezone equivalence."
  }
}
```

La identidad estable es `(platform, post_id)`. Se conserva la representación ISO
del instante recibido con su offset y el día UTC, sin copiar cuerpos HTTP. El CSV
no contiene zona: no se atribuye el conflicto a Madrid ni se acepta una
tolerancia de un día. El informe incluye la ruta CSV cuando consta: tratarlo
como información privada.

Devuelve todos los conflictos de fecha de registros canónicos presentes en la
ventana. Con `--metadata`, también compara `topic`, `format` y vínculo editorial
existente: su procedencia es el archivo editorial, no Instagram. El tipo de medio
API no se convierte en formato editorial. Inconsistencias internas del histórico
y errores API siguen bloqueando la operación.

La raíz incluye `status`, `conflicts`, `conflict_count`, `conflicting_posts`,
`posts_consulted`, `canonical_posts_checked`, `canonical_posts_unchecked`,
`bounded_listing`, cuenta, versión, captura, peticiones y `insights_requested=0`.
No devuelve métricas vacías como observaciones válidas.

- Código 0: `clear` en la ventana consultada.
- Código 1: ventana truncada sin conflictos, `partial`.
- Código 2: conflictos (`conflicts`) o fallo. La truncación sigue visible si hay
  conflictos.

## Cobertura y relación con sync

`--preview` sigue consultando métricas y es incompatible con este modo. La
sincronización normal conserva su contrato y preflight de persistencia: no omite
publicaciones incompatibles. La prevalidación se ejecuta explícitamente antes de
decidir una conciliación o sincronización; no es un permiso persistente ni una
garantía frente a cambios posteriores en Instagram.

Los límites pueden truncar la ventana. Incluso con `bounded_listing=false`, solo
se comprueban publicaciones devueltas para esa cuenta y permisos. Las canónicas
ausentes quedan sin comprobar. No hay backfill ni cursor persistente. Un fallo
de red, presupuesto o autenticación aborta sin declarar cobertura completa.

No recupera el lote perdido anterior ni modifica fechas. Se mantiene la
[política de conciliación](I4_B2_PUBLISHED_AT_DIAGNOSIS.md).

## Verificación local

`python -m unittest discover -s tests`: 198 pruebas correctas (189 previas y 9
nuevas). HTTP simulado, directorios temporales y acceso real a credenciales y
Task Scheduler prohibido en las pruebas nuevas. Se verifican múltiples páginas,
fechas/offsets, metadatos editoriales, ausencia de Insights y escrituras,
truncación, límites, errores y credenciales sin renovación ni guardado.
`git diff --check` sin errores. La huella conjunta de los 90 JSON locales y CSV
original permanece idéntica a la documentada en el diagnóstico anterior.
