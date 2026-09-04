"""
Testes unitários das funções de NLU, limpeza de <think> e mascaramento — sem
precisar subir o servidor inteiro, mais rápidos e mais fáceis de debugar
quando quebram.
"""
import os

os.environ.setdefault("USE_LLM", "false")

import main  # noqa: E402  (import depois de setar env, de propósito)


def test_strip_thinking_removes_reasoning_block():
    bruto = "<think>o usuário quer a fatura, vou responder em português</think>Sua fatura é R$ 89,90."
    assert main.strip_thinking(bruto) == "Sua fatura é R$ 89,90."


def test_strip_thinking_handles_dangling_closing_tag():
    bruto = "raciocínio solto sem abertura </think>Resposta final."
    assert main.strip_thinking(bruto) == "Resposta final."


def test_strip_thinking_returns_original_if_no_tag():
    texto = "Resposta direta sem raciocínio exposto."
    assert main.strip_thinking(texto) == texto


def test_classify_intent_detects_fatura():
    result = main.classify_intent("quero a segunda via da minha fatura")
    assert result["intent"] == "informacao"


def test_classify_intent_detects_cancelamento():
    result = main.classify_intent("quero cancelar meu plano")
    assert result["intent"] == "cancelamento"


def test_classify_intent_detects_suporte():
    result = main.classify_intent("minha internet está caindo toda hora")
    assert result["intent"] == "suporte"


def test_classify_intent_explicit_handoff_overrides_everything():
    result = main.classify_intent("quero falar com atendente humano")
    assert result["intent"] == "handoff_explicito"


def test_classify_intent_unknown_when_no_keywords_match():
    result = main.classify_intent("xpto blablabla sem sentido nenhum")
    assert result["intent"] == "nao_entendido"


def test_is_closing_signal_detects_common_phrases():
    assert main.is_closing_signal("muito obrigado, era isso mesmo") is True
    assert main.is_closing_signal("valeu!") is True


def test_is_closing_signal_false_for_ordinary_question():
    assert main.is_closing_signal("quero saber da minha fatura") is False


def test_mask_id_keeps_last_four_digits():
    assert main.mask_id("11999990001") == "*******0001"


def test_mask_name_keeps_first_name_and_last_initial():
    assert main.mask_name("João Pereira") == "João P."
