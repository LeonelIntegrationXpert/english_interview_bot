import os
import re
import sys
import subprocess
import argparse
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

REQUIRED_PACKAGES = ["ruamel.yaml"]


def install_missing_packages():
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package.split('.')[0])
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])


install_missing_packages()

# Configure ruamel.yaml
yaml = YAML()
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.allow_unicode = True


def normalize_intent_name(intent_path: str) -> str:
    return intent_path.strip().replace('\\', '/').split('/')[-1]


def slug_to_phrase(slug: str) -> str:
    phrase = re.sub(r'[._\-/]+', ' ', slug).strip()
    phrase = re.sub(r'\s+', ' ', phrase)
    return phrase or slug


def parse_limits(block: str, global_max_pt: int | None, global_max_en: int | None, global_max: int | None):
    def pick(tag):
        m = re.search(rf"(?mi)^\s*{tag}\s*:\s*(\d+)\s*$", block)
        return int(m.group(1)) if m else None

    max_pt = pick('#max_pt')
    max_en = pick('#max_en')
    max_both = pick('#max')

    # Precedência: específicos > #max > globais
    if max_both is not None:
        max_pt = max_pt if max_pt is not None else max_both
        max_en = max_en if max_en is not None else max_both

    if max_pt is None:
        max_pt = global_max_pt if global_max_pt is not None else (global_max if global_max is not None else None)
    if max_en is None:
        max_en = global_max_en if global_max_en is not None else (global_max if global_max is not None else None)

    return max_pt, max_en

def extract_list(block: str, tag: str):
    """Extrai lista de exemplos após '#pt:' ou '#en:' até próximo marcador/tag/---/EOF,
    ignorando linhas de controle tipo '#rp_1', '#rp', etc."""
    m = re.search(rf'(?smi)^\s*{re.escape(tag)}\s*:(.*?)(?=^\s*#\w+\s*:|^\s*---\s*$|\Z)', block)
    if not m:
        return []

    out = []
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        # remove bullet inicial e espaços
        cleaned = ln.strip().lstrip('-').strip()
        if not cleaned:
            continue
        # NÃO pode entrar nada que seja marcador de regra/peso: #rp, #rp_1, etc
        if re.match(r"^\s*#rp", cleaned, flags=re.IGNORECASE):
            continue
        out.append(cleaned)

    return out

def strip_markers(text: str) -> str:
    """Remove linhas de marcador como '#rp_', '#rp', etc., e espaços extras."""
    lines = text.strip().splitlines()
    out = []
    for ln in lines:
        if re.match(r"^\s*#rp\b", ln, flags=re.IGNORECASE):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def parse_input(file_path: str, global_max_pt: int | None, global_max_en: int | None, global_max: int | None):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # separa blocos por linhas contendo só --- (tolerante a espaços)
    blocks = [b for b in re.split(r'(?m)^\s*---\s*$', content) if b.strip()]

    intents = []
    seen_pt, seen_en = set(), set()
    ignored = 0

    def dedupe_global(lines, seen_global):
        out = []
        for entry in lines:
            if entry and entry not in seen_global:
                seen_global.add(entry)
                out.append(entry)
        return out

    for idx, raw in enumerate(blocks, 1):
        block = raw.strip()
        if not block:
            continue

        m_intent = re.search(r'(?mi)^\s*#intent\s*:\s*(.+)$', block)
        if not m_intent:
            print(f"⚠️  Bloco {idx} ignorado (sem #intent:). Prévia: {block[:120].replace(os.linesep, ' ')}")
            ignored += 1
            continue
        intent = m_intent.group(1).strip()

        # Limites por bloco + globais
        max_pt, max_en = parse_limits(block, global_max_pt, global_max_en, global_max)

        # exemplos PT/EN (opcionais)
        pt_examples = extract_list(block, '#pt')
        en_examples = extract_list(block, '#en')

        # limpar e deduplicar globalmente
        pt_examples = dedupe_global(list(dict.fromkeys(pt_examples)), seen_pt)
        en_examples = dedupe_global(list(dict.fromkeys(en_examples)), seen_en)

        # aplicar limites por bloco, se houver
        if max_pt is not None:
            pt_examples = pt_examples[:max_pt]
        if max_en is not None:
            en_examples = en_examples[:max_en]

        # pares de respostas (#vr_pt: ... #vr_en: ...) — suporta múltiplos
        response_array = []
        pattern = re.compile(
            r'(?smi)^\s*#vr_pt\s*:(.*?)^\s*#vr_en\s*:(.*?)(?='
            r'^\s*#vr_pt\s*:|^\s*#\w+\s*:|^\s*#rp[^\n]*$|^\s*---\s*$|\Z)'
        )
        for m in pattern.finditer(block):
            pt = strip_markers(m.group(1))
            en = strip_markers(m.group(2))
            if pt and en:
                response_array.append((pt, en))

        intents.append((intent, pt_examples, en_examples, response_array))

    if ignored:
        print(f"ℹ️  {ignored} bloco(s) ignorados por falta de #intent:.")

    return intents

