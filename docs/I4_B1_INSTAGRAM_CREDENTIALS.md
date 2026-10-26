# I4-B1 solicitado — credenciales de Instagram

26-10-2026. Rama `feature/i4-b1-instagram-credentials`. Evolución Social autorizada por la petición explícita de I4-B1; no cambia la especificación V1.0 ni la V0.1, no cierra V1.0 y no inicia I4-B2. No hay programador, servidor, OAuth completo, publicación ni correo.

## Evidencia documental y límites de Meta

Revisión exclusiva de renovación, 26-10-2026: **contrato oficial reconfirmado** en [Actualizar el token de acceso, Meta for Developers](https://developers.facebook.com/documentation/instagram-platform/reference/refresh_access_token), página que indica actualización del 17-07-2025. La ruta antigua `/docs/` devolvió 429 en la herramienta web; Chrome permitió leer la referencia pública y su redirección a `/documentation/`. La consulta documental no usó tokens ni llamadas autenticadas. Esta comprobación documental no acredita una renovación real ni resuelve los otros límites documentales de la implementación previa.

Meta especifica GET por HTTPS al host `graph.instagram.com`, ruta sin versión `/refresh_access_token`. Exige **ambos parámetros en la cadena de consulta**: `grant_type=ig_refresh_token` y `access_token` con el token de larga duración válido, sin caducar y de al menos 24 horas de antigüedad. Requiere `instagram_business_basic`. La respuesta JSON contiene `access_token`, `token_type=bearer` y `expires_in` (segundos restantes); el plazo renovado es de 60 días desde la renovación. La referencia no documenta Bearer como sustituto del parámetro obligatorio; no se presupone ni se afirma que Meta rechace cualquier uso de Bearer.

| Aspecto | Contrato implementado y evidencia pendiente |
| --- | --- |
| Larga duración | La referencia de renovación confirma token largo sin caducar, al menos 24 horas de edad, permiso `instagram_business_basic` y plazo renovado de 60 días. No se asignan 60 días a un token importado. |
| Panel de Meta | `dashboard` es una declaración del operador de que se generó en el apartado de **Instagram API with Instagram Login**. No incluye cualquier token de Graph API Explorer u otros productos. No es prueba técnica del tipo, edad, vigencia o renovabilidad del token concreto. La afirmación de que esos tokens se emiten directamente por 60 días queda pendiente de reconfirmación oficial; por ello importar nunca crea una expiración. |
| Endpoint | `GET https://graph.instagram.com/refresh_access_token`, sin versión, `grant_type=ig_refresh_token`. No se usa `fb_exchange_token`, un refresh token OAuth separado ni un app secret. |
| Transporte de la credencial | Solo renovación usa `access_token` en la consulta, conforme al parámetro obligatorio oficial, sin cabecera Authorization. Las lecturas conservan Bearer. El destino relativo se construye exclusivamente en memoria al enviar la petición; no se construye, devuelve, registra ni persiste una URL completa con el secreto. No hay fallback ni reintento. |
| Respuesta de renovación | Se exige `access_token` válido, `token_type=bearer` y `expires_in` entero positivo, como máximo 60 días. Se verifica nuevamente `/v26.0/me` con el token recibido. Solo una respuesta aceptada y una identidad coincidente permiten sustituir la credencial. |
| Expiración ya generada | `/me` solo prueba identidad/utilidad en ese instante. No da edad, tipo, permisos completos ni expiración. No se ha confirmado una introspección fiable para este flujo y token; no se implementa `debug_token` de Facebook ni se solicita un app secret para adivinarlo. Esto **no afirma que Meta carezca de cualquier herramienta**: si el panel/Debugger oficial proporciona una fecha explícita aplicable al token, el operador puede registrarla. |
| Revocación/caducidad remota | Código 190 o HTTP 401 → `authentication_required`, reautorizar manualmente. No se infiere una causa precisa a partir de mensajes ni se considera que renovar resuelva una revocación. La correspondencia exhaustiva de subcódigos de Instagram Login queda pendiente de reconfirmación; no se copia una tabla de Facebook como garantía. |
| Permisos | Códigos 10/200 → `instagram_permissions_required`, revisar permisos y reautorizar si procede. HTTP 403 en identidad impide continuar. Los rechazos individuales de Insights sin esos códigos conservan el comportamiento parcial de I4-A2; nunca acreditan acceso completo. `/me` no demuestra `instagram_business_manage_insights`. |

La prueba de I4-A2 con Instagram Login y Graph v26.0 aportada por el usuario acredita las lecturas ya realizadas. No acredita `refresh_access_token` ni una fecha de expiración. No se ejecutó una renovación real. Véase el incidente de aislamiento de la primera suite de esta revisión en la sección de verificación.

## Almacén y seguridad

`WindowsCredentialStore` cifra con **DPAPI del usuario de Windows**, `CryptProtectData`/`CryptUnprotectData`, sin `CRYPTPROTECT_LOCAL_MACHINE` y con interfaz de usuario prohibida. Usa únicamente `ctypes` y la biblioteca estándar. El archivo predeterminado es:

```text
%LOCALAPPDATA%\HelmOS\credentials\instagram.dpapi
```

Token y metadatos forman un único sobre cifrado. Se cifra en memoria antes de crear el temporal, se escribe el temporal cifrado en el mismo directorio, se hace `flush`/`fsync` y se sustituye mediante `os.replace`. Un fallo anterior al reemplazo conserva el archivo previo; se elimina el temporal. No se crean archivos con el token en texto plano, ni secretos en repositorio, estado Social, `var/runs` o informes. DPAPI requiere el perfil del mismo usuario de Windows; no se promete portabilidad a otro usuario, equipo o VPS ni protección frente a procesos maliciosos que ya ejecuten como ese usuario. Los secretos necesariamente existen en memoria del proceso; Python no garantiza su borrado inmediato.

El protocolo `CredentialStore` (`load`, `save`, `transaction`) separa la gestión del backend. Permite sustituir el almacén en un futuro servidor; no hay backend de VPS ni fallback a texto plano. El contrato exige sustitución atómica y exclusión entre escritores.

Un archivo `instagram.lock`, sin secretos, excluye importación/check/refresh/sync simultáneos durante la operación de credenciales. Si otro proceso está activo se devuelve `credential_store_busy`. Una terminación abrupta puede dejar el bloqueo: comprobar primero que **no hay ningún proceso HelmOS ejecutándose** y retirar únicamente ese `.lock` en el directorio indicado. No borrar el `.dpapi`. Un estado ilegible/corrupto falla cerrado; no se sustituye automáticamente por otro token.

La variable `HELMOS_INSTAGRAM_ACCESS_TOKEN` conserva precedencia explícita sobre el almacén para compatibilidad con I4-A2. Una credencial de entorno se valida contra la cuenta esperada, pero su tipo/expiración son desconocidos y **no se renueva ni se guarda automáticamente**: `import_required_for_refresh`. Evita que una renovación guardada quede ocultada por el token antiguo del entorno. Tras importar hay que retirar la variable del proceso que ejecutará HelmOS; el comando comunica `environment_override` cuando sigue presente. No hay carga automática de `.env`.

No hay token en argumentos CLI, URLs completas expuestas, representaciones de objetos, logs, mensajes remotos ni errores impresos. La excepción contractual es la consulta de renovación transmitida dentro de TLS: existe en memoria, nunca en archivos ni diagnósticos. HTTP usa TLS directo al host fijo `graph.instagram.com`, sin redirects, proxies de entorno, logging de cabeceras ni reintentos. Se fuerza `debuglevel=0`. Los fallos de transporte, lectura y cierre se convierten en códigos seguros, sin conservar la excepción original como contexto o causa. Mantiene los límites de tamaño, tiempo y peticiones de I4-A2; identidad inicial, renovación y segunda identidad consumen el **mismo** presupuesto (tres peticiones si se renueva). Las respuestas y cabeceras completas nunca se persisten. No activar depuradores HTTP, transcripciones de entrada, trazas con variables locales, hooks de auditoría que capturen solicitudes ni volcados de memoria durante operaciones de secretos. El código protege sus propias salidas; no puede impedir que instrumentación externa inspeccione memoria ni controlar registros del servidor de Meta.

## Política de renovación

1. Importar comprueba primero la cuenta profesional y el username esperado; no reemplaza nada si falla. Guarda ID, username, tipo/origen declarado, primera observación y fecha de comprobación. `issued_at` y `expires_at` solo se registran si se aportan expresamente con zona horaria y son coherentes; `expiry_source=operator` distingue ese dato de una respuesta de Meta. Por defecto son desconocidos.
2. Un token `unknown` o `short-lived` no se renueva. `long-lived`/`dashboard` son declaraciones de procedencia del operador, no renovabilidad certificada por `/me`.
3. Deben haber pasado al menos 24 horas desde la emisión conocida o, si se desconoce, desde la primera observación guardada. La observación es un límite conservador de edad y **no una fecha de emisión inventada**. Sin edad suficiente no se llama al endpoint.
4. Con expiración conocida, solo se renueva si quedan siete días o menos y aún no ha caducado. Esta ventana es una política local. También `refresh` respeta este límite; no fuerza renovaciones innecesarias.
5. Con expiración desconocida, sync/preflight no renuevan automáticamente. Se necesita un primer `refresh` explícito y procedencia declarada larga duración/panel. Se comprueba identidad antes del intento; Meta sigue siendo quien acepta o rechaza la elegibilidad. Un token que ya caducó no se recupera mediante renovación.
6. Un éxito registra `expires_at = inicio de la petición + expires_in`: es un límite conservador de uso, sin sumar tiempo de red. No se supone siempre 60 días. `last_refreshed_at` y `observed_at` registran la comprobación posterior; `issued_at` queda desconocida, porque Meta no devuelve ese instante. La nueva observación obliga a esperar otras 24 horas. `expiry_source=meta_refresh` acredita la procedencia del plazo.
7. Si falla HTTP, parsing, validación, identidad o almacenamiento, **no se guarda el sobre nuevo ni se modifica el anterior**. No se escribe Social. Un timeout puede haber ocurrido después de que Meta procesara la renovación; conservar el archivo local no garantiza que Meta mantenga válido el anterior. No se reintenta automáticamente; comprobar y, si es necesario, reautorizar.
8. Tras un éxito, repetir `refresh`/sync no renueva mientras no corresponda. Una comprobación exitosa puede actualizar `checked_at`; idempotencia se refiere a no repetir la renovación remota ni añadir cambios sociales innecesarios. `status` es completamente local y no escribe ni certifica validez remota.

Sync conserva métricas, mapping e histórico. La preparación ocurre antes de lecturas de medios/escrituras; un rechazo de autenticación o los códigos de permisos anteriores durante Insights aborta el lote completo antes de persistir. Los comandos de credenciales no crean ejecuciones Core. Preview/preflight no escriben Social; **sí pueden comprobar, renovar y guardar credenciales** conforme a esta política.

## CLI implementada y ensayo real controlado

Desde PowerShell interactivo en la raíz, con el usuario Windows que ejecutará las futuras sincronizaciones:

```powershell
python -m helmos social credentials instagram import --api-version v26.0 --expected-username JorgeBytes --kind dashboard
```

Pegar el token existente exclusivamente en la entrada oculta. No ponerlo en comandos, archivos JSON, prompts del agente ni tuberías. `import` usa `getpass` y rechaza stdin redirigido/fallback que pudiera mostrarlo. Elegir `dashboard` solo tras confirmar su procedencia Instagram Login. Las otras opciones son `--kind long-lived`, `short-lived` y `unknown` (defecto). Si no se conoce la procedencia, conservar `unknown` hasta comprobarla. Opcionales **solo para importación**: `--issued-at` y `--expires-at`, ambos ISO 8601 con zona horaria. No reconstruir fechas a partir de que `/me` haya funcionado ni aportar una fecha de hace 24 horas para saltar el control.

Si sigue presente en la sesión, retirar únicamente la variable de proceso, sin imprimirla:

```powershell
Remove-Item Env:HELMOS_INSTAGRAM_ACCESS_TOKEN -ErrorAction SilentlyContinue
python -m helmos social credentials instagram status --api-version v26.0 --expected-username JorgeBytes
python -m helmos social credentials instagram check --api-version v26.0 --expected-username JorgeBytes
```

`status` muestra estado local, origen, fechas conocidas/desconocidas, última comprobación y motivo de renovación. `check` consulta identidad y guarda solo los metadatos seguros de comprobación; no renueva. Ninguno revela el valor. Si la variable estaba configurada persistentemente fuera de esta sesión, debe retirarse también mediante el mecanismo que la suministra; no guardar una versión obsoleta para próximos procesos.

Para una futura renovación real autorizada, usar el mismo usuario Windows y una terminal interactiva sin transcripción, captura de variables locales ni instrumentación HTTP. Confirmar la procedencia Instagram Login y el permiso requerido; si la credencial no está importada, usar exclusivamente la entrada oculta de `import` descrita arriba. No usar cURL, navegador ni una URL manual con el token. Consultar primero `status` y `check`. Si falta fecha de emisión conocida, esperar 24 horas reales desde la importación; no falsificarla. Si existe expiración conocida con más de siete días restantes, el comando será un no-op por política:

```powershell
python -m helmos social credentials instagram refresh --api-version v26.0 --expected-username JorgeBytes
python -m helmos social credentials instagram status --api-version v26.0 --expected-username JorgeBytes
python -m helmos social credentials instagram check --api-version v26.0 --expected-username JorgeBytes
```

Revisar `action=refreshed`, `expiry_source=meta_refresh`, una expiración futura y la misma cuenta. Repetir `refresh` debe mostrar `checked` con `minimum_age_not_reached`/`not_due`, sin otra renovación. Si el endpoint no acepta Bearer, no sustituirlo por una llamada con secretos en URL: conservar la credencial y revisar la referencia oficial. Código 190/caducidad → reautorizar manualmente desde Instagram Login; permisos → revisar concesiones `instagram_business_basic`/`instagram_business_manage_insights` y rol/acceso antes de volver a importar. `metric_or_parameter_rejected` no se etiqueta automáticamente como revocación: puede ser un token no elegible o un contrato rechazado y requiere revisión.

Comprobar una sincronización mínima sin escribir Social:

```powershell
python -m helmos social credentials instagram preflight --api-version v26.0 --expected-username JorgeBytes
python -m helmos --state-dir var social sync instagram --api-version v26.0 --expected-username JorgeBytes --preview
```

`preflight` ejecuta el sync existente con preview, una página, un medio y diez peticiones como máximo (incluye las tres de credenciales si hay renovación y seis Insights). Es una comprobación acotada, no prueba de todos los medios ni garantía de que una ejecución posterior no sea revocada. Si no hay medios no puede probar Insights. Examinar `partial_errors`, `bounded_listing` y `posts_consulted`; no interpretar ausencia de medios como permisos completos. Códigos de salida: 0 si termina (incluido refresh no-op; consultar `action`/`refresh_reason`), 1 si preflight/sync tiene métricas parciales, 2 si falla. `status` con ausencia devuelve `source=absent` y `reauthorization_required=true`, sin red.

El comando de escritura de sincronización existente sigue disponible retirando `--preview`; para un ensayo de escritura usar un `--state-dir` nuevo y separado del histórico habitual conforme a I4-A2. La validación real se realiza manualmente después de esta entrega, nunca como parte de las pruebas automatizadas.

## Verificación y estado

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Se conservan las 118 pruebas anteriores a I4-B1, corrigiendo el aislamiento de una de ellas como se explica abajo. I4-B1 cuenta con 31 pruebas con secretos ficticios, HTTP simulado y directorios temporales: estados, revocación/caducidad/permisos, fechas desconocidas, origen no renovable, límites de edad/ventana, renovación fallida, identidad posterior, presupuesto compartido, sustitución fallida, bloqueo, integración sync/CLI, ausencia de fugas en logs/salidas/tracebacks/JSON y cifrado DPAPI nativo en Windows. La revisión del contrato adapta pruebas de renovación y añade cinco: query codificada y lecturas Bearer, rechazo de 301/302/303/307/308, fallos de envío/recepción/lectura/cierre sin contexto secreto, serialización real de stdlib con socket simulado y debug previamente activado, y rechazo de destinos/parámetros arbitrarios por la interfaz pública. Las pruebas de renovación usan exclusivamente tokens ficticios y no llaman a Meta.

Incidente de aislamiento: la primera suite ejecutó `test_missing_token_invalid_limits_and_disallowed_requests_use_no_network`, una prueba heredada que vaciaba el token del entorno pero no simulaba el nuevo fallback al almacén DPAPI ni bloqueaba HTTPS. Llegó al transporte real de identidad `/v24.0/me` y devolvió `api_version_mismatch`. Por ello no se puede afirmar que esa ejecución no leyera una credencial local ni hiciera una llamada autenticada, pese a la restricción del encargo. No solicitó renovación. Se corrigió la prueba simulando `WindowsCredentialStore.load` con resultado ausente y el constructor HTTPS, y comprobando que no se invoca. No se inspeccionó ni imprimió el valor del secreto para investigar el fallo.

Resultado de esta revisión en Windows: **149 pruebas correctas**, incluidas las 118 anteriores y DPAPI nativa; `git diff --check` sin errores. Las 31 pruebas de credenciales también pasan por separado tras añadir la comprobación de JSON inválido sin contexto de excepción. Una repetición combinada con otros comandos encontró permisos de sandbox sobre temporales; la repetición aislada con el permiso de pruebas aprobado pasó. No se ejecutó la renovación real controlada descrita arriba.

**Estado:** contrato oficial de renovación reconfirmado y transporte adaptado. La renovación real controlada con Instagram Login queda pendiente; no se declara acreditada la operación desatendida real. Los otros límites documentales indicados siguen vigentes. No hay commit, push, I4-B2 ni cierre de V1.0.
