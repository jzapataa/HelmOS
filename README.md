# HelmOS

HelmOS V1.0 se implementa por incrementos. La [especificación V1.0](docs/SYSTEM_SPEC_V1.0.md) rige el trabajo actual; la [V0.1 original](docs/SYSTEM_SPEC_FINAL_v0.1.docx) conserva las reglas aplicables a Personal. Jorge OS y HelmOS son el mismo producto con distinto nombre.

## Incremento I1: Core local

Requiere Python 3.11 o posterior. Desde la raíz del repositorio:

```powershell
python -m helmos run core.status
python -m helmos show <id-de-la-ejecucion>
```

La primera orden comprueba la presencia local de la especificación V1.0 y los perfiles Developer, QA y Reviewer. Enruta la operación por una regla explícita y registra un JSON con ID, ruta, estado, resultado o error, tiempos y consumo desconocido. La segunda recupera ese registro en otro proceso. Los datos se guardan en `var/runs/`, fuera de Git. Se puede usar `--state-dir RUTA` antes del subcomando para elegir otro directorio local.

Solo `core.status` está disponible en I1. No se ejecutan agentes, ámbitos Social o Personal, integraciones ni acciones externas. No introduzcas secretos en las órdenes o en el directorio de estado. El estado local tiene un único escritor; la concurrencia se abordará antes de habilitar ejecuciones paralelas.

## Muestra acotada de I2

La skill reutilizable [analyze-social-sample](.agents/skills/analyze-social-sample/SKILL.md) indica cómo interpretar con cautela una muestra. `examples/social_sample.csv` contiene **datos ficticios**. Para ejecutar su cálculo local:

```powershell
python -m helmos run social.sample --csv examples/social_sample.csv
python -m helmos show <id-de-la-ejecucion>
```

El CSV acepta de 1 a 20 publicaciones, con columnas `post_id,platform,impressions,likes,comments,shares`; las plataformas admitidas son Instagram, TikTok y LinkedIn. Los recuentos deben ser enteros no negativos y los IDs únicos. El Core conserva las filas, suma los recuentos y calcula `interactions = likes + comments + shares` para esta muestra. El registro incluye la ruta, la skill identificada, los datos, los cálculos y advertencias. Un CSV inválido produce un registro recuperable con estado `failed`. Esta suma no define un KPI aprobado para HelmOS Social. El Core Python no invoca subagentes; la interpretación y revisión delegadas se hacen desde Codex por separado.

La [evidencia de la demostración con subagentes](docs/I2_DELEGATION.md) identifica el registro del Core, los perfiles efectivamente usados en Codex y los límites de este avance.

Para verificar el incremento:

```powershell
python -m unittest discover -s tests -v
```