def clean_multiline_response(text: str) -> LiteralScalarString:
    cleaned = text.strip()
    if cleaned.startswith('|'):
        cleaned = cleaned.lstrip('|').strip()
    return LiteralScalarString(cleaned + '\n')

def create_files(intent_path: str, pt_examples, en_examples, response_array, base_dir: str):
    name = normalize_intent_name(intent_path)
    folder = os.path.join(base_dir, *intent_path.split('/'))
    os.makedirs(folder, exist_ok=True)

    formatted_pt = ''.join(f"- {ex}\n" for ex in pt_examples)
    formatted_en = ''.join(f"- {ex}\n" for ex in en_examples)

    examples_text = (formatted_pt + formatted_en).rstrip('\n')

    # Fallback se nenhum exemplo foi encontrado
    if not examples_text:
        phrase = slug_to_phrase(name)
        examples_text = f"- {phrase}?\n- {phrase}"

    questions = {
        'version': '3.1',
        'nlu': [{
            'intent': name,
            'examples': LiteralScalarString(examples_text + '\n')
        }]
    }

    # Fallback de respostas para não quebrar Rasa
    if not response_array:
        response_array = [("TODO: adicionar resposta em PT", "TODO: add response in EN")]

    # Monta custom com resp_1, resp_2, ..., resp_N
    custom_payload = {}
    for idx, (pt, en) in enumerate(response_array, start=1):
        resp_key = f"resp_{idx}"
        custom_payload[resp_key] = {
            'vr_pt': clean_multiline_response(pt),
            'vr_en': clean_multiline_response(en)
        }

    responses = {
        'version': '3.1',
        'responses': {
            f'utter_{name}': [
                {
                    'custom': custom_payload
                }
            ]
        }
    }

    with open(os.path.join(folder, 'questions.yml'), 'w', encoding='utf-8') as qf:
        yaml.dump(questions, qf)
    with open(os.path.join(folder, 'responses.yml'), 'w', encoding='utf-8') as rf:
        yaml.dump(responses, rf)

def load_yaml(path: str):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.load(f) or {}
    return {}


def save_yaml(data, path: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)


def get_all_intents_in_data_folder(base_dir: str):
    intents = []

    def recurse(path):
        for e in sorted(os.listdir(path)):
            full = os.path.join(path, e)
            if os.path.isdir(full):
                recurse(full)
            elif e == 'questions.yml':
                rel = os.path.relpath(path, base_dir).replace('\\', '/')
                intents.append(rel)

    recurse(base_dir)
    return intents

