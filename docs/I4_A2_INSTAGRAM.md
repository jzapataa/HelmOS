# I4-A2 solicitado — Instagram Login real

Actualización I4-B1 (26-10-2026): la evolución explícitamente autorizada añade almacén DPAPI y gestión de renovación. [Contrato, precedencia de entorno, comandos y límites actuales](I4_B1_INSTAGRAM_CREDENTIALS.md). Las descripciones de «solo entorno / sin renovación» de este documento describen I4-A2 y quedan ampliadas por I4-B1.

Rama: `feature/i4-a-social-connectors`. Incremento autorizado expresamente por el usuario, posterior a I4-A1 y separado del cierre V1.0 y de Personal. No modifica las especificaciones originales ni inicia I4-B.

## Comando y límites

```powershell
python -m helmos --state-dir var social sync instagram --api-version VERSION_CONFIRMADA --expected-username JorgeBytes --preview
```

`VERSION_CONFIRMADA` debe sustituirse por la versión Graph que soporte la aplicación en Meta, con formato `vNN.0`. No existe versión predeterminada ni selección de `latest`. `v24.0` aparece exclusivamente como configuración ficticia en las pruebas; no certifica su vigencia. Todas las rutas reales incluyen la versión elegida. Si Meta devuelve `Instagram-API-Version` o `Facebook-API-Version` y difiere de la solicitada, se detiene con `api_version_mismatch`. La ausencia de esas cabeceras no acredita la versión efectiva.

Opciones: `--metadata RUTA.json`, `--page-size` (1–100, defecto 5), `--max-pages` (1–1000, defecto 1), `--max-posts` (1–10000, defecto 5), `--max-requests` (2–1000, defecto 100, incluye identidad e Insights), `--timeout` (segundos por operación de socket, >0 y ≤30, defecto 10), `--deadline-seconds` (presupuesto de red comprobado entre peticiones y bloques de lectura, >0 y ≤600, defecto 120). Respuesta máxima: 1 MiB por petición. No hay reintentos automáticos ni sincronización programada.

Se consulta `GET /VERSION/me?fields=user_id,username,account_type`, se comprueba cuenta profesional y coincidencia de usuario sin distinguir mayúsculas, y se deriva la identidad del ID devuelto. Solo se lista `GET /VERSION/me/media` con `id,timestamp,media_type,media_product_type`. Se reconstruye la paginación con `after`: nunca se sigue ni guarda `paging.next`. El transporte solo permite GET en rutas de identidad, medios propios e Insights de IDs descubiertos en ese listado. No solicita publicación, mensajes, captions ni datos de seguidores.

Cada contenedor FEED (IMAGE, VIDEO, CAROUSEL_ALBUM) o REELS (VIDEO) consulta por separado `views`, `reach`, `likes`, `comments`, `shares` y `saved`. Se conserva `saved → saves`. No se solicitan `plays`, `impressions`, métricas exclusivas de Stories ni tiempos de reproducción. Stories y formatos no soportados detienen el lote para revisión. La disponibilidad efectiva también depende de versión, antigüedad, contenido y permisos; un rechazo individual queda en `partial_errors` y la métrica permanece ausente. Solo se aceptan contadores escalares enteros no negativos, `period=lifetime`; arrays vacíos y null son ausencia, y cero explícito es cero.

Un error de métrica (incluidos HTTP 400, 403, 5xx o estructura inválida) permite continuar con las demás métricas y se comunica. Autenticación (incluido código Meta 190 en HTTP 400/200), 429, transporte, presupuesto agotado, redirección y discrepancia de versión detienen el lote antes de persistir. Un error de listado también detiene el lote. Se conserva `Retry-After` numérico ante 429 para una repetición manual posterior. No se imprimen cuerpos, mensajes remotos, cabeceras completas ni URLs.

El resumen JSON contiene `posts_consulted` (IDs únicos procesados), `new_posts`, `existing_posts`, `posts_imported`, `observations_saved` (incluye la inicial de una publicación nueva), `observations_planned`, `unchanged_posts`, `duplicates_skipped`, `partial_errors`, `unclassified_posts`, `captured_at` UTC, cuenta y versión. `bounded_listing=true` indica parada deliberada por límite de páginas/publicaciones: **no es una sincronización completa de toda la cuenta**. Agotar peticiones o tiempo es un error fatal, no un lote limitado aceptado. Salidas CLI: 0 si termina sin errores de métricas, 1 si hay errores parciales (puede haber escrituras), 2 si falla. La salida 0 en preview puede incluir publicaciones pendientes de clasificación y no implica importación.

