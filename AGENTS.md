# Coordinación de agentes

- **Developer:** implementa los cambios solicitados y comunica archivos modificados, pruebas y riesgos pendientes.
- **QA:** valida los cambios frente a los requisitos y comunica resultados y fallos reproducibles, sin modificar código de producción.
- **Reviewer:** revisa los cambios y comunica hallazgos priorizados con referencias a los archivos, sin modificarlos.

Para la implementación V1.0, `docs/SYSTEM_SPEC_V1.0.md` es la fuente de verdad. `docs/SYSTEM_SPEC_FINAL_v0.1.docx` conserva las reglas originales aplicables a Personal y no debe modificarse retroactivamente. Jorge OS y HelmOS son el mismo producto; solo cambió el nombre.

Ningún agente debe suponer funcionalidades, módulos, arquitectura técnica ni reglas funcionales fuera de estas especificaciones y de las decisiones explícitas del usuario. Implementar por incrementos verificables en el orden Core → Social → Personal; no presentar un incremento parcial como V1.0 terminada.
