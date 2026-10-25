# Evidencia de la demostración acotada de I2

La muestra de `examples/social_sample.csv` es ficticia. La demostración se ejecutó bajo demanda desde Codex el 25 de octubre de 2026. No se utilizaron cuentas ni APIs sociales.

## Ejecución local del Core

Desde la raíz del repositorio:

```powershell
python -m helmos run social.sample --csv examples/social_sample.csv
python -m helmos show 177b4d2b-c371-4e9e-a24e-9c48b66a2d99
```

La primera orden produjo el registro `177b4d2b-c371-4e9e-a24e-9c48b66a2d99` (`succeeded`), conservado en `var/runs/` y recuperado por la segunda orden en otro proceso. El Core validó las tres filas, calculó 290 impresiones, 24 likes, 6 comentarios, 3 compartidos y 33 interacciones de muestra (13, 9 y 11 por publicación), y registró datos, resultados, advertencias y el nombre/ruta de `analyze-social-sample`. El campo `result.skill` identifica la guía asociada; **no prueba por sí mismo que Codex la leyera**.

## Delegación real en Codex

| Subagente | Perfil de la herramienta | Trabajo observado |
| --- | --- | --- |
| `/root/metrics_interpreter` | `default`, especializado por su tarea | Leyó `SKILL.md`, ejecutó `show` sobre el registro y describió las cifras. Señaló que Instagram tiene el mayor recuento de interacciones solo entre estas tres filas, sin inferir tendencias ni estrategia. |
| `/root/metrics_conclusion_reviewer` | `reviewer` | Leyó `SKILL.md`, recuperó el mismo registro de forma independiente y aprobó las cifras y cautelas sin correcciones. |

Ambos fueron subagentes reales creados en la sesión de Codex; la especialización del primero vino de su encargo y la del segundo del perfil `reviewer`. El Core Python **no creó ni ejecutó** esos subagentes. Esta nota registra sus nombres de tarea y resultados comunicados, pero no es un log automático exportado por el motor de Codex ni prueba que se cargaran los perfiles TOML locales.

## Verificación y alcance

QA ejecutó `python -m unittest discover -s tests -v`: 12 pruebas correctas (seis de I1 y seis de la muestra). Comprobó `social.sample`, `show` en otro proceso, entradas inválidas y `core.status`. Reviewer no encontró fallos bloqueantes. La validación automática empaquetada del formato de skill no pudo ejecutarse porque falta PyYAML en el Python local; se revisó manualmente el frontmatter `name`/`description` y el contenido.

Este trabajo demuestra una skill y delegación supervisada desde Codex. No completa el I2 amplio de `SYSTEM_SPEC_V1.0.md`: el motor Codex integrado en Python, el enrutamiento asistido por IA y la bandeja de aprobaciones siguen pendientes. Tampoco implementa HelmOS Social completo.