## Identidad editorial e histórico

La sincronización admite publicaciones sin clasificación editorial tanto en preview como en escritura. Las nuevas sin metadatos se guardan con `classification_status=pending`, `topic=null`, `format=null` y `provenance.editorial_piece_id=null`: son valores desconocidos, no un tema ni una identidad editorial compartida. Se incluyen sus métricas en `observations_planned`, `preview_posts` y, al guardar, en el histórico canónico. `unclassified_posts` sigue mostrando IDs, tipo remoto, fecha y métricas pendientes de clasificación; ya no significa que se haya descartado su observación. El tipo remoto no se convierte automáticamente en formato editorial.

Para clasificar publicaciones **nuevas** antes de su primera escritura se puede aportar JSON local, sin credenciales, con esta estructura (ID y contenido ficticios):

```json
{
  "123": {
    "topic": "Python",
    "format": "video",
    "editorial_piece_id": "pieza-ejemplo"
  }
}
```

Para todas las publicaciones existentes se reutilizan tema y formato persistidos, aunque procedan de CSV y no tengan referencia editorial. Se conserva la referencia editorial del histórico cuando existe; en caso contrario permanece null, sin inventarla a partir del ID oficial. Las existentes pendientes siguen admitiendo nuevas observaciones. Para conciliar deben coincidir ID oficial, día UTC de publicación, tema y formato. IDs manuales diferentes no se fusionan automáticamente. Los metadatos suministrados incompatibles, cambio de cuenta o referencia editorial provocan conflicto antes de escribir; el fichero de publicación existente se conserva byte a byte. `--metadata` no reclasifica ni reescribe publicaciones existentes; la edición de una clasificación pendiente queda fuera de esta corrección.

`social.import` mantiene su validación CSV y sus reglas de conflicto; no sustituye clasificaciones existentes. `social.analyze` conserva métricas y nulls en su dataset, comunica la clasificación pendiente como limitación y no debe inferir temas ni atribuir rendimiento por tema/formato desconocido. Los informes muestran «tema pendiente»/«formato pendiente» como etiquetas de presentación, sin almacenarlas como clasificación. No se migran datos previos.

La causa corregida era doble: `sync_instagram` solo recuperaba clasificación si encontraba `editorial_piece_id` en la procedencia; después `SocialConnector.collect` descartaba con `continue` cualquier publicación sin ese paquete completo. Además, la escritura rechazaba todo lote con publicaciones sin clasificar. Ni la especificación V1.0 ni las reglas de datos de Social exigen clasificación editorial para conservar una observación métrica válida; esta corrección sigue dentro del alcance I4-A2 autorizado.

Se reutiliza `_persist_posts` de `social_import.py`, el directorio histórico existente y las vistas de `social_history.py`. Cada observación contiene día UTC, `measurement_type=cumulative`, métricas presentes y procedencia con cuenta, versión, mapping, referencia editorial y `captured_at` UTC completo. No se guardan respuestas crudas, tokens, cabeceras, URLs ni archivos temporales con datos de API.

I4-A2 habilita capturas API distintas dentro del mismo día: si cambia el conjunto de métricas frente a la última observación se añade otra, incluso si las cifras vuelven a un valor anterior. Mismo instante con valores diferentes es conflicto; repetición del mismo instante/medición es idempotente. Mismo día y métricas sin cambios respecto a la última observación no añade fichero; otro día conserva la captura diaria aunque coincidan los valores. Un fallo parcial puede producir un conjunto menor de métricas: se guarda lo observado sin rellenar ausencias con cifras anteriores. Las vistas de último valor conservan el último valor conocido por métrica y ordenan por día/hora API. `observed_at` sigue siendo un día: para distinguir capturas intradiarias consultar `provenance.captured_at` en `social observations`.

CSV y adaptadores simulados mantienen su contrato diario: no admiten cambios ambiguos en el mismo día. Una observación CSV sin hora en el mismo día que una API con métricas diferentes también exige conciliación explícita. No se migran ni reescriben archivos históricos. Solo un proceso escritor; no ejecutar CSV/sync simultáneamente. Se prevalidan conflictos del lote, pero un fallo de disco durante escrituras puede dejar parte guardada: repetir con cuidado; no hay transacción global ni garantía de concurrencia.

