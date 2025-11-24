"""automation_intents_v2.py

Gera intents Rasa (3.1) a partir de um arquivo de entrada com blocos
em texto (#intent, #pt, #en, #vr_pt, #vr_en), criando:
- data/<intent_path>/questions.yml
- data/<intent_path>/responses.yml

E sincroniza automaticamente:
- domain.yml (intents + action_fallback)
- data/rules.yml (rules intent -> utter_intent + rule_nlu_fallback)
- data/stories.yml (stories intent -> utter_intent + story_nlu_fallback)

Principais garantias:
- Não sobrescreve intents existentes no domain.yml (apenas adiciona novas);
- Não cria rules/stories duplicadas para a mesma intent (usa detecção por intent+action);
- Deduplica globalmente exemplos PT/EN (uma frase só aparece uma vez no NLU inteiro);
- Garante action_fallback no domain;
- Garante rule_nlu_fallback e story_nlu_fallback com action_fallback, sem duplicar.

python .\automation_intents.py --max 10
python .\automation_intents.py --max-pt 8
python .\automation_intents.py --max-pt 50 --max-en 50
"""

import os
import re
import sys
import subprocess
import argparse
from typing import Any, Dict, List, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

# ==========================
# Configurações gerais
# ==========================

REQUIRED_PACKAGES = ["ruamel.yaml"]
RASA_VERSION = "3.1"
DEFAULT_BASE_DIR = "data"
DEFAULT_INPUT_FILE = "input.txt"
DEFAULT_DOMAIN_PATH = "domain.yml"


# ==========================
# Utilitários de logging
# ==========================

def info(msg: str) -> None:
    print(f"ℹ️  {msg}")


def success(msg: str) -> None:
    print(f"✅ {msg}")


def warn(msg: str) -> None:
    print(f"⚠️  {msg}")


def error(msg: str) -> None:
    print(f"❌ {msg}")


# ==========================
# Dependências
# ==========================

