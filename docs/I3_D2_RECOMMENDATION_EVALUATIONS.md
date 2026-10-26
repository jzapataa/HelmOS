# I3-D2: evaluación de recomendaciones editoriales

Evaluación local explícita, determinista y sin IA. Requiere una recomendación aceptada con al menos una publicación asociada existente. Una pendiente, rechazada o sin asociaciones produce un error sin crear evaluación. No cambia decisiones, publicaciones, informes ni calendarios.

Se puede anteponer `--state-dir RUTA` a los comandos:

```powershell
python -m helmos social recommendations evaluate <id-de-recomendación>
python -m helmos social recommendations evaluations <id-de-recomendación>
python -m helmos social evaluations show <id-de-evaluación>
```

La salida es JSON. La lista vacía distingue una recomendación aún sin evaluar de una evaluada con `insufficient_data`. Las consultas no ejecutan otra evaluación. El código de salida es 0 para una evaluación guardada (también con datos insuficientes) y 2 para errores de entrada, lectura o escritura.

## Modelo y cálculos

Los registros inmutables se guardan en `var/social/evaluations/<id>.json`. Cada registro incluye:

- `id`: SHA-256 de la recomendación, versión del método y conjunto de referencias de observaciones por publicación. No incluye la hora ni la ruta del CSV.
- `method`: `observed-cumulative-v1`, que identifica las reglas de cálculo.
- `recommendation_id`, `evaluated_at` (UTC) y `publications` utilizadas.
- Por publicación: identidad, fecha de publicación, métricas disponibles, instantáneas de todas las observaciones, resultados, estado y limitaciones. Cada instantánea conserva fechas, tipo de medición, períodos explícitos, métricas y procedencia (`source_csv`, `source_row`). Su `ref` es un SHA-256 de la identidad de publicación y del contenido temporal y numérico; también identifica observaciones iniciales e históricas sin archivo propio.
- `changes`: diferencias enteras y días transcurridos, con métrica, valores inicial/final, fechas y referencias de las dos observaciones concretas.
- `status`: `observed` si al menos una publicación dispone de algún intervalo comparable; `insufficient_data` si ninguna lo tiene. La cobertura parcial se muestra mediante estados individuales y el resumen de publicaciones con datos adecuados.
- `hypothesis_assessment`: siempre `insufficient_data` en este MVP. `explanation` ofrece una interpretación breve determinista y `limitations` explicita la ausencia de evidencia causal.

Se reutilizan las consultas de `social_history` y el almacén actual. Para cada publicación y métrica se ordenan las observaciones acumuladas con fecha y se comparan puntos consecutivos disponibles. La diferencia es `valor_final - valor_inicial`; los días transcurridos corresponden a las fechas de captura, sin inventar una ventana de cobertura acumulada. No se restan publicaciones, plataformas, períodos ni mediciones de cobertura desconocida. Un valor ausente permanece desconocido y se omite; un cero explícito sí es un dato. Cada métrica puede tener un intervalo distinto. Una caída del contador acumulado o fechas iguales se señala y se excluye del cálculo por posible corrección, reinicio o ambigüedad.

Una única captura permite mostrar recuentos, pero no satisface el criterio mínimo de comparación temporal de este incremento. Las observaciones de período conservan las ventanas aportadas, sin restarlas ni mezclarlas con acumuladas. Los registros antiguos sin fecha siguen siendo consultables, sin reinterpretarlos como acumulados. No se añaden tasas, puntuaciones ni umbrales editoriales.

## Histórico, idempotencia y límites

Evaluar otra vez con la misma recomendación y las mismas observaciones devuelve exactamente el registro anterior, incluida su fecha. Nuevas observaciones o asociaciones generan otro registro y conservan los anteriores; esto también se aplica a evaluaciones con datos insuficientes. No hay una opción para forzar duplicados. No existe índice mutable ni migración: la consulta de histórico reúne los registros por recomendación. La escritura usa archivo temporal y sustitución atómica bajo el modelo existente de un único escritor; no habilita escritores concurrentes.

El rendimiento describe lo observado. Muchas visualizaciones o un crecimiento positivo no demuestran que una recomendación fuera acertada. La asociación humana tampoco acredita que la recomendación se ejecutara. Este MVP no define grupo de control, línea base, criterio de éxito ni prueba de la hipótesis; no deduce causalidad ni aceptación editorial automática. Se pueden utilizar datos históricos anteriores a la aceptación, mostrando sus fechas sin tratarlos como un efecto posterior. La interpretación es un resumen de cobertura y límites, sin invocar Codex.

La procedencia conserva rutas del equipo local como en las importaciones actuales: revisar antes de compartir JSON. El historial crece con nuevos conjuntos de observaciones y las consultas recorren archivos locales, adecuado para el alcance actual. Los hashes identifican contenido, no autentican archivos modificados externamente. La revisión humana sigue siendo necesaria para cualquier conclusión editorial.

## Verificación

`python -m unittest discover -s tests` conserva las 58 pruebas anteriores y añade cobertura con datos ficticios en directorios temporales: estados, asociaciones, suficiencia, comparabilidad temporal, ausencias, idempotencia, procesos independientes, legado, importaciones fuera de orden, plataformas separadas, causalidad, errores y escritura atómica. `git diff --check` verifica el diff.

I3-D2 entrega este flujo de evaluación bajo demanda; no declara terminado I3 ni HelmOS V1.0.
