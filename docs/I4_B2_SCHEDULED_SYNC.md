# I4-B2 solicitado — sincronización programada de Instagram

27-10-2026. Rama `feature/i4-b2-scheduled-instagram-sync`. La petición explícita autoriza esta evolución Social sobre I4-A2/I4-B1. Se mantiene la especificación V1.0 y no se modifica V0.1. Prepara la captura diaria de JorgeBytes; no genera el informe semanal, recomendaciones, contenidos, correo ni publicaciones y no cierra V1.0.

## Arquitectura y seguridad

Task Scheduler → Python absoluto, con directorio de trabajo HelmOS absoluto → `social scheduled-sync run` → `InstagramCredentials(dpapi_only=True)` → sincronizador oficial existente → publicaciones y observaciones canónicas. Versión inicial `v26.0`, configurable; cuenta fija `jorgebytes`, verificada remotamente y sin opción para sustituirla.

Esta ruta ignora `HELMOS_INSTAGRAM_ACCESS_TOKEN` y utiliza exclusivamente el almacén DPAPI existente del usuario Windows actual. Conserva la política de renovación de I4-B1: no inventa caducidad ni elegibilidad; con expiración desconocida sigue siendo necesaria la renovación controlada allí descrita. No recibe tokens por argumentos ni los imprime o guarda en registros. Reautorizar/importar sigue siendo una operación interactiva de I4-B1.

Cada intento guarda un JSON atómico en `STATE\social\scheduled_instagram\runs\ID.json`: identificador, cuenta, configuración, inicio, fin, estado, código de salida, estadísticas, captura, errores seguros y necesidad de reautorización. Primero queda `running`; después `succeeded`, `partial`, `failed` o `busy`. `status` calcula última ejecución y último éxito desde esos registros. Un fallo conserva el último éxito. Una interrupción deja `running` consultable; el siguiente intento con bloqueo adquirido lo marca `interrupted`. Su `finished_at` será el momento de detección, no una fecha inventada de terminación.

Exclusión: `IgnoreNew` en la tarea y bloqueo del sistema operativo para el ciclo programado. Sincronizaciones Instagram manuales y programadas comparten además `.<nombre-state>.instagram.lock` en el padre del estado. Los bloqueos se liberan al morir el proceso; **no borrar sus archivos**, pues rompería la exclusión durante una ejecución. Otros escritores Social, como CSV, conservan la restricción existente de escritor único: no ejecutarlos simultáneamente con el sync.

## Comandos implementados

En PowerShell, con el usuario que importó la credencial DPAPI, preparar rutas absolutas. No hace falta elevar la terminal:

```powershell
$Python = (& python -c "import sys; print(sys.executable)").Trim()
$Repo = (Resolve-Path 'C:\workspace\HelmOS').Path
$State = Join-Path $Repo 'var'
Set-Location -LiteralPath $Repo
```

Usar el **mismo `$State` del histórico existente**, sustituyendo `var` si procede. Se admiten rutas con espacios.

```powershell
# Ejecución directa: usa Meta y escribe histórico cuando la ejecute el usuario
& $Python -m helmos --state-dir $State social scheduled-sync run
$LASTEXITCODE

# Consulta local sin red: last_run y last_success, o null si aún no existen
& $Python -m helmos --state-dir $State social scheduled-sync status
```

`run` no instala ni activa tareas. Opciones de `run` y `task install`:

| Opción | Defecto | Límites / significado |
| --- | --- | --- |
| `--api-version` | `v26.0` | Forma `vNN.0`; verificar soporte antes de cambiar |
| `--interval-days` | 1 | 1–365; frecuencia del disparador al instalar; no limita órdenes manuales |
| `--page-size` | 25 | 1–100 |
| `--max-pages` | 4 | 1–1000 |
| `--max-posts` | 100 | 1–10000 |
| `--max-requests` | 700 | 2–1000; incluye identidad, renovación y métricas |
| `--timeout` | 10 | >0 y ≤30 segundos por operación de socket |
| `--deadline-seconds` | 120 | >0 y ≤600 segundos de presupuesto de red compartido |
| `--at` | `09:00` | Hora local HH:MM; solo tiene efecto al instalar |

