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

Para verificar el incremento:

```powershell
python -m unittest discover -s tests -v
```
