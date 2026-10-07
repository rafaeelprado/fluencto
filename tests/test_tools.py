import json

import pytest

from app.agent.tools import ToolExecutor
from app.db.database import AgendaError, ClinicDB


@pytest.fixture
def db():
    d = ClinicDB(":memory:")
    d.seed_if_empty()
    return d


def test_horarios_disponiveis_retorna_slots_futuros(db):
    r = db.horarios_disponiveis("dermato")
    assert r["servico"] == "Dermatologia"
    assert 0 < len(r["horarios"]) <= 6
    assert all(h["data_hora"] > db.now().isoformat() for h in r["horarios"])


def test_agendar_ocupa_o_horario(db):
    slot = db.horarios_disponiveis("Nutrição")["horarios"][0]
    ag = db.agendar("Maria Teste", "(79) 98888-7777", "nutricao", slot["data_hora"])
    assert ag["profissional"] == slot["profissional"]
    livres = [h["data_hora"] for h in db.horarios_disponiveis("Nutrição", slot["data_hora"][:10])["horarios"]]
    assert slot["data_hora"] not in livres
    with pytest.raises(AgendaError):
        db.agendar("Outra", "79977776666", "nutricao", slot["data_hora"])


def test_remarcar_e_cancelar(db):
    h = db.horarios_disponiveis("Fisioterapia")["horarios"]
    ag = db.agendar("José", "79911112222", "fisio", h[0]["data_hora"])
    novo = db.remarcar(ag["agendamento_id"], h[1]["data_hora"])
    meus = db.agendamentos_do_paciente("79911112222")
    assert [m["data_hora"] for m in meus] == [novo["data_hora"]]
    db.cancelar(novo["agendamento_id"])
    assert db.agendamentos_do_paciente("79911112222") == []


def test_servico_inexistente_vira_erro_legivel_para_o_llm(db):
    out, is_err = ToolExecutor(db).run("buscar_horarios_disponiveis", {"servico": "cardiologia"})
    assert is_err and "Opções" in json.loads(out)["erro"]


def test_faq(db):
    out, is_err = ToolExecutor(db).run("informacoes_clinica", {"topico": "convenios"})
    assert not is_err and "Unimed" in out