def ensure_all_data_intents_registered(base_dir: str, domain_path: str, rules_path: str, stories_path: str):
    """
    NÃO sobrescreve nada:
    - Mantém intents já existentes no domain;
    - Adiciona intents novas vindas de data/*/questions.yml;
    - Gera rules/stories apenas para intents que ainda não têm esse par (intent + utter_{intent});
    - Garante 'action_fallback' em actions do domain (NÃO mexe em responses).
    """
    domain = load_yaml(domain_path)
    rules = load_yaml(rules_path)
    stories = load_yaml(stories_path)

    domain.setdefault('version', '3.1')
    domain.setdefault('intents', [])
    domain.setdefault('actions', [])
    domain.setdefault('session_config', {
        'session_expiration_time': 60,
        'carry_over_slots_to_new_session': True
    })
    # ❌ não damos mais setdefault em 'responses' aqui, deixa o que já existir no arquivo

    rules.setdefault('version', '3.1')
    rules.setdefault('rules', [])

    stories.setdefault('version', '3.1')
    stories.setdefault('stories', [])

    # Intents descobertas na pasta data
    all_intents = sorted(set(normalize_intent_name(i) for i in get_all_intents_in_data_folder(base_dir)))

    # --- DOMAIN.INTENTS: adiciona apenas as que não existem ---
    existing_intents = domain.get('intents') or []
    added_intents = 0
    for it in all_intents:
        if it not in existing_intents:
            existing_intents.append(it)
            added_intents += 1
    domain['intents'] = existing_intents

    # --- RULES: para cada intent, garante uma regra (intent -> utter_intent) se não existir ---
    existing_rules = rules.get('rules') or []

    def has_rule_for_intent(intent_name: str) -> bool:
        for r in existing_rules:
            if not isinstance(r, dict):
                continue
            steps = r.get('steps') or []
            has_intent = any(isinstance(s, dict) and s.get('intent') == intent_name for s in steps)
            has_action = any(isinstance(s, dict) and s.get('action') == f'utter_{intent_name}' for s in steps)
            if has_intent and has_action:
                return True
        return False

    added_rules = 0
    for it in all_intents:
        if not has_rule_for_intent(it):
            existing_rules.append({
                'rule': f"rule_{it}",
                'steps': [{'intent': it}, {'action': f'utter_{it}'}]
            })
            added_rules += 1
    rules['rules'] = existing_rules

    # --- STORIES: para cada intent, garante uma história (intent -> utter_intent) se não existir ---
    existing_stories = stories.get('stories') or []

    def has_story_for_intent(intent_name: str) -> bool:
        for s in existing_stories:
            if not isinstance(s, dict):
                continue
            steps = s.get('steps') or []
            has_intent = any(isinstance(st, dict) and st.get('intent') == intent_name for st in steps)
            has_action = any(isinstance(st, dict) and st.get('action') == f'utter_{intent_name}' for st in steps)
            if has_intent and has_action:
                return True
        return False

    added_stories = 0
    for it in all_intents:
        if not has_story_for_intent(it):
            existing_stories.append({
                'story': f"story_{it}",
                'steps': [{'intent': it}, {'action': f'utter_{it}'}]
            })
            added_stories += 1
    stories['stories'] = existing_stories

    # --- FALLBACK: garante SOMENTE a action 'action_fallback' no domain ---
    actions = domain.get('actions') or []
    if 'action_fallback' not in actions:
        actions.append('action_fallback')
        domain['actions'] = actions
        print("➕ Action 'action_fallback' adicionada em domain.yml")

    save_yaml(domain, domain_path)
    save_yaml(rules, rules_path)
    save_yaml(stories, stories_path)

    print(
        f"✅ Sincronizado domain/rules/stories com intents de data/ "
        f"(+{added_intents} intents, +{added_rules} rules, +{added_stories} stories)."
    )

