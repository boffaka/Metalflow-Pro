# backend/engines/ai_flowsheet_advisor.py
"""
AIFlowsheetAdvisor — analyse Claude API avec prompt caching et cooldown 10s.

3 modes : analyse passive (post-convergence), suggestions proactives, chat streaming.
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("mpdpms.ai_flowsheet_advisor")

_SYSTEM_PROMPT = """Tu es un expert en ingénierie des procédés miniers de l'or.
Analyse le flowsheet fourni et retourne des observations JSON strictement.
Format requis : [{"severity": "info|warning|error", "message": "...", "action": {}}]
Sois concis (max 3 observations), cible les vrais problèmes opérationnels.
Contexte : usine Au uniquement — flottation sulfurée, CIL, gravité, réfractarité."""

_METALLURGICAL_CONTEXT = """Benchmarks industrie pour usines Au :
- Récupération CIL oxyde : 93–96%
- Récupération CIL réfractaire : 78–88%
- Récupération gravitaire (GRG 40%) : 30–40%
- SRT CIL : 16–24h (oxyde), 24–32h (réfractaire)
- Énergie spécifique totale : 18–35 kWh/t
- CN consommation : 0.3–0.8 kg/t (CIL oxyde)
- Knelson mass pull : 2–4%"""


@dataclass
class AIObservation:
    severity: str  # "info" | "warning" | "error"
    message: str
    action: dict = field(default_factory=dict)


class AIFlowsheetAdvisor:

    def __init__(self, cooldown_s: float = 10.0):
        self.cooldown_s = cooldown_s
        self._last_analysis_ts: float = 0.0
        self._client = None
        self._init_client()

    def _init_client(self):
        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic()
        except Exception as exc:
            logger.warning("anthropic client non disponible: %s", exc)
            self._client = None

    def is_in_cooldown(self) -> bool:
        return (time.time() - self._last_analysis_ts) < self.cooldown_s

    def build_context(
        self, graph_summary: dict, kpis: dict, lims_data: dict
    ) -> str:
        rec = kpis.get("total_recovery_pct", "N/A")
        energy = kpis.get("energy_kwh_t", "N/A")
        oz = kpis.get("annual_oz", "N/A")
        nodes = graph_summary.get("nodes", 0)
        edges = graph_summary.get("edges", 0)

        lines = [
            f"Flowsheet : {nodes} nœuds, {edges} streams",
            f"Récupération totale : {rec}%",
            f"Énergie spécifique : {energy} kWh/t",
            f"Production annuelle estimée : {oz} oz/an",
        ]
        if lims_data:
            head = lims_data.get("mean_au_g_t")
            if head:
                lines.append(f"Teneur tête LIMS : {head:.2f} g/t Au")
            sulf = lims_data.get("mean_sulfur_pct")
            if sulf:
                lines.append(f"Soufre moyen LIMS : {sulf:.2f}%")

        return "\n".join(lines)

    async def analyze(self, context: str) -> list[AIObservation]:
        if not self._client:
            return []
        try:
            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=[
                    {"type": "text", "text": _SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": _METALLURGICAL_CONTEXT,
                     "cache_control": {"type": "ephemeral"}},
                ],
                messages=[{"role": "user", "content": context}],
            )
            self._last_analysis_ts = time.time()
            raw = response.content[0].text
            # Strip JSON fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            return [AIObservation(**obs) for obs in data]
        except Exception as exc:
            logger.warning("AI analysis failed: %s", exc)
            return []

    async def stream_chat(self, question: str, context: str):
        """Streaming chat contextuel — yields text chunks."""
        if not self._client:
            return
        async with self._client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=[{"type": "text", "text": _SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[
                {"role": "user", "content": f"Contexte flowsheet :\n{context}\n\nQuestion : {question}"}
            ],
        ) as stream:
            async for text in stream.text_stream:
                yield text
