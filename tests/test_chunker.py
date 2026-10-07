from app.text_chunker import SentenceChunker


def stream(text: str, chunker: SentenceChunker | None = None) -> list[str]:
    c = chunker or SentenceChunker()
    out: list[str] = []
    for i in range(0, len(text), 3):   # simula tokens de 3 caracteres
        out += c.feed(text[i:i + 3])
    if rest := c.flush():
        out.append(rest)
    return out


def test_divide_em_frases():
    assert stream("Claro! Tenho horário na quinta. Quer esse?") == ["Claro!", "Tenho horário na quinta.", "Quer esse?"]


def test_nao_quebra_em_abreviacao_nem_numero():
    out = stream("A Dra. Helena atende. O valor é 1.500 reais.")
    assert out == ["A Dra. Helena atende.", "O valor é 1.500 reais."]


def test_primeira_frase_longa_quebra_na_virgula():
    texto = "Perfeito, deixa eu olhar a agenda de dermatologia, porque na quinta à tarde costuma lotar bastante."
    out = stream(texto)
    assert out[0] == "Perfeito, deixa eu olhar a agenda de dermatologia,"   # 1º pedaço sai cedo
    assert " ".join(out) == texto


def test_flush_devolve_resto_sem_pontuacao():
    c = SentenceChunker()
    assert c.feed("Deixa eu ver aqui") == []
    assert c.flush() == "Deixa eu ver aqui"
    assert c.flush() is None