La respuesta HTTP sigue limitada a 1 MiB. El presupuesto de red se comprueba entre peticiones y lecturas; no es un temporizador estricto de proceso o disco. La tarea añade un límite de proceso de 15 minutos. No hay reintentos dentro de una ejecución. Red, 429 y presupuestos agotados finalizan con error; `Retry-After`, si existe, queda registrado. Antes de repetir manualmente, esperar al menos ese plazo. La siguiente tarea constituye el próximo intento; no hay bucles ni reintentos de renovación con resultado incierto.

## Instalación explícita y gestión

**Solo el usuario debe ejecutar esta orden cuando decida activar la programación. No se instaló ninguna tarea durante el desarrollo ni las pruebas.**

```powershell
& $Python -m helmos --state-dir $State social scheduled-sync task install --at 09:00 --interval-days 1 --api-version v26.0 --max-posts 100 --max-pages 4 --max-requests 700 --deadline-seconds 120
```

Registra `HelmOS-Instagram-jorgebytes` mediante XML temporal UTF-16, `schtasks.exe` y el SID actual obtenido con `whoami.exe`. Usa `InteractiveToken`, `LeastPrivilege` y rutas absolutas al intérprete, repositorio y estado. Rechaza SYSTEM y cuentas de servicio. No admite otra identidad, contraseñas, comandos libres ni shell. No reemplaza una tarea existente: para cambiar opciones, deshabilitarla, esperar al fin de la ejecución activa, eliminar y reinstalar. Ante restricciones del equipo devuelve error sin intentar elevar privilegios.

El instalador identifica al usuario actual, pero no lee ni prueba DPAPI. Instalar desde la sesión del usuario que importó la credencial y comprobarla previamente con I4-B1. La primera sincronización debe verificar que puede descifrar el almacén.

```powershell
# Configuración XML: rutas, argumentos, identidad y disparador
& $Python -m helmos --state-dir $State social scheduled-sync task query

# Última ejecución del Programador, próximo disparo y resultado (salida localizada)
& $Python -m helmos --state-dir $State social scheduled-sync task last-run

# Solicitar ejecución manual de la tarea ya instalada
& $Python -m helmos --state-dir $State social scheduled-sync task run

# Consultar después el resultado real
& $Python -m helmos --state-dir $State social scheduled-sync task last-run
& $Python -m helmos --state-dir $State social scheduled-sync status

# Deshabilitar y eliminar
& $Python -m helmos --state-dir $State social scheduled-sync task disable
& $Python -m helmos --state-dir $State social scheduled-sync task remove
```

`task run` devuelve `requested`: código 0 solo acredita que el Programador aceptó la solicitud. Consultar después su último resultado y el registro HelmOS. La gestión devuelve 2 ante fallos del sistema. Deshabilitar o eliminar no cancela una sincronización activa ni elimina credenciales, registros o histórico.

## Apagado, suspensión y recuperación

`StartWhenAvailable=true` permite que Windows encole un arranque posterior a un horario perdido, sujeto a disponibilidad y sesión: no es inmediato ni garantiza una repetición por cada día perdido. `WakeToRun=false` no despierta el ordenador. No hay sincronización mientras esté apagado. Tras encender o reanudar, con la sesión disponible, revisar tarea y registros. Véase [Microsoft StartWhenAvailable](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable).

Se exige sesión iniciada del usuario (`InteractiveToken`); no se afirma que DPAPI esté comprobado sin sesión. Bloquear la pantalla conserva la sesión, pero el funcionamiento real bajo bloqueo/suspensión queda por validar en este equipo. No se ofrece modo SYSTEM, S4U ni contraseña almacenada. La exclusión corresponde a [Microsoft MultipleInstancesPolicy](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-multipleinstancespolicy-settingstype-element).

