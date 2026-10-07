"""Ferramentas (tool calling) expostas ao Claude + dispatcher.

Regra de ouro para voz: o retorno das tools é JSON enxuto. Cada token a mais
no resultado é latência a mais no próximo round do LLM.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db.database import FAQ, AgendaError, ClinicDB

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "listar_servicos",
        "description": "Lista os serviços da clínica com duração e preço particular.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "buscar_horarios_disponiveis",
        "description": (
            "Busca horários livres para um serviço. Sem 'data', procura os próximos dias. "
            "Use SEMPRE antes de agendar ou remarcar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "servico": {"type": "string", "description": "Ex: Clínico Geral, Dermatologia, Nutrição, Fisioterapia"},
                "data": {"type": "string", "description": "Data AAAA-MM-DD (opcional)"},
                "periodo": {"type": "string", "enum": ["manha", "tarde", "noite"]},
            },
            "required": ["servico"],
        },
    },
    {
        "name": "agendar_consulta",
        "description": "Cria o agendamento. Só chame após o paciente confirmar horário, nome e telefone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_paciente": {"type": "string"},
                "telefone": {"type": "string", "description": "Com DDD, só números"},
                "servico": {"type": "string"},
                "data_hora": {"type": "string", "description": "AAAA-MM-DDTHH:MM, exatamente como veio da busca"},
                "profissional_id": {"type": "integer"},
            },
            "required": ["nome_paciente", "telefone", "servico", "data_hora"],
        },
    },
    {
        "name": "buscar_agendamentos",
        "description": "Lista as consultas futuras de um paciente pelo telefone.",
        "input_schema": {
            "type": "object",
            "properties": {"telefone": {"type": "string"}},
            "required": ["telefone"],
        },
    },
    {
        "name": "remarcar_consulta",
        "description": "Move um agendamento existente para um novo horário livre.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agendamento_id": {"type": "integer"},
                "nova_data_hora": {"type": "string", "description": "AAAA-MM-DDTHH:MM"},
            },
            "required": ["agendamento_id", "nova_data_hora"],
        },
    },
    {
        "name": "cancelar_consulta",
        "description": "Cancela um agendamento. Confirme com o paciente antes.",
        "input_schema": {
            "type": "object",
            "properties": {"agendamento_id": {"type": "integer"}},
            "required": ["agendamento_id"],
        },
    },
    {
        "name": "informacoes_clinica",
        "description": "Responde dúvidas frequentes da clínica.",
        "input_schema": {
            "type": "object",
            "properties": {"topico": {"type": "string", "enum": sorted(FAQ.keys())}},
            "required": ["topico"],
        },
    },
]


class ToolExecutor:
    def __init__(self, db: ClinicDB):
        self.db = db

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Executa a tool. Retorna (json_resultado, is_error)."""
        try:
            result = self._dispatch(name, args)
            return json.dumps(result, ensure_ascii=False), False
        except AgendaError as e:
            return json.dumps({"erro": str(e)}, ensure_ascii=False), True
        except Exception as e:  # noqa: BLE001 — nunca derrubar a chamada de voz por causa de uma tool
            log.exception("Falha na tool %s", name)
            return json.dumps({"erro": f"Falha interna: {e}"}, ensure_ascii=False), True

    def _dispatch(self, name: str, a: dict[str, Any]) -> Any:
        db = self.db
        match name:
            case "listar_servicos":
                return db.listar_servicos()
            case "buscar_horarios_disponiveis":
                return db.horarios_disponiveis(a["servico"], a.get("data"), a.get("periodo"))
            case "agendar_consulta":
                return db.agendar(a["nome_paciente"], a["telefone"], a["servico"], a["data_hora"], a.get("profissional_id"))
            case "buscar_agendamentos":
                ags = db.agendamentos_do_paciente(a["telefone"])
                return ags or {"mensagem": "Nenhuma consulta futura para esse telefone."}
            case "remarcar_consulta":
                return db.remarcar(int(a["agendamento_id"]), a["nova_data_hora"])
            case "cancelar_consulta":
                return db.cancelar(int(a["agendamento_id"]))
            case "informacoes_clinica":
                return {"resposta": db.faq(a["topico"]) or "Não tenho essa informação."}
            case _:
                raise AgendaError(f"Ferramenta desconhecida: {name}")
