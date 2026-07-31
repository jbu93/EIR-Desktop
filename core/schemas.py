from pydantic import BaseModel, Field
from typing import Dict, Any, Literal, List


# §EIR_ACADEMY · Roles soportados V3
# - clinico:      odontólogo en consulta — respuestas directas, técnicas, dosis exactas
# - estudiante:   formación universitaria — respuestas pedagógicas con paso a paso y citas
# - investigador: análisis comparativo — gaps de evidencia, meta-análisis, contraste de protocolos
# - ciudadano:    persona común (Atelier público) — lenguaje cálido, sin jerga, banderas rojas claras
# - auxiliar:     auxiliar/asistente dental — bioseguridad, instrumentación, esterilización, bandejas
# - tecnico_dental: técnico de laboratorio 3D — flujo Blender→Anycubic, tolerancias de impresión
UserRole = Literal["clinico", "estudiante", "investigador", "ciudadano", "auxiliar", "tecnico_dental"]


class ConsultationRequest(BaseModel):
    specialty: str
    clinical_manifestation: str
    treatment_parameters: Dict[str, Any]
    patient_history: Dict[str, Any] = Field(default_factory=dict)
    # §EIR_ACADEMY · campo opcional. Default = clinico (comportamiento anterior).
    # Si no se envía, el sistema responde como antes (zero-breaking change).
    role: UserRole = "clinico"
    # §SMIDR-BUG1 · historial conversacional multi-turno. Lista de turnos previos
    # con formato: [{"role": "user"|"assistant", "content": "..."}]
    historial: list = Field(default_factory=list)