## Acceso, evidencia y prueba real controlada

La única credencial leída es `HELMOS_INSTAGRAM_ACCESS_TOKEN`, desde el entorno del proceso. No hay carga de `.env`, lectura de otros secretos, OAuth ejecutable, renovación ni persistencia de credenciales. Se envía como `Authorization: Bearer …` a `graph.instagram.com` mediante TLS de la biblioteca estándar, sin redirecciones ni logging HTTP. No pasar el token en argumentos, prompts, JSON o comandos que lo impriman.

El usuario ha confirmado autorización de JorgeBytes y pruebas reales de `/me`, `/me/media` e Insights `views,reach,saved,shares` en un Reel. Las cifras observadas no se incorporan como constantes de producción. Esta entrega no ejecutó llamadas autenticadas ni inspeccionó el token: la integración con la cuenta real queda pendiente de la prueba siguiente.

Antes del ensayo, comprobar en el panel de Meta que la aplicación usa Instagram Login, cuenta Business/Creator y permisos de lectura `instagram_business_basic` e `instagram_business_manage_insights`. Verificar el rol/acceso de la cuenta propia (Standard Access) o las condiciones de Advanced Access/App Review para cuentas ajenas, así como versión soportada y matriz de métricas. No añadir permisos de escritura para corregir una denegación. `/me` valida identidad y tipo; la capacidad Insights se verifica por las lecturas de cada métrica, no se infiere de tener un token.

Referencias primarias a revisar: [Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/), [Media Insights](https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-media/insights/), [Insights con Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/insights/), [colección oficial de Meta](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login). Comprobación documental del 26-10-2026: las referencias directas devolvieron 429/inaccesible y Postman no expuso la referencia técnica completa; **no se presenta su contrato vigente ni una versión como reconfirmados**. La selección explícita y el manejo de rechazos reducen el riesgo, pero no sustituyen esa comprobación en el panel.

1. Tener la variable del token ya disponible en la sesión mediante el mecanismo local autorizado, sin imprimirla. Elegir la versión confirmada en Meta y asignarla a `$instagramVersion` (solo contiene la versión, nunca la credencial).
2. Ejecutar una primera lectura mínima, sin tocar datos existentes:

   ```powershell
   python -m helmos --state-dir var social sync instagram --api-version $instagramVersion --expected-username JorgeBytes --preview --page-size 1 --max-pages 1 --max-posts 1 --max-requests 8
   ```

   Máximo ocho peticiones: identidad, listado y seis Insights. Preview no crea ni actualiza el directorio de estado ni registros Core. Revisar cuenta, fecha, IDs sin clasificar, límites y errores. Para probar específicamente un Reel, ampliar el listado de forma controlada hasta que aparezca uno; no hay selección por ID que eluda la comprobación de propiedad.
3. Revisar clasificación pendiente, conflictos y observaciones previstas. Opcionalmente preparar clasificación humana de publicaciones nuevas, por ejemplo en `var/instagram-editorial.json` (fuera de Git), y repetir con `--metadata var/instagram-editorial.json --preview` antes de guardarlas. Las ya existentes reutilizan sus metadatos sin necesitar ese archivo. No redirigir respuestas HTTP crudas a archivos.
4. Si se decide validar escritura, elegir **un directorio de ensayo nuevo** con `--state-dir var/instagram-i4a2-trial` y repetir el comando con metadata y sin `--preview`. No usar el estado existente para el ensayo. Repetir permite verificar idempotencia; una captura posterior con cifras distintas debe añadir observación sin modificar la publicación inicial. Consultar con `social observations --platform Instagram --post-id ID` y `social latest --platform Instagram --post-id ID`, usando el mismo `--state-dir` antes de `social`.

## Verificación local

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Las pruebas nuevas sustituyen HTTP y reloj cuando corresponde, usan credenciales ficticias y directorios temporales. Cubren el recorrido CLI→HTTP→modelo→preflight, paginación, límites, Insights, ausencia/cero, HTTP/token caducado, redirecciones, tamaño, identidad, duplicados, metadatos e histórico intradiario. No invocan API ni IA reales. No se hacen commits, push ni tareas programadas. TikTok y LinkedIn conservan el alcance de I4-A1.
