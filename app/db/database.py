"""Base local da clínica (SQLite).

Disponibilidade NÃO é armazenada: é calculada = expediente do profissional − agendamentos.
Isso evita inconsistência entre "slots" e "consultas" e mantém o modelo simples.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS servicos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    descricao TEXT NOT NULL,
    duracao_min INTEGER NOT NULL,
    preco REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS profissionais (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    servico_id INTEGER NOT NULL REFERENCES servicos(id),
    dias_semana TEXT NOT NULL,        -- ex: "0,1,2,3,4" (0 = segunda)
    inicio TEXT NOT NULL,             -- "08:00"
    fim TEXT NOT NULL                 -- "17:00"
);
CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    telefone TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS agendamentos (
    id INTEGER PRIMARY KEY,
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    profissional_id INTEGER NOT NULL REFERENCES profissionais(id),
    servico_id INTEGER NOT NULL REFERENCES servicos(id),
    inicio TEXT NOT NULL,             -- ISO "2026-09-21T14:00"
    status TEXT NOT NULL DEFAULT 'confirmado',   -- confirmado | cancelado
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ag_prof_inicio ON agendamentos(profissional_id, inicio);
CREATE TABLE IF NOT EXISTS faq (
    topico TEXT PRIMARY KEY,
    resposta TEXT NOT NULL
);
"""

SERVICOS = [
    ("Clínico Geral", "Consulta de rotina, check-up e encaminhamentos.", 30, 180.0),
    ("Dermatologia", "Avaliação de pele, cabelo e unhas.", 30, 250.0),
    ("Nutrição", "Avaliação nutricional e plano alimentar.", 60, 200.0),
    ("Fisioterapia", "Sessão de fisioterapia ortopédica.", 60, 150.0),
]

PROFISSIONAIS = [
    ("Dra. Helena Souza", "Clínico Geral", "0,1,2,3,4", "08:00", "17:00"),
    ("Dr. Marcos Lima", "Clínico Geral", "0,2,4,5", "13:00", "19:00"),
    ("Dra. Beatriz Rocha", "Dermatologia", "1,3", "09:00", "16:00"),
    ("Carla Mendes", "Nutrição", "0,1,2,3,4", "08:00", "14:00"),
    ("Rafael Duarte", "Fisioterapia", "0,1,2,3,4,5", "07:00", "12:00"),
]

FAQ = {
    "endereco": "Avenida Beira Mar, 1500, sala 302, bairro Treze de Julho, Aracaju.",
    "horario": "Segunda a sexta das sete às dezenove horas, sábado das sete ao meio-dia.",
    "convenios": "Atendemos Unimed, Bradesco Saúde e SulAmérica. Também atendemos particular.",
    "pagamento": "Aceitamos Pix, cartão de débito e crédito em até três vezes sem juros.",
    "estacionamento": "Há estacionamento conveniado no prédio, com a primeira hora gratuita.",
    "cancelamento": "Cancelamentos e remarcações são gratuitos com até vinte e quatro horas de antecedência.",
    "preparo": "Chegue com dez minutos de antecedência e traga um documento com foto e a carteirinha do convênio.",
}


class AgendaError(Exception):
    """Erro de regra de negócio — a mensagem é mostrada ao LLM para ele se corrigir."""


@dataclass
class Slot:
    inicio: datetime
    profissional_id: int
    profissional: str


