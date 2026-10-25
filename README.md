# HelmOS

I3-A: [importación CSV de publicaciones Social](docs/I3_A_SOCIAL_IMPORT.md). I3-B: [análisis y propuesta editorial](docs/I3_B_SOCIAL_ANALYSIS.md).

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

La [evidencia de la demostración con subagentes](docs/I2_DELEGATION.md) identifica el registro del Core, los perfiles efectivamente usados en Codex y los límites de aquel avance.

## Incremento I2-B: agente Codex local

Requiere Codex CLI instalado y autenticado. Para una tarea de lectura del repositorio:

```powershell
python -m helmos run core.agent --task "Resume qué hace core.status." --context README.md
python -m helmos show <id-de-la-ejecucion>
```

`core.agent` inicia **una ejecución nueva de `codex exec`**, pasa la tarea y el texto del documento por stdin y solicita una respuesta JSON con `outcome`, `summary`, `evidence` y `limitations`. `--context` admite `README.md` o un archivo Markdown dentro de `docs/`, hasta 32 KiB; rechaza enlaces simbólicos y rutas fuera del repositorio. Si el agente declara `blocked`, HelmOS registra `failed` con el resultado y error recuperables. El registro incluye el motor `codex exec`, el modelo solicitado, el ID de hilo si Codex lo comunica y tokens si el evento de uso los proporciona. Los resultados quedan pendientes de revisión humana.

La ejecución solicita `gpt-6-sol` con esfuerzo `medium`, usa sandbox `read-only` y política de aprobación `never` para fallar cerrada en modo no interactivo. Desactiva herramientas de shell, multiagente, apps, plugins, navegación, búsqueda web, uso del equipo y hooks para esta invocación. No carga la configuración global de usuario ni la modifica; conserva las reglas de ejecución. El registro identifica el modelo solicitado, sin afirmar que los eventos hayan confirmado el modelo efectivo. En esta instalación Windows npm se invoca directamente el binario nativo que usa el lanzador oficial, para que el timeout termine el proceso Codex. La tarea tiene un máximo de 1000 caracteres, un proceso y 120 segundos; las salidas JSON tienen límites de tamaño. No introduzcas secretos en la tarea o documento. La ruta de estado local sigue teniendo un único escritor.

Este agente de CLI no es un subagente interno de una sesión de Codex. Los perfiles `.codex/agents/developer.toml`, `qa.toml` y `reviewer.toml` no se seleccionan ni cargan con esta interfaz; el registro deja `profile: null`. La tarea de ejemplo usa el documento aportado por el Core; no demuestra acceso del agente al sistema de archivos mediante sus herramientas. I2-B no implementa aún la asignación dinámica de perfiles, la ruta asistida por IA ni la bandeja de aprobaciones del I2 completo.

Para verificar el incremento:

```powershell
python -m unittest discover -s tests -v
```

## Incremento I2-C: rutas y aprobaciones locales

El Core usa un registro cerrado de rutas: `core.status`, `social.sample`, `social.import` y `core.agent`. Una ruta desconocida se rechaza antes de llamar a un ejecutor o crear un registro. La bandeja de aprobaciones guarda un JSON por propuesta en `var/approvals/` (o bajo `--state-dir RUTA`). Para crear y resolver una propuesta vinculada a una ejecución existente:

```powershell
python -m helmos run core.status
python -m helmos approvals create --run-id <id-de-la-ejecucion> --action review_output --description "Revisar la salida de estado" --scope "Core local"
python -m helmos approvals list
python -m helmos approvals show <id-de-la-propuesta>
python -m helmos approvals approve <id-de-la-propuesta>
# Como alternativa a approve, para otra propuesta pendiente:
python -m helmos approvals reject <id-de-la-propuesta>
```

Las acciones permitidas son `review_output` (revisión humana de una salida) y `manual_follow_up` (propuesta de trabajo posterior para una persona). La descripción y el alcance son metadatos; no conceden capacidades ni se interpretan como comandos. Crear una propuesta requiere una ejecución recuperable. Cada propuesta registra su origen, estado `pending`, `approved` o `rejected`, fechas e historial de transiciones. Aprobar o rechazar solo actualiza el JSON: no ejecuta la propuesta, modifica la ejecución original ni contacta servicios externos. Una propuesta resuelta no puede decidirse de nuevo. El almacenamiento local sigue admitiendo un único escritor.
