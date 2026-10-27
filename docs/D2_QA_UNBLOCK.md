# D2 — Desbloqueo del ejecutor de QA

Fecha: 2026-10-27. Continuación de D2 en la rama existente. No modifica [D1](D1_AUTONOMOUS_DEV_TEAM_DESIGN.md) ni la especificación V1.0.

## Requisito auditado y causa exacta

D1, §6, «Aplicación técnica de la política», exige antes de ejecutar código generado: escritura solo en workspace/temporales concedidos, lecturas limitadas sin secretos, red bloqueada y terminación de descendientes. Dice expresamente que un cwd, instrucciones o hashes no constituyen una frontera de seguridad y que, sin esos límites, se conserva `blocked_environment`. SYS-04 exige canarios y prueba de cancelación. **Ese requisito se mantiene.** No exige que el modelo QA tenga shell ni que las pruebas las ejecute una herramienta del modelo.

Había tres causas técnicas distintas:

1. El runner no seleccionaba backend. En esta instalación el backend elegido para ese proceso rechazaba el perfil restrictivo con `Restricted read-only access requires the elevated Windows sandbox backend`.
2. La comprobación anterior de MXC se había ejecutado dentro del sandbox anidado del agente. Allí aparecía `native MXC is unavailable on this executor`. Al repetir la comprobación en el **host**, MXC respondió `MXC_OK`. Por tanto no era correcto generalizar la falta de MXC al host.
3. Al seleccionar MXC en el runner, el entorno filtrado omitía `SystemDrive` y `LOCALAPPDATA`. El runtime devolvió sucesivamente `MXC requires nonempty Unicode host SystemDrive` y `MXC requires nonempty Unicode host LOCALAPPDATA`. Se conservan esas dos rutas de sistema; no se heredan claves, tokens ni el resto del entorno.