def append_fallback_rule_and_story(rules_path: str, stories_path: str):
    """
    Garante que:
    - TODA ocorrência de (intent: nlu_fallback + action: utter_nlu_fallback)
      em rules/stories vira action_fallback;
    - rule_nlu_fallback e story_nlu_fallback existam com action_fallback.
    """
    FALLBACK_ACTION = "action_fallback"

    # ========== RULES ==========
    rules = load_yaml(rules_path)
    rules.setdefault('version', '3.1')
    rules.setdefault('rules', [])

    updated_any_rule = False
    has_named_rule = False

    for r in rules['rules']:
        if not isinstance(r, dict):
            continue

        steps = r.get('steps') or []
        # marca se é a rule “oficial”
        if r.get('rule') == 'rule_nlu_fallback':
            has_named_rule = True

        # normaliza qualquer combinação intent nlu_fallback + action utter_nlu_fallback
        has_intent = any(isinstance(s, dict) and s.get('intent') == 'nlu_fallback' for s in steps)
        if not has_intent:
            continue

        for s in steps:
            if isinstance(s, dict) and s.get('action') == 'utter_nlu_fallback':
                s['action'] = FALLBACK_ACTION
                updated_any_rule = True

    # garante a rule oficial com formato certinho
    if not has_named_rule:
        rules['rules'].append({
            'rule': 'rule_nlu_fallback',
            'steps': [
                {'intent': 'nlu_fallback'},
                {'action': FALLBACK_ACTION}
            ]
        })
        print('➕ rule_nlu_fallback adicionada ao final de rules.yml')
    elif updated_any_rule:
        print('♻️ Regras com nlu_fallback atualizadas para usar action_fallback em rules.yml')
    else:
        print('✔️ Regras de fallback já usam action_fallback em rules.yml')

    save_yaml(rules, rules_path)

    # ========== STORIES ==========
    stories = load_yaml(stories_path)
    stories.setdefault('version', '3.1')
    stories.setdefault('stories', [])

    updated_any_story = False
    has_named_story = False

    for s in stories['stories']:
        if not isinstance(s, dict):
            continue

        steps = s.get('steps') or []
        if s.get('story') == 'story_nlu_fallback':
            has_named_story = True

        has_intent = any(isinstance(st, dict) and st.get('intent') == 'nlu_fallback' for st in steps)
        if not has_intent:
            continue

        for st in steps:
            if isinstance(st, dict) and st.get('action') == 'utter_nlu_fallback':
                st['action'] = FALLBACK_ACTION
                updated_any_story = True

    if not has_named_story:
        stories['stories'].append({
            'story': 'story_nlu_fallback',
            'steps': [
                {'intent': 'nlu_fallback'},
                {'action': FALLBACK_ACTION}
            ]
        })
        print('➕ story_nlu_fallback adicionada ao final de stories.yml')
    elif updated_any_story:
        print('♻️ Stories com nlu_fallback atualizadas para usar action_fallback em stories.yml')
    else:
        print('✔️ Stories de fallback já usam action_fallback em stories.yml')

    save_yaml(stories, stories_path)

def create_file_if_not_exists(path: str, default):
    if not os.path.exists(path):
        save_yaml(default, path)
        print(f"🆕 Arquivo criado: {path}")


def main():
    parser = argparse.ArgumentParser(description='Gera intents a partir de input.txt com respostas e exemplos PT/EN')
    parser.add_argument('input_file', nargs='?', default='input.txt', help='Arquivo de entrada (default: input.txt)')
    parser.add_argument('--max', dest='max_both', type=int, default=None, help='Limite global para PT e EN')
    parser.add_argument('--max-pt', dest='max_pt', type=int, default=None, help='Limite global apenas PT')
    parser.add_argument('--max-en', dest='max_en', type=int, default=None, help='Limite global apenas EN')
    args = parser.parse_args()

    input_file = args.input_file

    base_dir = 'data'
    domain_path = 'domain.yml'
    rules_path = os.path.join(base_dir, 'rules.yml')
    stories_path = os.path.join(base_dir, 'stories.yml')

    os.makedirs(base_dir, exist_ok=True)
    create_file_if_not_exists(domain_path, {
        'version': '3.1',
        'intents': [],
        'actions': [],
        'session_config': {
            'session_expiration_time': 60,
            'carry_over_slots_to_new_session': True
        }
    })
    create_file_if_not_exists(rules_path, {'version': '3.1', 'rules': []})
    create_file_if_not_exists(stories_path, {'version': '3.1', 'stories': []})

    # 1) Gera intents/respostas a partir do input (cria/atualiza arquivos em data/*)
    intents = parse_input(input_file, args.max_pt, args.max_en, args.max_both)
    for intent_path, pt, en, response_array in intents:
        name = normalize_intent_name(intent_path)
        print(f"📥 Criando intent '{name}' com {len(pt)} exemplos PT, {len(en)} EN e {len(response_array)} resposta(s)")
        create_files(intent_path, pt, en, response_array, base_dir)

    # 2) Depois de gerar os arquivos, sincroniza domain/rules/stories sem duplicar
    ensure_all_data_intents_registered(base_dir, domain_path, rules_path, stories_path)

    # 3) Por último, append da rule/story de fallback no fim dos arquivos (sem duplicar)
    append_fallback_rule_and_story(rules_path, stories_path)

    print("🎉 Finalizado: exemplos preservados, intents sincronizadas sem duplicar, e fallback rule/story + action_fallback garantidos!")


if __name__ == "__main__":
    main()
