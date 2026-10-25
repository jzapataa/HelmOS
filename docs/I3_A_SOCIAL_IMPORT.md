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