La documentación oficial permite seleccionar MXC para una invocación sin cambiar la configuración guardada y diferencia las garantías de los backends Windows. [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox). Una variable que contiene una ruta no concede acceso a ese directorio: los permisos efectivos siguen definidos por el perfil. [Permisos](https://learn.chatgpt.com/docs/permissions).

## QA y ejecutor son responsabilidades diferentes

| Componente | Responsabilidad y autoridad |
| --- | --- |
| Agente QA | Invocación independiente y sin herramientas. Deriva tests de los criterios, propone exclusivamente `test_qa_*.py`, solicita `recipe: unittest` y describe defectos. No ejecuta shell ni decide permisos. |
| Host | Valida propuestas, las aplica dentro del workspace, comprueba preflight y ejecuta la receta fija. Registra argv, salida, conteos, exit code y snapshot. |
| Proceso de pruebas | Python con `-I -B` dentro de MXC. Importa código generado, por lo que se trata como código no confiable aunque el comando proceda del host. |
| Reviewer | Sesión independiente, sin escritura. Inspecciona petición, fuentes iniciales/finales y recibo real del runner. Su revisión no sustituye los tests ni la aceptación humana. |

La evidencia de ejecución pertenece a la etapa QA y la mide el runner. Un `completed` del modelo significa propuesta lista para procesar, nunca éxito de aceptación. Los fallos del runner siguen provocando el ciclo existente de reparación.

## Alternativas evaluadas

| Alternativa | Evaluación |
| --- | --- |
| Repositorio desechable + unittest directamente en el host | Descartada: reduce daños sobre datos del proyecto, pero Python puede leer otras rutas. Un argv fijo, un entorno filtrado y `-I` tampoco impiden ese acceso. Incumple D1. |
| Host que aplica archivos + receta fija dentro de MXC | Elegida. Reutiliza el diseño y el runner, conserva el perfil y utiliza un backend ya disponible. No necesita servicios, cuentas, instalación o elevación del proceso de pruebas. |
| Backend legacy elevated | No instalado/configurado por esta tarea. No es necesario cambiar cuentas o firewall al existir MXC disponible y verificable. |
| Docker/WSL | Se inventariaron sin modificar el entorno: Docker CLI existe, pero no hay conexión al engine; WSL enumera `docker-desktop`. No se arrancó Docker ni se creó una distribución o plataforma nueva. |

El repositorio ficticio sigue siendo desechable, pero la confidencialidad la impone MXC con el perfil, no su ubicación temporal. Si MXC no está disponible, el comando falla y se mantiene el bloqueo; no hay fallback automático a ejecución directa o a permisos amplios.

## Cambios mínimos

- `helmos/devteam_runtime.py`: selección explícita por proceso `-c windows.sandbox=mxc`; conservación únicamente de las dos rutas de runtime adicionales; sin cambio a los grants de lectura/escritura ni a `network.enabled=false`.
- El canario de descendientes ahora exige que el hijo arranque y lea un archivo permitido, y comprueba también su lectura y escritura externas denegadas. Se exige el conjunto exacto de controles para evitar un falso positivo si no se ejecutó una comprobación.
- `helmos/devteam_demo.py`: opción explícita `--repair-demo`. Prepara un defecto sembrado y declarado sobre `None`, sin modificar el coordinador, los contratos, los límites o las puertas de salida.
- La ejecución real detectó además un defecto de contexto: al repetir QA tras una corrección, el coordinador volvía a presentar el fallo del snapshot anterior. `helmos/devteam.py` filtra esos recibos por snapshot y entrega solo objetivos de reverificación; `helmos/devteam_contracts.py` aclara que QA debe señalar defectos presentes en las fuentes actuales. Se mantienen los esquemas y las puertas de salida. El historial completo permanece en los artefactos.
- Tres regresiones nuevas: entorno MXC sin secretos, presencia de controles del hijo y selección de contexto de QA después de una reparación. Se ajustan las pruebas existentes a la selección de backend y conjunto de canarios.

No se añaden especialistas, backends genéricos, dependencias, infraestructura, rutas de acceso extra ni ampliaciones del presupuesto. Se conservan 16 llamadas/2 reparaciones/100 000 tokens observados de la demo y las demás cotas existentes. No se cambia configuración global de Codex.

## Garantías comprobadas

El preflight se ejecuta antes de las pruebas y registra:

1. Escritura/lectura de un archivo del workspace permitida.
2. Lectura y escritura sobre un canario ficticio externo denegadas.
3. Hijo arrancado con lectura interna permitida y lectura/escritura del canario externo denegadas.
4. Conexión a un listener loopback del host bloqueada, manteniendo la red del perfil desactivada.
5. Cancelación del árbol: el hijo inocuo no llega a crear su marcador diferido después de terminar la receta por timeout.

Las rutas de runtime y Python conservan lectura conforme al perfil original. No se afirma que toda lectura fuera del workspace esté prohibida ni que los canarios sean una prueba formal contra cualquier vulnerabilidad del sistema operativo. Se verifican los límites necesarios para esta fixture, con mecanismos nativos y sin usar credenciales reales.

Los archivos de aplicación continúan escribiéndose solo mediante el aplicador validado del host. Ningún especialista recibe comandos arbitrarios ni un canal de escritura directa. Los artefactos del coordinador se guardan en STATE, fuera del proyecto de aplicación.

## Repetición

Ejecutar desde una consola normal del **host**, no desde otro sandbox que oculte las capacidades MXC. Requiere la instalación actual de Python/Codex, autenticación Codex previamente disponible y MXC soportado por Windows; no necesita permisos de administrador ni configuración adicional. Si el preflight falla, detenerse y conservar el diagnóstico.

```powershell
python -m helmos --state-dir var/d2-qa-audit devteam probe
python -m helmos.devteam_demo --repair-demo
python -m unittest discover -s tests -v
git diff --check
```

La demo imprime proyecto, estado y directorio de artefactos. `delivery.md` contiene pasos manuales con resultados esperados. Usar el STATE que imprime la demo para `devteam show <id>`, `show <id>` y `approvals show <approval-id>`; consultar no vuelve a ejecutar IA.

La incidencia es una **inyección explícita de fallo**: la primera propuesta conserva `normalize_tags(None) == []`; el requisito final siempre exige `TypeError`. QA debe reproducir ese incumplimiento ejecutando tests reales. Solo después Developer lo corrige y el runner repite la suite. No se adulteran recibos, no se falsifica una salida fallida y no se relaja el test para obtener verde. Este escenario demuestra el mecanismo de reparación, no que un agente haya descubierto espontáneamente un defecto desconocido.

## Resultado real completo

**Estado final: `ready_for_acceptance`.** Proyecto `00824e89-76b3-4c4c-88b8-3fe90cd4829f`, repositorio ficticio desechable `var/d2-demos/31ca1195-c17c-4b5d-81a8-176d5b26a139/fictional-repository`. Artefactos locales: `state/devteam/<id>` bajo ese mismo directorio UUID. Evidencia exportada: [D2_QA_REPAIR_REAL.json](evidence/D2_QA_REPAIR_REAL.json).

| Etapa | Evidencia observada |
| --- | --- |
| Preflight | Ocho controles correctos: acceso interno, denegaciones externas de padre e hijo, arranque efectivo del hijo, red y terminación. Perfil restrictivo sin grants añadidos. |
| Planificación | Hilo `01a17f21-9718-7170-ae29-3619dc7942d6`; 14 177 tokens. |
| Developer inicial | Hilo `01a17f21-bdfc-7401-ace9-07899a3aca79`; 14 884 tokens. Propuesta de código/tests/README aplicada por host, con el defecto sembrado. |
| QA inicial | Hilo `01a17f21-e940-7dd2-8736-69dd6a9db459`; 15 457 tokens. El runner ejecutó 13 tests: **2 fallos**, 0 errores/saltos, exit 1. Reproducción: `None` no eleva TypeError, tanto en el test de Developer como en el de QA. |
| Reparación Developer | Hilo `01a17f22-1951-7991-9929-1755fc749c60`; 17 902 tokens. Corrigió el guard de entrada; no modificó los archivos propiedad de QA. |
| QA de regresión | Hilo `01a17f22-436c-76c2-84c0-278b96890309`; 15 800 tokens. El runner ejecutó **12 tests correctos**, 0 fallos/errores/saltos, exit 0. El test de `None` sigue presente y pasa; no se eliminó el requisito. |
| Reviewer | Hilo `01a17f22-6b1d-7f12-b0e1-c4497f02a77e`; 17 120 tokens. Revisión independiente de las fuentes y recibos suministrados: sin hallazgos bloqueantes, limitaciones y plan manual explícitos. No afirmó ejecutar pruebas por su cuenta. |
| Entrega | Código, instrucciones, ambos recibos de pruebas, plan manual y revisión humana pendiente. |

Total: **6 llamadas reales, 95 340 tokens observados, una reparación**, sin ampliar el presupuesto de 100 000 tokens/16 llamadas/2 reparaciones de la demo. Las seis invocaciones tienen hilos distintos. El modelo solicitado es `gpt-6-sol`; el modelo efectivo y el coste monetario siguen sin confirmación por el transporte.

Snapshot probado y revisado: `d522be16a6e3bf076b8aaf227172ed28496ce02b75ec9de7168f20579e8748e3`. Se contrastó también con los archivos actuales antes de exportar. La propuesta de revisión humana `e52e3860-6972-4107-912f-ce886f365f39` permanece `pending`; no se ha atribuido aceptación al usuario.

Los intentos previos se conservan y constan en la evidencia: uno se detuvo al declarar Developer su propuesta `blocked`; otro corrigió el defecto y pasó 13 tests, pero volvió a reparar por contexto obsoleto y terminó en `stopped_limits`. No se reescribieron sus estados ni se reutilizaron sus resultados como si fueran la ejecución completa. La ejecución final usa el filtro de snapshot descrito arriba y mantiene los límites originales.

### Plan manual de aceptación de la fixture

En el workspace final, tras verificar que el entorno de ejecución sigue autorizado:

1. Repetir la receta de QA bajo MXC: esperar 12 tests correctos, incluidos `None` y entradas no string.
2. Revisar el ejemplo del README: `normalize_tags(" Python, web, PYTHON, , Data Science ")` debe producir `['python', 'web', 'data science']`.
3. Comprobar `normalize_tags("")` y `normalize_tags(" , , ")`: ambos deben producir `[]`.
4. Registrar aceptación o defectos sobre el snapshot entregado. La ejecución técnica correcta no sustituye esa decisión.

## Verificación final y límites

- Suite completa: **253 pruebas correctas** en 31,410 s, incluidas las 250 iniciales y tres regresiones nuevas. Comando: `python -m unittest discover -s tests -v`.
- `git diff --check`: correcto. Sintaxis, referencias locales y coherencia de snapshot/recibos/exportación comprobadas.
- Se mantienen el aplicador, esquemas de salida, máquina de estados, política de aprobación, receta, presupuestos, permisos de archivos y ausencia de red. Cambia la selección de backend y se corrige el contexto de reverificación.
- El resultado acredita esta fixture pequeña de Python y el ciclo de reparación declarado. No acredita aplicaciones arbitrarias, ausencia de todo defecto, despliegue, aceptación humana o aislamiento en otras instalaciones.
- No se modifican Social, especificaciones ni perfiles; no se usan datos reales o repositorios privados; no se hacen commits, push, merge, despliegues o instalaciones. D3 no se ha iniciado.

| Cierre arquitectónico | Estado |
| --- | --- |
| Comportamiento | VERIFICADO para la demo: fallo real, reparación automática, QA repetido, Reviewer y entrega. |
| Contratos | VERIFICADO: mismos esquemas, roles y permisos; contexto QA filtrado por revisión. |
| Tests/build/lint | VERIFICADO: 253 pruebas y diff; no hay build o linter adicional configurado para este Core. |
| Migraciones | NO APLICA: sin migraciones de datos o esquemas. |
| Seguridad | VERIFICADO mediante el perfil sin ampliaciones y canarios de esta instalación; sin promesa de prueba formal del SO. |
| Observabilidad | VERIFICADO: intentos fallidos y final conservados, recibos y consumo; JSONL crudo sigue sin retención. |
| Documentación | VERIFICADO: causas, comandos, garantías, evidencia y límites. |
| Cambios accidentales | VERIFICADO mediante revisión de diff/estado; sin cambios en Social ni configuración global. |