Una terminación libera los bloqueos de ejecución automáticamente. El bloqueo de **credenciales de I4-B1**, en cambio, usa un archivo exclusivo que puede quedar abandonado y causar `credential_store_busy`. Solo después de confirmar que no hay procesos HelmOS ni renovación activos, el usuario puede retirar ese `.lock` del almacén según I4-B1. No confundirlo con los bloqueos de ejecución de esta entrega.

Ante un fallo fatal posterior se conservan publicaciones completamente recogidas y validadas antes del fallo. No se guardan métricas de una publicación interrumpida a mitad. Los errores parciales de métricas conservan lo observado y explicitan lo ausente. Una terminación abrupta antes de persistir puede perder el lote en memoria. Las escrituras canónicas completadas antes de un fallo de disco permanecen, aunque los contadores puedan ser desconocidos: el intento falla y el histórico constituye la evidencia verificable. Repetir concilia mediante la deduplicación existente sin sobrescribir versiones anteriores. Las métricas idénticas pueden no producir una observación nueva; la captura del intento sigue registrada.

Cada intento recorre una ventana acotada desde los medios recientes. No hay cursor persistente, backfill por día perdido ni garantía de cubrir todo el histórico cuando supera los límites. `bounded_listing=true` produce `partial`, nunca éxito completo. Ampliar límites dentro de los máximos y revisar cobertura. Son límites operativos, no una frecuencia editorial aprobada.

Ante `reauthorization_required=true`, revisar la credencial y reautorizar/importar manualmente para `jorgebytes` conforme a I4-B1. No editar JSON ni inventar fechas para habilitar renovación.

## Códigos y validación

| Código de `scheduled-sync run` | Resultado |
| --- | --- |
| 0 | Captura terminada sin errores ni listado truncado |
| 1 | Parcial por errores de métricas o listado truncado |
| 2 | Fallo de credenciales, API, red, configuración, presupuesto o persistencia |
| 3 | Excluida porque otro sincronizador posee el bloqueo |

La ausencia legítima de una métrica no se convierte en cero ni, por sí sola, en un fallo. No se modifican informes ni recomendaciones.

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Las 21 pruebas nuevas cubren CLI → credencial ficticia → HTTP simulado → histórico temporal; capturas posteriores y duplicados; identidad; ausencia de credencial; 429 y resultados anteriores; revocación; red; métricas; límites; registros/último éxito; recuperación; exclusión entre procesos y liberación tras terminación; exclusión con sync manual; fallo de disco; códigos; XML y codificación; rutas Windows con espacios; comandos simulados, SID actual y privilegios. No se lee el almacén DPAPI real, llama a Meta ni registra una tarea.

Pendiente de validación operativa por el usuario: instalación explícita, primer sync real con DPAPI, disparo diario, pantalla bloqueada y recuperación tras suspensión/apagado. La renovación real controlada de I4-B1 sigue pendiente. El informe semanal autónomo requiere un incremento posterior.

## Revisión de entrega

| Criterio | Estado y evidencia |
| --- | --- |
| Comportamiento | VERIFICADO en pruebas con HTTP y Task Scheduler simulados; operación real PENDIENTE de activación explícita |
| Contratos | VERIFICADO: sincronizador e histórico existentes, modo parcial optativo y bloqueo compartido; suite anterior conservada |
| Tests/build/lint | VERIFICADO: `python -m unittest discover -s tests`, 170 pruebas (149 previas + 21 nuevas); `git diff --check` sin errores |
| Migraciones | NO APLICA: sin cambios de esquema ni transformaciones del histórico |
| Seguridad | VERIFICADO: almacén real aislado en pruebas nuevas, HTTP simulado, cuenta fija, DPAPI obligatorio, mínimo privilegio y ausencia de secretos en registros |
| Observabilidad | VERIFICADO: inicio/fin/estado/captura/contadores, último intento y último éxito, fallos e interrupciones recuperables |
| Documentación | VERIFICADO: CLI, instalación/eliminación explícitas, códigos, recuperación y límites en este documento y enlace desde README |
| Cambios accidentales | VERIFICADO: revisión de diff limitada al incremento; especificaciones, informes y recomendaciones sin cambios |