def install_missing_packages() -> None:
    """Instala pacotes necessários via pip, caso não estejam presentes."""
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package.split(".")[0])
        except ImportError:
            info(f"Instalando dependência ausente: {package}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])


# Já rodamos a verificação/instalação antes de configurar o YAML
install_missing_packages()

# Configure ruamel.yaml
yaml = YAML()
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.allow_unicode = True


# ==========================
# Helpers básicos de texto
# ==========================

def normalize_intent_name(intent_path: str) -> str:
    """Normaliza o nome da intent pegando só o último segmento do caminho.

    Exemplo:
    - "interview/feelingToday" -> "feelingToday".
    """
    return intent_path.strip().replace("\\", "/").split("/")[-1]


def slug_to_phrase(slug: str) -> str:
    """Converte um slug em frase legível caso nenhum exemplo seja fornecido."""
    phrase = re.sub(r"[._\-/]+", " ", slug).strip()
    phrase = re.sub(r"\s+", " ", phrase)
    return phrase or slug


# ==========================
# Parsing do arquivo de entrada
# ==========================

def parse_limits(block: str, global_max_pt: int | None, global_max_en: int | None, global_max: int | None) -> Tuple[int | None, int | None]:
    """Resolve limites de exemplos PT/EN considerando tags locais e globais.

    Precedência: #max_pt/#max_en > #max > globais fornecidos em linha de comando.
    """

    def pick(tag: str) -> int | None:
        m = re.search(rf"(?mi)^\s*{tag}\s*:\s*(\d+)\s*$", block)
        return int(m.group(1)) if m else None

    max_pt = pick("#max_pt")
    max_en = pick("#max_en")
    max_both = pick("#max")

    if max_both is not None:
        max_pt = max_pt if max_pt is not None else max_both
        max_en = max_en if max_en is not None else max_both

    if max_pt is None:
        max_pt = global_max_pt if global_max_pt is not None else (global_max if global_max is not None else None)
    if max_en is None:
        max_en = global_max_en if global_max_en is not None else (global_max if global_max is not None else None)

    return max_pt, max_en


def extract_list(block: str, tag: str) -> List[str]:
    """Extrai lista de exemplos após '#pt:' ou '#en:' até próximo marcador/tag/---/EOF.

    Ignora linhas de controle como '#rp', '#rp_1', etc.
    Retorna uma lista de frases cruas (sem '- ').
    """
    m = re.search(rf"(?smi)^\s*{re.escape(tag)}\s*:(.*?)(?=^\s*#\w+\s*:|^\s*---\s*$|\Z)", block)
    if not m:
        return []

    out: List[str] = []
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        cleaned = ln.strip().lstrip("-").strip()
        if not cleaned:
            continue
        if re.match(r"^\s*#rp", cleaned, flags=re.IGNORECASE):
            continue
        out.append(cleaned)

    return out


def strip_markers(text: str) -> str:
    """Remove linhas de marcador como '#rp_', '#rp', etc., mantendo só conteúdo."""
    lines = text.strip().splitlines()
    out: List[str] = []
    for ln in lines:
        if re.match(r"^\s*#rp\b", ln, flags=re.IGNORECASE):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def parse_input(file_path: str, global_max_pt: int | None, global_max_en: int | None, global_max: int | None) -> List[Tuple[str, List[str], List[str], List[Tuple[str, str]]]]:
    """Lê o arquivo de entrada (input.txt) e devolve uma lista de intents.

    Cada item: (intent_path, [pt_examples], [en_examples], [(vr_pt, vr_en), ...])
    """
    if not os.path.exists(file_path):
        error(f"Arquivo de entrada não encontrado: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # separa blocos por linhas contendo só --- (tolerante a espaços)
    blocks = [b for b in re.split(r"(?m)^\s*---\s*$", content) if b.strip()]

    intents: List[Tuple[str, List[str], List[str], List[Tuple[str, str]]]] = []
    seen_pt: set[str] = set()
    seen_en: set[str] = set()
    ignored = 0

    def dedupe_global(lines: List[str], seen_global: set[str]) -> List[str]:
        out_local: List[str] = []
        for entry in lines:
            if entry and entry not in seen_global:
                seen_global.add(entry)
                out_local.append(entry)
        return out_local

    for idx, raw in enumerate(blocks, 1):
        block = raw.strip()
        if not block:
            continue

        m_intent = re.search(r"(?mi)^\s*#intent\s*:\s*(.+)$", block)
        if not m_intent:
            warn(f"Bloco {idx} ignorado (sem #intent:). Prévia: {block[:120].replace(os.linesep, ' ')}")
            ignored += 1
            continue
        intent = m_intent.group(1).strip()

        # Limites por bloco + globais
        max_pt, max_en = parse_limits(block, global_max_pt, global_max_en, global_max)

        # exemplos PT/EN (opcionais)
        pt_examples = extract_list(block, "#pt")
        en_examples = extract_list(block, "#en")

        # limpar e deduplicar globalmente (ninguém repete exemplo em lugar nenhum)
        pt_examples = dedupe_global(list(dict.fromkeys(pt_examples)), seen_pt)
        en_examples = dedupe_global(list(dict.fromkeys(en_examples)), seen_en)

        # aplicar limites por bloco, se houver
        if max_pt is not None:
            pt_examples = pt_examples[:max_pt]
        if max_en is not None:
            en_examples = en_examples[:max_en]

        # pares de respostas (#vr_pt: ... #vr_en: ...) — suporta múltiplos
        response_array: List[Tuple[str, str]] = []
        pattern = re.compile(
            r"(?smi)^\s*#vr_pt\s*:(.*?)^\s*#vr_en\s*:(.*?)(?="
            r"^\s*#vr_pt\s*:|^\s*#\w+\s*:|^\s*#rp[^\n]*$|^\s*---\s*$|\Z)"
        )
        for m in pattern.finditer(block):
            pt = strip_markers(m.group(1))
            en = strip_markers(m.group(2))
            if pt and en:
                response_array.append((pt, en))

        intents.append((intent, pt_examples, en_examples, response_array))

    if ignored:
        info(f"{ignored} bloco(s) ignorados por falta de #intent:.")

    return intents


# ==========================
# Geração de arquivos questions.yml / responses.yml
# ==========================

def clean_multiline_response(text: str) -> LiteralScalarString:
    cleaned = text.strip()
    if cleaned.startswith("|"):
        cleaned = cleaned.lstrip("|").strip()
    return LiteralScalarString(cleaned + "\n")


def create_files(intent_path: str, pt_examples: List[str], en_examples: List[str], response_array: List[Tuple[str, str]], base_dir: str) -> None:
    """Cria/atualiza questions.yml e responses.yml para uma intent específica."""
    name = normalize_intent_name(intent_path)
    folder = os.path.join(base_dir, *intent_path.split("/"))
    os.makedirs(folder, exist_ok=True)

    formatted_pt = "".join(f"- {ex}\n" for ex in pt_examples)
    formatted_en = "".join(f"- {ex}\n" for ex in en_examples)

    examples_text = (formatted_pt + formatted_en).rstrip("\n")

    # Fallback se nenhum exemplo foi encontrado
    if not examples_text:
        phrase = slug_to_phrase(name)
        examples_text = f"- {phrase}?\n- {phrase}"

    questions: Dict[str, Any] = {
        "version": RASA_VERSION,
        "nlu": [
            {
                "intent": name,
                "examples": LiteralScalarString(examples_text + "\n"),
            }
        ],
    }

    # Fallback de respostas para não quebrar Rasa
    if not response_array:
        response_array = [("TODO: adicionar resposta em PT", "TODO: add response in EN")]

    # Monta custom com resp_1, resp_2, ..., resp_N
    custom_payload: Dict[str, Dict[str, LiteralScalarString]] = {}
    for idx, (pt, en) in enumerate(response_array, start=1):
        resp_key = f"resp_{idx}"
        custom_payload[resp_key] = {
            "vr_pt": clean_multiline_response(pt),
            "vr_en": clean_multiline_response(en),
        }

    responses: Dict[str, Any] = {
        "version": RASA_VERSION,
        "responses": {
            f"utter_{name}": [
                {
                    "custom": custom_payload,
                }
            ]
        },
    }

    with open(os.path.join(folder, "questions.yml"), "w", encoding="utf-8") as qf:
        yaml.dump(questions, qf)
    with open(os.path.join(folder, "responses.yml"), "w", encoding="utf-8") as rf:
        yaml.dump(responses, rf)


# ==========================
# Helpers de YAML
# ==========================

def load_yaml(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.load(f) or {}
    return {}


def save_yaml(data: Dict[str, Any], path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


# ==========================
# Descoberta de intents na pasta data
# ==========================

def get_all_intents_in_data_folder(base_dir: str) -> List[str]:
    """Percorre data/* e devolve caminhos relativos que contêm questions.yml."""
    intents: List[str] = []

    def recurse(path: str) -> None:
        for e in sorted(os.listdir(path)):
            full = os.path.join(path, e)
            if os.path.isdir(full):
                recurse(full)
            elif e == "questions.yml":
                rel = os.path.relpath(path, base_dir).replace("\\", "/")
                intents.append(rel)

    recurse(base_dir)
    return intents


# ==========================
# Sincronização domain / rules / stories
# ==========================

def ensure_all_data_intents_registered(base_dir: str, domain_path: str, rules_path: str, stories_path: str) -> None:
    """Sincroniza domain.yml, rules.yml e stories.yml com as intents geradas em data/.

    - Não sobrescreve nada existente em domain.yml (só acrescenta intents novas e action_fallback se faltar);
    - Cria rules/story padrão (intent -> utter_intent) apenas se ainda não existir;
    - Trata nlu_fallback como caso especial (aceita action_fallback ou utter_nlu_fallback).
    """

    domain = load_yaml(domain_path)
    rules = load_yaml(rules_path)
    stories = load_yaml(stories_path)

    domain.setdefault("version", RASA_VERSION)
    domain.setdefault("intents", [])
    domain.setdefault("actions", [])
    domain.setdefault(
        "session_config",
        {
            "session_expiration_time": 60,
            "carry_over_slots_to_new_session": True,
        },
    )

    rules.setdefault("version", RASA_VERSION)
    rules.setdefault("rules", [])

    stories.setdefault("version", RASA_VERSION)
    stories.setdefault("stories", [])

    # Intents descobertas na pasta data
    all_intents = sorted(set(normalize_intent_name(i) for i in get_all_intents_in_data_folder(base_dir)))

    # --- DOMAIN.INTENTS: adiciona apenas as que não existem ---
    existing_intents: List[str] = domain.get("intents") or []
    added_intents = 0
    for it in all_intents:
        if it not in existing_intents:
            existing_intents.append(it)
            added_intents += 1
    domain["intents"] = existing_intents

    # --- RULES: para cada intent, garante uma regra (intent -> utter_intent) se não existir ---
    existing_rules: List[Dict[str, Any]] = rules.get("rules") or []

    def has_rule_for_intent(intent_name: str) -> bool:
        for r in existing_rules:
            if not isinstance(r, dict):
                continue

            steps = r.get("steps") or []
            has_intent = any(isinstance(s, dict) and s.get("intent") == intent_name for s in steps)
            if not has_intent:
                continue

            # para intents normais, procura utter_{intent}
            target_actions = {f"utter_{intent_name}"}

            # CASO ESPECIAL: nlu_fallback -> pode ser utter_nlu_fallback OU action_fallback
            if intent_name == "nlu_fallback":
                target_actions.add("action_fallback")

            has_action = any(isinstance(s, dict) and s.get("action") in target_actions for s in steps)

            if has_action:
                return True

        return False

    added_rules = 0
    for it in all_intents:
        if not has_rule_for_intent(it):
            existing_rules.append(
                {
                    "rule": f"rule_{it}",
                    "steps": [{"intent": it}, {"action": f"utter_{it}"}],
                }
            )
            added_rules += 1
    rules["rules"] = existing_rules

    # --- STORIES: para cada intent, garante uma história (intent -> utter_intent) se não existir ---
    existing_stories: List[Dict[str, Any]] = stories.get("stories") or []

    def has_story_for_intent(intent_name: str) -> bool:
        for s in existing_stories:
            if not isinstance(s, dict):
                continue

            steps = s.get("steps") or []
            has_intent = any(isinstance(st, dict) and st.get("intent") == intent_name for st in steps)
            if not has_intent:
                continue

            target_actions = {f"utter_{intent_name}"}
            if intent_name == "nlu_fallback":
                target_actions.add("action_fallback")

            has_action = any(isinstance(st, dict) and st.get("action") in target_actions for st in steps)

            if has_action:
                return True

        return False

    added_stories = 0
    for it in all_intents:
        if not has_story_for_intent(it):
            existing_stories.append(
                {
                    "story": f"story_{it}",
                    "steps": [{"intent": it}, {"action": f"utter_{it}"}],
                }
            )
            added_stories += 1
    stories["stories"] = existing_stories

    # --- FALLBACK: garante SOMENTE a action 'action_fallback' no domain ---
    actions = domain.get("actions") or []
    if "action_fallback" not in actions:
        actions.append("action_fallback")
        domain["actions"] = actions
        info("Action 'action_fallback' adicionada em domain.yml")

    save_yaml(domain, domain_path)
    save_yaml(rules, rules_path)
    save_yaml(stories, stories_path)

    success(
        f"Sincronizado domain/rules/stories com intents de data/ (+{added_intents} intents, +{added_rules} rules, +{added_stories} stories)."
    )


# ==========================
# Fallback rule/story
# ==========================

def append_fallback_rule_and_story(rules_path: str, stories_path: str) -> None:
    """Normaliza fallback em rules.yml e stories.yml.

    - Toda combinação (intent: nlu_fallback + action: utter_nlu_fallback) vira action_fallback;
    - Garante rule_nlu_fallback e story_nlu_fallback com action_fallback, sem duplicar.
    """

    FALLBACK_ACTION = "action_fallback"

    # ========== RULES ==========
    rules = load_yaml(rules_path)
    rules.setdefault("version", RASA_VERSION)
    rules.setdefault("rules", [])

    # Remove duplicatas de rule_nlu_fallback, mantendo só a primeira
    unique_rules: List[Dict[str, Any]] = []
    seen_fallback_rule = False
    for r in rules["rules"]:
        if isinstance(r, dict) and r.get("rule") == "rule_nlu_fallback":
            if seen_fallback_rule:
                continue
            seen_fallback_rule = True
        unique_rules.append(r)
    rules["rules"] = unique_rules

    updated_any_rule = False
    has_named_rule = False

    for r in rules["rules"]:
        if not isinstance(r, dict):
            continue

        steps = r.get("steps") or []
        if r.get("rule") == "rule_nlu_fallback":
            has_named_rule = True

        has_intent = any(isinstance(s, dict) and s.get("intent") == "nlu_fallback" for s in steps)
        if not has_intent:
            continue

        for s in steps:
            if isinstance(s, dict) and s.get("action") == "utter_nlu_fallback":
                s["action"] = FALLBACK_ACTION
                updated_any_rule = True

    if not has_named_rule:
        rules["rules"].append(
            {
                "rule": "rule_nlu_fallback",
                "steps": [
                    {"intent": "nlu_fallback"},
                    {"action": FALLBACK_ACTION},
                ],
            }
        )
        info("rule_nlu_fallback adicionada ao final de rules.yml")
    elif updated_any_rule:
        info("Regras com nlu_fallback atualizadas para usar action_fallback em rules.yml")
    else:
        success("Regras de fallback já usam action_fallback em rules.yml")

    save_yaml(rules, rules_path)

    # ========== STORIES ==========
    stories = load_yaml(stories_path)
    stories.setdefault("version", RASA_VERSION)
    stories.setdefault("stories", [])

    # Remove duplicatas de story_nlu_fallback, mantendo só a primeira
    unique_stories: List[Dict[str, Any]] = []
    seen_fallback_story = False
    for s in stories["stories"]:
        if isinstance(s, dict) and s.get("story") == "story_nlu_fallback":
            if seen_fallback_story:
                continue
            seen_fallback_story = True
        unique_stories.append(s)
    stories["stories"] = unique_stories

    updated_any_story = False
    has_named_story = False

    for s in stories["stories"]:
        if not isinstance(s, dict):
            continue

        steps = s.get("steps") or []
        if s.get("story") == "story_nlu_fallback":
            has_named_story = True

        has_intent = any(isinstance(st, dict) and st.get("intent") == "nlu_fallback" for st in steps)
        if not has_intent:
            continue

        for st in steps:
            if isinstance(st, dict) and st.get("action") == "utter_nlu_fallback":
                st["action"] = FALLBACK_ACTION
                updated_any_story = True

    if not has_named_story:
        stories["stories"].append(
            {
                "story": "story_nlu_fallback",
                "steps": [
                    {"intent": "nlu_fallback"},
                    {"action": FALLBACK_ACTION},
                ],
            }
        )
        info("story_nlu_fallback adicionada ao final de stories.yml")
    elif updated_any_story:
        info("Stories com nlu_fallback atualizadas para usar action_fallback em stories.yml")
    else:
        success("Stories de fallback já usam action_fallback em stories.yml")

    save_yaml(stories, stories_path)


# ==========================
# Arquivos default
# ==========================

def create_file_if_not_exists(path: str, default: Dict[str, Any]) -> None:
    if not os.path.exists(path):
        save_yaml(default, path)
        info(f"Arquivo criado: {path}")


# ==========================
# main()
# ==========================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera intents a partir de input.txt com respostas e exemplos PT/EN e sincroniza domain/rules/stories.",
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=DEFAULT_INPUT_FILE,
        help=f"Arquivo de entrada (default: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument("--max", dest="max_both", type=int, default=None, help="Limite global para PT e EN")
    parser.add_argument("--max-pt", dest="max_pt", type=int, default=None, help="Limite global apenas PT")
    parser.add_argument("--max-en", dest="max_en", type=int, default=None, help="Limite global apenas EN")
    parser.add_argument("--base-dir", dest="base_dir", default=DEFAULT_BASE_DIR, help="Diretório base das intents (default: data)")
    parser.add_argument("--domain", dest="domain_path", default=DEFAULT_DOMAIN_PATH, help="Caminho para domain.yml (default: domain.yml)")
    args = parser.parse_args()

    input_file = args.input_file
    base_dir = args.base_dir
    domain_path = args.domain_path
    rules_path = os.path.join(base_dir, "rules.yml")
    stories_path = os.path.join(base_dir, "stories.yml")

    os.makedirs(base_dir, exist_ok=True)
    create_file_if_not_exists(
        domain_path,
        {
            "version": RASA_VERSION,
            "intents": [],
            "actions": [],
            "session_config": {
                "session_expiration_time": 60,
                "carry_over_slots_to_new_session": True,
            },
        },
    )
    create_file_if_not_exists(rules_path, {"version": RASA_VERSION, "rules": []})
    create_file_if_not_exists(stories_path, {"version": RASA_VERSION, "stories": []})

    # 1) Gera intents/respostas a partir do input (cria/atualiza arquivos em data/*)
    intents = parse_input(input_file, args.max_pt, args.max_en, args.max_both)
    for intent_path, pt, en, response_array in intents:
        name = normalize_intent_name(intent_path)
        info(
            f"Criando intent '{name}' com {len(pt)} exemplo(s) PT, {len(en)} EN e {len(response_array)} resposta(s)"
        )
        create_files(intent_path, pt, en, response_array, base_dir)

    # 2) Depois de gerar os arquivos, sincroniza domain/rules/stories sem duplicar
    ensure_all_data_intents_registered(base_dir, domain_path, rules_path, stories_path)

    # 3) Por último, append da rule/story de fallback no fim dos arquivos (sem duplicar)
    append_fallback_rule_and_story(rules_path, stories_path)

    success(
        "Finalizado: exemplos preservados, intents sincronizadas sem duplicar, e fallback rule/story + action_fallback garantidos!",
    )


if __name__ == "__main__":
    main()
