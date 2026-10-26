# I3-D1: seguimiento de recomendaciones editoriales

Al completar `python -m helmos run social.analyze`, HelmOS guarda cada acción propuesta en `var/social/recommendations/` como JSON local. Su ID estable es `<id-de-ejecución>:<posición>`. El registro conserva el texto, motivo, referencias a publicaciones de la muestra, fecha, estado, historial y publicaciones asociadas. `platform` y `topic` quedan vacíos en el formato editorial actual, que no los declara para cada acción; no se deduce el destino de una recomendación a partir de las publicaciones citadas como evidencia. Procesar de nuevo la misma ejecución conserva decisiones y asociaciones; ejecuciones distintas generan IDs distintos aunque el texto coincida.

Se puede anteponer `--state-dir RUTA` a cada comando. Las decisiones son acciones explícitas:

```powershell
python -m helmos social recommendations list
python -m helmos social recommendations list --status pending
python -m helmos social recommendations list --status accepted
python -m helmos social recommendations list --status rejected
python -m helmos social recommendations show <id-de-recomendación>
python -m helmos social recommendations accept <id-de-recomendación>
python -m helmos social recommendations reject <id-de-recomendación>
python -m helmos social recommendations associate <id-de-recomendación> --platform Instagram --post-id <post_id>
python -m helmos social recommendations posts <id-de-recomendación>
```

Solo se puede decidir una recomendación pendiente. Solo una aceptada admite asociaciones, y la publicación `(platform, post_id)` debe existir en el almacén importado. Se pueden asociar varias publicaciones distintas. La asociación documenta una decisión humana, sin atribuir causalidad a la recomendación. La aceptación no modifica objetivos, preferencias ni calendarios, y no publica contenido. La bandeja de aprobaciones del Core mantiene su semántica independiente para salidas o acciones que requieren aprobación.

El registro ocurre después de guardar la ejecución completada. Si se interrumpe la escritura de recomendaciones, la ejecución permanece recuperable; el siguiente comando reintenta explícitamente el registro, sin IA y sin sobrescribir decisiones ya guardadas:

```powershell
python -m helmos social recommendations register-run <id-de-ejecución>
```

Las ejecuciones históricas no se registran automáticamente. Los registros e informes anteriores se leen sin migración. La evaluación de resultados queda para I3-D2.