class ClinicDB:
    def __init__(self, path: Path | str, tz: str = "America/Sao_Paulo"):
        self.path = str(path)
        self.tz = ZoneInfo(tz)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ setup
    def now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None, second=0, microsecond=0)

    def seed_if_empty(self, rng_seed: int = 42) -> None:
        if self.conn.execute("SELECT COUNT(*) FROM servicos").fetchone()[0]:
            return
        c = self.conn
        c.executemany("INSERT INTO servicos(nome, descricao, duracao_min, preco) VALUES (?,?,?,?)", SERVICOS)
        ids = {r["nome"]: r["id"] for r in c.execute("SELECT id, nome FROM servicos")}
        c.executemany(
            "INSERT INTO profissionais(nome, servico_id, dias_semana, inicio, fim) VALUES (?,?,?,?,?)",
            [(n, ids[s], d, i, f) for n, s, d, i, f in PROFISSIONAIS],
        )
        c.executemany("INSERT INTO faq(topico, resposta) VALUES (?,?)", FAQ.items())
        c.executemany(
            "INSERT INTO pacientes(nome, telefone) VALUES (?,?)",
            [("Ana Paula Ribeiro", "79999990001"), ("João Carlos Silva", "79999990002")],
        )
        c.commit()
        # Ocupa ~40% da agenda dos próximos 10 dias para a demo parecer real.
        rng = random.Random(rng_seed)
        start = self.now().date()
        for d in range(10):
            dia = start + timedelta(days=d)
            for slot in self._slots_do_dia(dia, servico_id=None, apenas_futuros=False):
                if rng.random() < 0.4:
                    self._inserir(1 + rng.randint(0, 1), slot.profissional_id, slot.inicio)
        c.commit()

    # ------------------------------------------------------------ consultas
    def listar_servicos(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT nome, descricao, duracao_min, preco FROM servicos")]

    def faq(self, topico: str) -> str | None:
        topico = _norm(topico)
        for r in self.conn.execute("SELECT topico, resposta FROM faq"):
            if r["topico"] in topico or topico in r["topico"]:
                return r["resposta"]
        return None

    def _servico(self, nome: str) -> sqlite3.Row:
        alvo = _norm(nome)
        for r in self.conn.execute("SELECT * FROM servicos"):
            if alvo in _norm(r["nome"]) or _norm(r["nome"]) in alvo:
                return r
        opcoes = ", ".join(s["nome"] for s in self.listar_servicos())
        raise AgendaError(f"Serviço '{nome}' não encontrado. Opções: {opcoes}.")

    def _slots_do_dia(self, dia: date, servico_id: int | None, apenas_futuros: bool = True) -> list[Slot]:
        q = "SELECT p.*, s.duracao_min FROM profissionais p JOIN servicos s ON s.id = p.servico_id"
        params: tuple = ()
        if servico_id is not None:
            q += " WHERE p.servico_id = ?"
            params = (servico_id,)
        agora = self.now()
        slots: list[Slot] = []
        for p in self.conn.execute(q, params).fetchall():
            if str(dia.weekday()) not in p["dias_semana"].split(","):
                continue
            ocupados = {
                r["inicio"]
                for r in self.conn.execute(
                    "SELECT inicio FROM agendamentos WHERE profissional_id=? AND status='confirmado' AND inicio LIKE ?",
                    (p["id"], f"{dia.isoformat()}%"),
                )
            }
            t = datetime.combine(dia, time.fromisoformat(p["inicio"]))
            fim = datetime.combine(dia, time.fromisoformat(p["fim"]))
            passo = timedelta(minutes=p["duracao_min"])
            while t + passo <= fim:
                livre = t.isoformat(timespec="minutes") not in ocupados
                if livre and (not apenas_futuros or t > agora + timedelta(minutes=30)):
                    slots.append(Slot(t, p["id"], p["nome"]))
                t += passo
        return sorted(slots, key=lambda s: s.inicio)

    def horarios_disponiveis(
        self, servico: str, data: str | None = None, periodo: str | None = None, limite: int = 6
    ) -> dict:
        s = self._servico(servico)
        inicio = date.fromisoformat(data) if data else self.now().date()
        dias = 1 if data else 14
        faixa = {"manha": (0, 12), "tarde": (12, 18), "noite": (18, 24)}.get(_norm(periodo or ""), (0, 24))
        encontrados: list[Slot] = []
        for d in range(dias):
            dia = inicio + timedelta(days=d)
            for sl in self._slots_do_dia(dia, s["id"]):
                if faixa[0] <= sl.inicio.hour < faixa[1]:
                    encontrados.append(sl)
            if len(encontrados) >= limite:
                break
        return {
            "servico": s["nome"],
            "duracao_min": s["duracao_min"],
            "preco": s["preco"],
            "horarios": [
                {"data_hora": sl.inicio.isoformat(timespec="minutes"), "profissional": sl.profissional,
                 "profissional_id": sl.profissional_id, "dia_semana": _DIAS[sl.inicio.weekday()]}
                for sl in encontrados[:limite]
            ],
        }

    # ------------------------------------------------------------- escrita
    def _paciente(self, nome: str, telefone: str) -> int:
        tel = _so_digitos(telefone)
        if len(tel) < 10:
            raise AgendaError("Telefone inválido: peça o número com DDD.")
        row = self.conn.execute("SELECT id FROM pacientes WHERE telefone=?", (tel,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO pacientes(nome, telefone) VALUES (?,?)", (nome.strip(), tel))
        return cur.lastrowid

    def _inserir(self, paciente_id: int, profissional_id: int, inicio: datetime) -> int:
        servico_id = self.conn.execute(
            "SELECT servico_id FROM profissionais WHERE id=?", (profissional_id,)
        ).fetchone()["servico_id"]
        cur = self.conn.execute(
            "INSERT INTO agendamentos(paciente_id, profissional_id, servico_id, inicio, criado_em) VALUES (?,?,?,?,?)",
            (paciente_id, profissional_id, servico_id, inicio.isoformat(timespec="minutes"),
             self.now().isoformat(timespec="minutes")),
        )
        return cur.lastrowid

    def _resolver_slot(self, servico_id: int, data_hora: str, profissional_id: int | None) -> Slot:
        try:
            inicio = datetime.fromisoformat(data_hora)
        except ValueError as e:
            raise AgendaError("data_hora deve estar no formato AAAA-MM-DDTHH:MM.") from e
        candidatos = [s for s in self._slots_do_dia(inicio.date(), servico_id) if s.inicio == inicio]
        if profissional_id:
            candidatos = [s for s in candidatos if s.profissional_id == profissional_id]
        if not candidatos:
            raise AgendaError("Esse horário não está disponível. Consulte os horários livres antes de agendar.")
        return candidatos[0]

    def agendar(self, nome: str, telefone: str, servico: str, data_hora: str, profissional_id: int | None = None) -> dict:
        s = self._servico(servico)
        slot = self._resolver_slot(s["id"], data_hora, profissional_id)
        pid = self._paciente(nome, telefone)
        ag_id = self._inserir(pid, slot.profissional_id, slot.inicio)
        self.conn.commit()
        return {"agendamento_id": ag_id, "servico": s["nome"], "profissional": slot.profissional,
                "data_hora": slot.inicio.isoformat(timespec="minutes"), "preco": s["preco"]}

    def agendamentos_do_paciente(self, telefone: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.id, a.inicio, s.nome AS servico, p.nome AS profissional, pa.nome AS paciente
               FROM agendamentos a
               JOIN servicos s ON s.id=a.servico_id
               JOIN profissionais p ON p.id=a.profissional_id
               JOIN pacientes pa ON pa.id=a.paciente_id
               WHERE pa.telefone=? AND a.status='confirmado' AND a.inicio >= ?
               ORDER BY a.inicio""",
            (_so_digitos(telefone), self.now().isoformat(timespec="minutes")),
        ).fetchall()
        return [{"agendamento_id": r["id"], "data_hora": r["inicio"], "servico": r["servico"],
                 "profissional": r["profissional"], "paciente": r["paciente"]} for r in rows]

    def _agendamento(self, agendamento_id: int) -> sqlite3.Row:
        r = self.conn.execute(
            "SELECT * FROM agendamentos WHERE id=? AND status='confirmado'", (agendamento_id,)
        ).fetchone()
        if not r:
            raise AgendaError("Agendamento não encontrado ou já cancelado.")
        return r

    def cancelar(self, agendamento_id: int) -> dict:
        self._agendamento(agendamento_id)
        self.conn.execute("UPDATE agendamentos SET status='cancelado' WHERE id=?", (agendamento_id,))
        self.conn.commit()
        return {"agendamento_id": agendamento_id, "status": "cancelado"}

    def remarcar(self, agendamento_id: int, nova_data_hora: str) -> dict:
        ag = self._agendamento(agendamento_id)
        slot = self._resolver_slot(ag["servico_id"], nova_data_hora, None)
        # Atômico: cancela o antigo e cria o novo na mesma transação.
        with self.conn:
            self.conn.execute("UPDATE agendamentos SET status='cancelado' WHERE id=?", (agendamento_id,))
            novo = self._inserir(ag["paciente_id"], slot.profissional_id, slot.inicio)
        return {"agendamento_id": novo, "profissional": slot.profissional,
                "data_hora": slot.inicio.isoformat(timespec="minutes")}


_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def dia_semana(d: date) -> str:
    return _DIAS[d.weekday()]


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return s.strip()
