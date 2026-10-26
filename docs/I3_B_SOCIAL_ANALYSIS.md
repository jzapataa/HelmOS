# I3-B: análisis editorial bajo demanda

Importa primero uno o más CSV canónicos mediante `social.import`. Las tres plantillas de `examples/` contienen **datos ficticios** y sirven para probar el recorrido. Luego ejecuta:

```powershell
python -m helmos run social.analyze
python -m helmos run social.analyze --platform Instagram --from 2026-01-01 --to 2026-12-31
python -m helmos show <id-de-la-ejecucion>
```

`--state-dir RUTA` puede preceder a `run` y `show`. La ejecución devuelve `report_path` y conserva el conjunto de datos y la propuesta estructurada en `var/runs/<id>.json`. El informe legible se guarda en `var/social/reports/<id>.md`. El ID del archivo coincide con la ejecución de origen. No se genera un informe si faltan publicaciones, falla Codex o no se verifica su respuesta; el error queda en una ejecución recuperable.

El conjunto de datos incluye las publicaciones seleccionadas con referencias `P001` y siguientes, métricas presentes, sumas **por plataforma**, número de publicaciones con dato y número sin dato para cada métrica reconocida. Las ausencias no son ceros. El contexto del agente es acotado, usa la [skill editorial](../.agents/skills/analyze-social-editorial/SKILL.md) y conserva las restricciones de `codex exec` de solo lectura. HelmOS valida la estructura de la propuesta, referencias y cifras estructuradas antes de persistirla. Las frases con cifras libres se rechazan porque no pueden cotejarse de forma fiable.

El informe presenta observaciones e hipótesis por separado, limitaciones, acciones y un calendario **orientativo pendiente de revisión**. No cambia objetivos, prioridades ni calendarios aprobados; tampoco publica contenido. Se admite hasta 50 publicaciones por ejecución; usa los filtros para seleccionar una muestra manejable. El CSV canónico no armoniza definiciones ni ventanas de medición de plataformas. La calidad editorial de las recomendaciones requiere revisión humana posterior.

Desde I3-C2, `social.analyze` usa por publicación y métrica el último valor de la base agregada: `cumulative`, una única ventana `period` común o `unspecified` cuando las otras bases no están disponibles. No suma capturas sucesivas de una publicación ni ventanas diferentes. Las mediciones ajenas a la base seleccionada se presentan aparte y no entran en la evidencia agregada. El conjunto de datos y el Markdown muestran la base, las capturas más antigua y reciente, fechas desconocidas, ventanas y valores ausentes. Las fechas antiguas no se sustituyen por la fecha del informe ni se declaran actuales. Los filtros `--from` y `--to` siguen seleccionando por **fecha de publicación**, no por captura. Python crea un catálogo `evidence` de IDs `E001` y siguientes, ordenados por plataforma y métrica dentro de la selección; omite métricas ausentes. Cada entrada conserva plataforma, métrica, suma, cobertura, tipo y período de medición. La IA devuelve únicamente IDs en `findings[].metric_evidence`; Python rechaza IDs desconocidos o incompatibles con las publicaciones citadas y persiste los valores resueltos en la ejecución y el informe. Los registros e informes anteriores permanecen legibles sin migración. El texto editorial con dígitos se rechaza automáticamente; afirmaciones cuantitativas escritas con palabras y la interpretación editorial requieren revisión humana.
