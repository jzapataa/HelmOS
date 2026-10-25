# I3-A: importación local de publicaciones Social

`social.import` acepta un CSV **canónico preparado por la persona usuaria**. No interpreta directamente exportaciones nativas de Instagram, TikTok o LinkedIn: hay que mapear sus campos y comprobar sus definiciones antes de copiar valores. Las [plantillas de Instagram](../examples/social_instagram_template.csv), [TikTok](../examples/social_tiktok_template.csv) y [LinkedIn](../examples/social_linkedin_template.csv) contienen solo **datos ficticios** identificados. Sustituye sus filas por datos reales de JorgeBytes en un archivo local que no se añada a Git.

## Esquema CSV

Las cinco primeras columnas, en este orden, son obligatorias: `platform,post_id,published_at,format,topic`. Después debe haber al menos una columna de métrica reconocida, en cualquier orden. Se aceptan columnas de métricas comunes `impressions,reach,views,likes,comments,shares,saves,clicks` y columnas específicas `instagram_profile_visits,tiktok_favorites,linkedin_reactions,linkedin_reposts`. Se pueden omitir columnas opcionales; una celda vacía significa **dato ausente** y se conserva ausente. El valor `0` significa cero medido. Cada recuento presente debe ser un entero decimal no negativo, sin separadores ni decimales.

`platform` debe ser exactamente `Instagram`, `TikTok` o `LinkedIn`. `post_id` es el identificador estable de la publicación dentro de esa plataforma. `published_at` es la fecha civil `YYYY-MM-DD`; no representa una hora ni zona horaria. `format` y `topic` son etiquetas aportadas por quien prepara el CSV, sin taxonomía inferida. Los cinco campos obligatorios no pueden estar vacíos, tener espacios iniciales o finales ni superar 200 caracteres. No se admiten encabezados desconocidos, duplicados, filas mal formadas ni valores específicos de otra plataforma.

Solo se aceptan **métricas de publicaciones individuales**. No introduzcas seguidores de la cuenta, alcance de la cuenta, métricas de perfil agregadas ni filas de resumen como si fueran publicaciones. El encabezado `instagram_profile_visits` solo se usa si el dato corresponde a esa publicación, no al perfil completo. `linkedin_reactions` se mantiene separado de `likes`; `linkedin_reposts` se mantiene separado de `shares`. Las definiciones y ventanas de medición dependen de la fuente original: el CSV canónico no las armoniza ni permite comparar plataformas directamente.

## Uso

```powershell
python -m helmos run social.import --csv C:\ruta\jorgebytes_instagram.csv
python -m helmos run social.import --csv C:\ruta\jorgebytes_tiktok.csv
python -m helmos run social.import --csv C:\ruta\jorgebytes_linkedin.csv
python -m helmos show <id-de-la-ejecucion>
python -m helmos social posts --platform Instagram
python -m helmos social summary
python -m helmos social summary --platform TikTok
```

`--state-dir RUTA` puede preceder a `run`, `show` o `social`. Las publicaciones normalizadas quedan en `var/social/posts/`, un JSON por pareja `(platform, post_id)`; cada JSON conserva también `source_row` con los valores literales del CSV. El archivo CSV original no se modifica. Cada importación crea un registro recuperable en `var/runs/` con filas recibidas, nuevas, ya presentes y resumen por plataforma. Reimportar la misma fila no duplica la publicación. Una fila con la misma identidad y contenido diferente falla sin sobrescribirla; se debe revisar la diferencia antes de corregir datos mediante un procedimiento explícito futuro. El archivo completo se valida y se detectan conflictos antes de guardar publicaciones.

`social summary` suma cada métrica **solo dentro de su plataforma** e indica `observed_posts` junto a `post_count`. Si la cobertura es parcial, la suma es únicamente la de los valores presentes; no se estima el resto. No calcula tasas ni comparaciones entre plataformas. Las métricas de una importación se refieren a sus filas; `social summary` consulta todas las publicaciones almacenadas. El almacenamiento local admite un único proceso escritor, conforme a la especificación V1.0.

## I3-C1: observaciones históricas

El CSV de I3-A sigue siendo válido. Se pueden añadir `observed_at`, `measurement_type`, `period_start` y `period_end` después de las cinco columnas de identidad y metadatos, en cualquier orden junto a las métricas. Consulta el [ejemplo ficticio de observaciones](../examples/social_observations_template.csv).

- `observed_at`: fecha civil de captura `YYYY-MM-DD`, igual o posterior a `published_at`. Déjala vacía si se desconoce; no se infiere de la fecha de importación.
- `measurement_type`: `cumulative` para un total acumulado, `period` para una ventana delimitada, `unspecified` si no se conoce. Vacío equivale a `unspecified`.
- `period_start` y `period_end`: ambas obligatorias solo para `period`; delimitan una ventana inclusiva desde la publicación hasta la captura.

La primera importación crea el JSON de publicación en `var/social/posts/`. Las observaciones posteriores de la misma pareja `(platform, post_id)` se guardan en archivos JSON independientes bajo `var/social/observations/`; el archivo de publicación no se reescribe. Cada observación conserva las métricas presentes, la fila literal y el nombre del CSV origen. Una reimportación de idéntica fecha, tipo, período y métricas es idempotente. Otra medición con la misma fecha de captura o un cambio de metadatos se rechaza para revisión. Para añadir una medición diferente se necesita una fecha de captura explícita; no se aceptan dos mediciones sin fecha para la misma publicación.

Los JSON previos funcionan sin migración: su medición original se interpreta como `unspecified`, con fecha de captura y período desconocidos, y origen CSV no recuperable. No hay que reimportar los datos de JorgeBytes.

## I3-C2: consultas temporales

```powershell
python -m helmos social observations --platform TikTok --post-id MI_POST
python -m helmos social latest --platform TikTok --post-id MI_POST
python -m helmos social evolution --platform TikTok --post-id MI_POST
python -m helmos social summary --platform TikTok
```

`observations` devuelve todas las capturas de la publicación. `latest` devuelve el último valor conocido **por métrica y tipo de medición**, con su propia fecha; las ventanas `period` se separan por sus fechas de inicio y fin. Una fecha desconocida permanece `null` y no se presenta como dato actual. `evolution` muestra los valores acumulados fechados de cada métrica y la diferencia respecto al anterior valor acumulado comparable; excluye ausencias, ventanas y mediciones `unspecified`. La diferencia inicial es `null`.

`summary` cuenta cada publicación una vez. Su campo `metrics` conserva el formato anterior y utiliza el último valor acumulado por publicación y métrica cuando hay datos acumulados en la plataforma. Si no hay ninguno, puede agregar una única ventana `period` idéntica; si existen varias ventanas, las mantiene aparte. Después utiliza mediciones `unspecified` fechadas o, en su ausencia, las no fechadas heredadas. `metric_basis`, `capture_status` y, para ventanas, `period_start`/`period_end` indican la base elegida. `measurement_groups` muestra por separado las demás bases, el estado de la fecha y las ventanas exactas; no se suman entre sí. `observed_posts` solo cuenta publicaciones con un valor en esa base; `undated_posts` y las fechas de captura indican los límites temporales. Una métrica ausente en la captura más reciente conserva su último valor conocido y su fecha anterior. Estas sumas no estiman datos faltantes ni equiparan definiciones entre plataformas.
