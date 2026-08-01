"""layer4_egress · Capa 4 del harness: egress default-deny.

Las capas 1-3 responden "¿qué llega al handler y quién lo autoriza dentro del
sandbox?". Esta capa responde una pregunta distinta: "¿a dónde puede salir la
red desde este proceso?" Ninguna tool de red está exenta de declarar su
destino — un host no declarado en ``data/egress_allowlist.json`` se deniega,
sin excepciones tácitas (L2).

  · ``egress``: ``verificar_destino(host, tool) -> (bool, motivo)``.

Por qué es una capa aparte y no una regla más de ``layer2_risk``: el riesgo
mide DAÑO POTENCIAL de una tool call ya confinada al sandbox; el egress mide
FUGA DE RED hacia fuera del proceso. Son preguntas ortogonales — una tool de
bajo riesgo (``hablar_fish_audio`` es lectura, sin efecto en el sandbox)
puede seguir necesitando un destino de red permitido.
"""
