# I4-A1 solicitado — preparación de conectores oficiales

Investigación: 26 de octubre de 2026. Rama: `feature/i4-a-social-connectors`.

Preparación local implementada y contrastada con mocks; ninguna integración autenticada ni permiso de producción verificados. No es un cierre de V1.0. Véase la [propuesta separada](PROPOSAL_I4_A_SOCIAL_CONNECTORS.md) sobre alcance y numeración; la especificación aprobada permanece intacta.

## Viabilidad y evidencia

| Plataforma | Confirmado en fuentes oficiales consultadas | Acceso y bloqueos | Decisión del incremento |
| --- | --- | --- | --- |
| Instagram | Business y Creator mediante Instagram Login, sin exigir página Facebook vinculada. Alternativa Facebook Login sí necesita página. [Colección oficial de Meta](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login), [alternativa](https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login). | No se ha comprobado el tipo de cuenta JorgeBytes, roles, scopes ni acceso concedido. Las referencias directas actuales de Insights/OAuth devolvieron 429 o página inaccesible en esta investigación. No se presenta su matriz de métricas/versiones como verificada de extremo a extremo. | Adaptador de contrato provisional para feed y Reels. Validar referencia actual y permisos en el dashboard antes de cualquier activación. |
| TikTok | Display API permite leer vídeos **públicos propios** con `video.list`; perfil con `user.info.basic`. [Overview](https://developers.tiktok.com/docs/en/display-api-overview), [listado v2](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list). | Revisión de aplicación necesaria. Las directrices excluyen apps de uso privado/personal: bloqueo sustancial para HelmOS tal como existe. [App Review](https://developers.tiktok.com/docs/en/app-review-guidelines). Sandbox no equivale a producción. | Adaptador v2 con métricas del Video Object, sin afirmar que el caso de uso será aprobado. Mantener CSV. |
| LinkedIn | Sí existe analítica de **miembros**, además de páginas: `memberCreatorPostAnalytics`, por publicación conocida o agregado del miembro, con `r_member_postAnalytics`. [Estadísticas de miembros](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/post-statistics?view=li-lms-2026-09). | Community Management está sujeto a vetting. La lectura de publicaciones mediante `r_member_social` está cerrada a nuevas solicitudes. [Overview y FAQ](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-08). No basta con tener una cuenta o Sign In. | Gate explícito `linkedin_member_access_unavailable`; sin endpoints, tokens ni mapper de métricas en producción. CSV continúa siendo la vía disponible. |

### Instagram: contrato pendiente de comprobación completa

La ruta elegida es Instagram API with Instagram Login. Solicitar únicamente `instagram_business_basic` y, sujeto a confirmación en la referencia/dashboard vigente, `instagram_business_manage_insights`; no copiar todos los permisos de ejemplos de Postman. Referencias que se intentaron consultar: [inicio](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/), [Insights](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/insights/), [media Insights](https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-media/insights/), [Business Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/).

El diseño presupone OAuth de autorización del titular, intercambio del código en backend, token corto intercambiable por uno largo y renovación de ese token. Los plazos habituales de referencia son corto ~1 hora, largo ~60 días y renovación tras al menos 24 horas mientras siga vigente; **no quedaron reconfirmados aquí desde la referencia oficial actual** y no se codifican. Deben leerse `expires_in`, el contrato de renovación y las condiciones de revocación al habilitar la aplicación; una credencial expirada/revocada requiere nueva autorización. No se ha implementado OAuth ni refresh.

Standard Access para cuentas propias con roles de aplicación y Advanced Access/App Review para cuentas fuera de esos roles son condiciones que deben confirmarse en el dashboard actual; verificar también si exige verificación del negocio. Ser propietario no implica automáticamente tener acceso de producción.

El adaptador prepara `GET /{version}/{ig-user-id}/media` y `GET /{version}/{media-id}/insights` en `graph.instagram.com`. Requiere versión explícita: `v24.0` en tests es un ejemplo ficticio de configuración, no una certificación de vigencia. La combinación de métricas por tipo, las retiradas de métricas y la disponibilidad histórica requieren contrastar la referencia de esa versión. No se solicita `impressions` ni `plays` ni se reinterpretan como `views`.

Solo acepta contenedores FEED/REELS (IMAGE, VIDEO, CAROUSEL_ALBUM); no Stories, directos ni hijos de carrusel. Solo transforma valores enteros escalares con `period=lifetime`. Arrays vacíos y valores nulos no se convierten en cero. Un tipo/formato inesperado o una métrica rechazada por la API aborta el lote para revisión, sin retirar silenciosamente permisos ni introducir otra API.

### TikTok: alcance técnico y elegibilidad

`POST /v2/video/list/` es una operación de lectura paginada; máximo 20 vídeos por petición. `has_more` y `cursor` controlan la continuación. Para IDs ya conocidos existe [video/query](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query), que verifica pertenencia al usuario; este incremento usa el listado. La [definición oficial Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object) ofrece los cuatro contadores implementados. No expone favoritos, alcance, impresiones, guardados o tiempo de visionado mediante este contrato Display; no se usan campos de Research API.

El flujo es Login Kit OAuth con código, `state` comprobado y redirect idéntico al registrado. Intercambio y renovación por `POST https://open.tiktokapis.com/v2/oauth/token/`, grants `authorization_code` y `refresh_token`. Access token: 24 horas; refresh: 365 días desde emisión inicial. Respetar expiraciones devueltas y conservar el nuevo refresh token cuando rote; revocación/caducidad exige reautorizar. [Gestión oficial de tokens](https://developers.tiktok.com/docs/en/oauth-user-access-token-management).

El límite publicado para video/list, video/query y user/info es 600 peticiones/minuto por endpoint, ventana móvil; 429 y `rate_limit_exceeded` al excederlo. No es un presupuesto garantizado para nuestra app. [Rate Limits](https://developers.tiktok.com/docs/en/tiktok-api-v2-rate-limit).

### LinkedIn: no confundir analítica con descubrimiento

La referencia de analítica de miembros permite obtener IMPRESSION, MEMBERS_REACHED, REACTION, COMMENT y RESHARE de una publicación conocida o agregados. Documenta también métricas adicionales según versión. Tener `r_member_postAnalytics` no demuestra acceso al finder de publicaciones por autor ni concesión de `r_member_social`. Los agregados de miembro tampoco sustituyen observaciones por publicación.

El [proceso oficial de revisión](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review?view=li-lms-2026-06) limita Community Management a organizaciones legales registradas y usos comerciales, requiere email de negocio, sitio/política de privacidad y aplicación verificada por un superadministrador de la página asociada. Development y Standard necesitan aprobación; el uso personal local actual no acredita elegibilidad. La revisión Standard requiere demostración. No solicitar escritura para superar la revisión: si piden una capacidad incompatible con el alcance, se registra el bloqueo. No se usa el antiguo supuesto de que LinkedIn solo ofrece métricas de páginas.

## Contrato local y persistencia

`helmos.social_connectors` usa biblioteca estándar y proveedor inyectado; no existe cliente de red real ni ruta nueva en Core/CLI. Un `Account(platform, account_id)` identifica la cuenta **ya autenticada** por el proveedor. No sirve como prueba de autenticación por sí mismo. El futuro proveedor deberá derivarlo de OAuth/identidad oficial y verificar la vinculación antes de entregar respuestas.

- `AuthorizedProvider.request(method, path, params=..., body=...) -> Response(status, body, headers)`: proveedor ligado a cuenta, host permitido y scopes de lectura. Es quien custodiaría secretos, TLS, timeouts, límites de tamaño y ausencia de redirecciones/logging sensible. En I4-A1 solo se sustituye por mocks.
- `InstagramConnector(provider, api_version=..., max_requests=100)` y `TikTokConnector(provider, max_requests=100)` exponen `collect(metadata, captured_at=...) -> list[dict]` e `import_snapshot(state_dir, metadata, captured_at=...) -> dict`.
- `collect` no escribe. `import_snapshot` recopila y valida el lote completo antes de llamar al escritor común `_persist_posts` de `social_import.py`; no genera CSV temporal ni duplica persistencia. `social_history.py` lee las mismas observaciones sin cambios.
- `metadata` relaciona ID oficial con `EditorialMetadata(topic, format, editorial_piece_id)` aportado por una persona. Falta de clasificación aborta; no se inventan temas a partir de captions. Para conciliar con CSV deben coincidir ID oficial, fecha UTC, formato y tema existentes; IDs manuales distintos requieren conciliación posterior explícita, sin fusiones automáticas.
- Instagram y TikTok usan el mismo `editorial_piece_id` para el mismo vídeo y conservan `(platform, post_id)` y métricas distintas. LinkedIn deberá usar una identidad editorial independiente. Esto conserva el vínculo explícito en la procedencia; no añade consolidación ni modifica informes editoriales existentes.
- Cada observación contiene `observed_at` (día UTC), `measurement_type=cumulative`, periodos nulos, métricas presentes y `provenance`: `source=official_api`, plataforma, cuenta, versión, `captured_at` UTC completo, referencia editorial y mapping de métricas. No se guardan respuestas crudas, captions, cabeceras ni URLs `next`.
- Se conserva la granularidad diaria existente. Mismo día/métricas: idempotente, conserva la primera procedencia. Mismo día con valores distintos: conflicto, sin sobrescribir. Día nuevo: observación adicional. No se introducen capturas múltiples al día ni migraciones destructivas. La procedencia adicional es opcional para coexistir con CSV y registros antiguos.
- Conflictos de metadata, cuenta o identidad editorial API se revisan antes de escribir. Un error remoto o de validación no deja publicaciones parciales. Cada archivo mantiene la escritura existente; un fallo de disco durante la fase de escritura puede dejar parte del lote, recuperable mediante repetición idempotente. No se promete transacción de lote.
- Un único proceso escritor, también frente al importador CSV; no ejecutar importaciones en paralelo.

### Métricas del adaptador

| Plataforma | Campo oficial → campo HelmOS | Estado |
| --- | --- | --- |
| Instagram | `views → views`, `reach → reach`, `likes → likes`, `comments → comments`, `shares → shares`, `saved → saves` | Transformación simulada; combinación por feed/Reels y versión pendiente de reconfirmación oficial por bloqueo documental. |
| TikTok | `view_count → views`, `like_count → likes`, `comment_count → comments`, `share_count → shares` | Contrato Display v2 contrastado; aplicación no aprobada. |
| LinkedIn | Ninguno implementado | Gate de acceso. No sustituir reacciones por likes ni inventar métricas. |

Métrica ausente/nula permanece ausente, cero explícito se conserva. Valores negativos, booleanos, texto y estructuras inesperadas se rechazan. Los contadores cumulativos no se suman entre capturas; las vistas históricas existentes los comparan por plataforma/publicación.

### Paginación y errores

Presupuesto por recopilación configurable de 1–1000 peticiones (100 por defecto), incluye Insights por publicación. Agotarlo aborta; no se persiste un listado truncado como completo. Detecta IDs duplicados y cursores repetidos/inválidos. TikTok usa cursor entero; Instagram reconstruye la ruta con `after` y jamás sigue la URL remota `next`.

`ConnectorError` expone solo código seguro, status HTTP y `retry_after` numérico cuando existe. Distingue 401, 403, 429, otros HTTP, errores API en 200, proveedor y validación. No imprime ni propaga cuerpos o mensajes remotos. No hay retries automáticos, esperas ni scheduler: ante 429 se detiene y devuelve indicación al llamador; el futuro transporte debe respetar también los límites/uso de la aplicación y las señales actuales de Meta. No se fija una cuota Meta sin comprobar la versión y el dashboard.

## Registro y autorización de aplicaciones reales (procedimiento posterior)

Estas instrucciones no se han ejecutado. I4-A1 **no incluye callback OAuth ni proveedor real**: registrar una app no habilita aún la recopilación desde HelmOS. No introducir tokens en CLI, prompts, CSV, informes, archivos de estado o repositorio. Antes de un ensayo real hará falta una nueva implementación autorizada de ese límite, guardando secretos en un gestor del sistema fuera del repositorio y registros, con verificación de scopes/identidad y expiraciones.

### Instagram

1. Comprobar en Instagram que JorgeBytes es Business o Creator; si es personal, convertirla conscientemente a profesional antes de continuar.
2. Entrar en [Meta for Developers](https://developers.facebook.com/apps/), registrar la aplicación de HelmOS y seleccionar el caso de uso/producto Instagram API con Instagram Login disponible en ese dashboard. Completar datos de contacto, privacidad y eliminación de datos. No seleccionar publicación ni mensajería.
3. Configurar Instagram Login y un redirect HTTPS exacto que vaya a atender un backend propio. No introducir un callback ficticio esperando que esta versión de HelmOS lo atienda.
4. Añadir la cuenta propia como tester/rol y aceptar la invitación según el panel. Verificar qué acceso ofrece el modo de desarrollo y si el scope de Insights está disponible junto a `instagram_business_basic`. Releer las cuatro referencias enlazadas arriba: esta comprobación pendiente es requisito para continuar.
5. Si el uso exige Advanced Access, enviar App Review para los dos scopes de lectura justificados, demostrando autorización, lectura de medios y estadísticas. Completar la verificación empresarial que el dashboard requiera. No añadir permisos de escritura para solventar un rechazo.
6. Cuando exista el backend OAuth, autorizar expresamente con la cuenta propietaria, verificar `state`, redirect, cuenta y scopes concedidos; intercambiar código y token largo solo en backend. Guardar secretos fuera del repositorio y registrar únicamente vencimientos/estado seguros. Validar renovación con el contrato vigente y exigir nueva autorización si deja de ser válida.
7. Fijar versión Graph soportada y probar una publicación propia feed y un Reel, campo a campo y con valores ausentes, antes de activar el proveedor. Si Meta no concede Insights, conservar CSV y registrar la denegación.

### TikTok

1. Leer primero las directrices de revisión enlazadas: **la aplicación personal/privada actual está excluida de aprobación de producción**. No falsear el caso de uso ni presentar aprobación como un trámite garantizado. Para producción se requiere resolver esta elegibilidad con TikTok; mientras tanto, CSV.
2. Para una prueba de desarrollo que TikTok permita, crear cuenta en [TikTok for Developers](https://developers.tiktok.com/), registrar HelmOS y configurar un sandbox con la cuenta objetivo. [Guía de sandbox](https://developers.tiktok.com/docs/en/add-a-sandbox).
3. Seleccionar Login Kit y Display API, `video.list` y `user.info.basic` para comprobar identidad. Configurar plataforma Web, redirect exacto, dominio/propiedad, sitio, privacidad y términos que exija el portal. No activar Content Posting API.
4. Cuando exista callback, iniciar autorización Login Kit con `state` impredecible y comprobarlo a la vuelta; intercambiar código en backend con redirect idéntico. Verificar scopes y `open_id` devueltos, custodiar tokens y vencimientos fuera del repositorio. Renovar con `refresh_token` y conservar la rotación; no imprimir respuestas OAuth.
5. Si se acredita un caso elegible de producción, enviar revisión de productos/scopes con una demostración del flujo completo en sandbox. Esperar concesión efectiva antes de usar producción. Un sandbox o consentimiento del titular no elimina el requisito de revisión.

### LinkedIn

1. Comprobar elegibilidad organizativa/comercial según la revisión enlazada; el perfil personal de Jorge no equivale a organización elegible.
2. Solo si se cumplen condiciones, crear app en [LinkedIn Developers](https://www.linkedin.com/developers/apps), asociarla a página de organización y hacer que un superadministrador la verifique. Completar contacto empresarial, dominio, privacidad y redirect.
3. Solicitar Community Management Development Tier mediante Products y aportar los datos exigidos. Comprobar los scopes **realmente concedidos**, en particular `r_member_postAnalytics`; no inferirlos de Share on LinkedIn o Sign In.
4. Registrar separadamente el bloqueo de lectura de posts (`r_member_social` cerrado). No existe un procedimiento abierto confirmado para obtenerlo ahora. Si LinkedIn ofrece otra vía expresamente autorizada, documentarla y revisar el diseño antes de implementarla.
5. Solo tras aprobación y una implementación futura del proveedor, autorizar por OAuth del miembro con los scopes de lectura concedidos y consultar analítica de URNs propios conocidos. Fijar versión Marketing soportada. No solicitar `w_member_social` ni permisos de organizaciones como sustituto.
6. Standard Tier requiere revisión posterior y demostración de las funciones aplicables; no prometer aprobación ni renovación automática de credenciales. Mientras no se resuelvan acceso y descubrimiento, el gate permanece cerrado y se mantiene CSV.

## Verificación y estado

Archivos: nuevo `helmos/social_connectors.py`; nuevo `tests/test_social_connectors.py`; extracción del escritor común y compatibilidad de campos opcionales en `helmos/social_import.py`; este documento y propuesta separada; enlace desde README. Sin modificación de especificaciones, dependencias, Core, objetivos, calendarios ni Personal.

Pruebas: las 77 existentes se conservan; se añaden 17 casos con mocks/datos ficticios para mapping, ausencia/cero, paginación, límites, errores HTTP/API, timeout/JSON inválido del proveedor, seguridad de errores/procedencia, metadata explícita, duplicados, fallo remoto tardío, idempotencia, conflictos y no sobrescritura, histórico real en temporales, convivencia CSV en ambas direcciones, pieza compartida y gate LinkedIn. Sin IA real, tokens ni llamadas autenticadas.

Comandos de validación:

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Estado de preparación: implementada y verificable localmente. Activación real: pendiente y bloqueada por permisos/elegibilidad y por completar la comprobación documental de Meta. No se ha iniciado I4-A2, no hay commit ni push.

Resultado observado: `python -m unittest discover -s tests -v`: **94 pruebas OK en 16,118 s** (77 previas + 17 nuevas). `git diff --check`: sin errores. Comprobación adicional `git diff --no-index --check -- /dev/null <archivo>` sobre los cuatro archivos nuevos: sin errores de whitespace (Git avisa de conversión futura LF/CRLF; el código 1 de no-index indica archivos distintos).

### Cierre técnico

| Control | Estado y evidencia |
| --- | --- |
| Comportamiento solicitado | VERIFICADO en simulación; PENDIENTE completar contraste actual de Insights/OAuth de Meta por inaccesibilidad documental. No se declara integración real verificada. |
| Contratos | VERIFICADO localmente: CSV compatible, procedencia opcional, historial e identidad editorial/plataforma probados. |
| Tests/build/lint | VERIFICADO: 94 pruebas y diff check. No hay build/tipado/linter configurados que añadir para este incremento Python estándar. |
| Migraciones | NO APLICA: no hay migración de datos ni cambio destructivo; registros existentes intactos. |
| Seguridad | VERIFICADO en mocks: errores/procedencia sin payloads remotos ni secretos, rutas fijas, gate LinkedIn, ausencia de transporte real. Custodia/autorización real PENDIENTE de implementación y acceso. |
| Observabilidad | VERIFICADO: códigos de error, status, indicación numérica de espera y contadores del escritor compartido; no se añaden ejecuciones de Core que puedan guardar credenciales. |
| Documentación afectada | VERIFICADO: contrato, viabilidad, pasos de autorización, bloqueos y propuesta separada. |
| Cambios accidentales | VERIFICADO: diff de archivos existentes y archivos nuevos revisados; ninguna especificación modificada, sin commit/push. |
