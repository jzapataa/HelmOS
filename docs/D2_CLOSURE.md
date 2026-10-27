# D2 — Cierre técnico y aceptación humana pendiente

Fecha: 2026-10-27. Alcance: D1 y D2; sin nuevas funcionalidades ni inicio de D3. La integración en Git cierra el trabajo técnico solicitado, **no representa aceptación del usuario** ni cierre de HelmOS V1.0.

## Entrega que se somete a aceptación

- Proyecto existente: `00824e89-76b3-4c4c-88b8-3fe90cd4829f`, estado `ready_for_acceptance`.
- Workspace ficticio: `var/d2-demos/31ca1195-c17c-4b5d-81a8-176d5b26a139/fictional-repository`.
- Snapshot: `d522be16a6e3bf076b8aaf227172ed28496ce02b75ec9de7168f20579e8748e3`.
- Revisión humana: `e52e3860-6972-4107-912f-ce886f365f39`, estado **pending**.
- Evidencia reproducible y fuentes entregadas: [D2_QA_REPAIR_REAL.json](evidence/D2_QA_REPAIR_REAL.json). La exportación permite revisar la entrega aunque desaparezca el workspace temporal.

## Plan breve de aceptación humana

No requiere crear otra demo, invocar IA, repetir canarios o ejecutar la suite. Consiste en revisar la demostración y sus artefactos ya conservados. Cada paso lo evalúa la persona responsable; sus resultados siguen pendientes.

| Paso | Acción sobre la entrega existente | Resultado esperado | Estado humano |
| --- | --- | --- | --- |
| 1 | Abrir la evidencia y contrastar ID, estado y snapshot con este documento. | Mismo proyecto y snapshot; `ready_for_acceptance`; revisión humana `pending`. | Pendiente |
| 2 | Revisar `project.snapshot.tags.py`, README y los casos de QA de la exportación. Contrastar el ejemplo `normalize_tags(" Python, web, PYTHON, , Data Science ")`, vacíos y entradas no string. | Ejemplo: `['python', 'web', 'data science']`; vacíos: `[]`; `None` y demás no string: `TypeError`. Sin dependencias o servicios. Los recibos respaldan estos casos. | Pendiente |
| 3 | Leer ambos recibos `project.tests` y las dos propuestas de Developer. | Primera ejecución: 13 tests y 2 fallos por el defecto sembrado. Una reparación automática. Segunda: 12 tests correctos, incluido el caso de `None`; sin rebajar ese requisito. | Pendiente |
| 4 | Revisar `project.isolation.checks`, perfil y cancelación. | Ocho controles `true`; MXC por invocación, red desactivada, límites de lectura/escritura y descendientes; sin bypass ni ampliación de permisos. | Pendiente |
| 5 | Leer `project.review`, `delivery_markdown` y las limitaciones. | Reviewer independiente sin bloqueantes, plan manual comprensible; diferencia explícita entre evidencia automática y aceptación humana. Alcance limitado a esta fixture. | Pendiente |

**Criterio de aprobación humana:** los cinco pasos resultan conformes, la demostración satisface el objetivo de D2 y sus limitaciones son comprendidas. Si algún paso no es conforme, registrar defecto con paso, esperado y observado; no marcar aceptado. La persona debe emitir una decisión explícita con fecha y snapshot. Este cierre no ejecuta `approvals approve` ni modifica estados de aceptación.

## Evidencia conservada y comprobaciones de cierre

- [D1: diseño](D1_AUTONOMOUS_DEV_TEAM_DESIGN.md), [implementación D2](D2_AUTONOMOUS_DEV_CIRCUIT.md) y [desbloqueo de QA](D2_QA_UNBLOCK.md).
- [Primera demostración parcial](evidence/D2_REAL_DEMO.json) y [demostración completa con reparación](evidence/D2_QA_REPAIR_REAL.json), sin reescribir resultados previos ni recibos.
- [Manifiesto de validación](evidence/D2_CLOSURE_VALIDATION.json): conserva el resultado observado de **253 pruebas correctas en 31,410 s**, hashes de las evidencias y de los 43 archivos Python de Core/pruebas en el cierre. Distingue el resumen observado del log bruto de unittest, que no se guardó en un archivo del repositorio.
- En este cierre solo se verifican sintaxis, JSON, integridad de evidencia/snapshot, aceptación pendiente, diferencias y patrones de credenciales. No se repiten demo, tests de aplicación, canarios o suite completa, puesto que no cambian código ni pruebas.
- Revisión de seguridad: aplicación de archivos por host, separación de roles, perfil MXC restrictivo, entorno filtrado y límites conservados. Sin patrones de credenciales reales en el alcance de publicación revisado; ese análisis estático no es una garantía formal de detección de secretos. No se incluyen `var/`, credenciales ni repositorios temporales en el commit.
- Límites ya declarados: Windows/MXC disponible, proyectos Python ficticios pequeños, aceptación y cobertura semántica humana pendientes, JSONL crudo no retenido y canarios sin pretensión de prueba formal del SO. No se han identificado bloqueantes técnicos nuevos dentro de este alcance.

## Git e integración

La rama existente al iniciar el cierre es `master`, sobre `a3247c8456b1af474fc059e41feb85e347dfe316`, coincidente con el remoto comprobado. D1 y D2 se integran mediante el commit de cierre en esa rama, sin merge redundante ni force push. El hash final y el estado de publicación se entregan tras verificar el push.

La revisión humana permanece pendiente tanto antes como después de publicar. Social, Personal, especificaciones y perfiles no se modifican. No hay despliegues ni trabajo de D3.
