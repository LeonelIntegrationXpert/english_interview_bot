import os
import re
import subprocess
import sys
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


def normalize_intent_name(intent_path):
    return intent_path.strip().replace('\\', '/').split('/')[-1]


def parse_input(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        blocks = f.read().split('---')

    intents = []
    seen_pt = set()
    seen_en = set()

    def dedupe_global(lines, seen_global):
        out = []
        for entry in lines:
            if entry and entry not in seen_global:
                seen_global.add(entry)
                out.append(entry)
        return out

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        intent = re.search(r'#intent:(.*)', block).group(1).strip()
        raw_pt = re.findall(r'#pt:(.*?)(?=#en:)', block, re.DOTALL)[0].splitlines()
        raw_en = re.findall(r'#en:(.*?)(?=#rp_|#vr_pt:)', block, re.DOTALL)[0].splitlines()

        response_blocks = re.findall(r'#vr_pt:(.*?)(?:#vr_en:(.*?))(?=#rp_|$)', block, re.DOTALL)

        response_array = []
        for pt, en in response_blocks:
            pt_clean = pt.strip()
            en_clean = en.strip()
            if pt_clean and en_clean:
                response_array.append((pt_clean, en_clean))

        clean_pt = [l.strip().lstrip('-').strip() for l in raw_pt if l.strip()]
        clean_en = [l.strip().lstrip('-').strip() for l in raw_en if l.strip()]

        unique_pt = list(dict.fromkeys(clean_pt))
        unique_en = list(dict.fromkeys(clean_en))

        pt_examples = dedupe_global(unique_pt, seen_pt)
        en_examples = dedupe_global(unique_en, seen_en)

        common = set(pt_examples) & set(en_examples)
        if common:
            pt_examples = [e for e in pt_examples if e not in common]
            en_examples = [e for e in en_examples if e not in common]

        pt_examples = pt_examples[:300]
        en_examples = en_examples[:300]

        if len(pt_examples) != len(en_examples):
            m = min(len(pt_examples), len(en_examples))
            pt_examples = pt_examples[:m]
            en_examples = en_examples[:m]

        intents.append((intent, pt_examples, en_examples, response_array))

    return intents


def clean_multiline_response(text):
    cleaned = text.strip()
    if cleaned.startswith('|'):
        cleaned = cleaned.lstrip('|').strip()
    return LiteralScalarString(cleaned + '\n')


def create_files(intent_path, pt_examples, en_examples, response_array, base_dir):
    name = normalize_intent_name(intent_path)
    folder = os.path.join(base_dir, *intent_path.split('/'))
    os.makedirs(folder, exist_ok=True)

    formatted_pt = ''.join(f"- {ex}\n" for ex in pt_examples)
    formatted_en = ''.join(f"- {ex}\n" for ex in en_examples)

    questions = {
        'version': '3.1',
        'nlu': [{
            'intent': name,
            'examples': LiteralScalarString(formatted_pt + formatted_en)
        }]
    }

    responses = {
        'version': '3.1',
        'responses': {
            f'utter_{name}': [
                {'custom': {
                    'vr_pt': clean_multiline_response(pt),
                    'vr_en': clean_multiline_response(en)
                }} for pt, en in response_array
            ]
        }
    }

    with open(os.path.join(folder, 'questions.yml'), 'w', encoding='utf-8') as qf:
        yaml.dump(questions, qf)
    with open(os.path.join(folder, 'responses.yml'), 'w', encoding='utf-8') as rf:
        yaml.dump(responses, rf)


def load_yaml(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.load(f) or {}
    return {}


def save_yaml(data, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)


def get_all_intents_in_data_folder(base_dir):
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


def ensure_all_data_intents_registered(base_dir, domain_path, rules_path, stories_path):
    domain = load_yaml(domain_path)
    rules = load_yaml(rules_path)
    stories = load_yaml(stories_path)

    domain.setdefault('intents', [])
    rules.setdefault('rules', [])
    stories.setdefault('stories', [])

    all_intents = [normalize_intent_name(i) for i in get_all_intents_in_data_folder(base_dir)]

    domain['intents'] = all_intents
    rules['rules'] = [
        {'rule': f"rule_{it}", 'steps': [{'intent': it}, {'action': f'utter_{it}'}]}
        for it in all_intents
    ]
    stories['stories'] = [
        {'story': f"story_{it}", 'steps': [{'intent': it}, {'action': f'utter_{it}'}]}
        for it in all_intents
    ]

    save_yaml(domain, domain_path)
    save_yaml(rules, rules_path)
    save_yaml(stories, stories_path)
    print(f"✅ Sincronizado domain, rules e stories com {len(all_intents)} intents.")


def create_file_if_not_exists(path, default):
    if not os.path.exists(path):
        save_yaml(default, path)
        print(f"🆕 Arquivo criado: {path}")


def main():
    input_file = 'input.txt'
    base_dir = 'data'
    domain_path = 'domain.yml'
    rules_path = os.path.join(base_dir, 'rules.yml')
    stories_path = os.path.join(base_dir, 'stories.yml')

    os.makedirs(base_dir, exist_ok=True)
    create_file_if_not_exists(domain_path, {
        'version': '3.1', 'intents': [], 'actions': [],
        'session_config': {
            'session_expiration_time': 60,
            'carry_over_slots_to_new_session': True
        }
    })
    create_file_if_not_exists(rules_path, {'version': '3.1', 'rules': []})
    create_file_if_not_exists(stories_path, {'version': '3.1', 'stories': []})

    ensure_all_data_intents_registered(base_dir, domain_path, rules_path, stories_path)

    intents = parse_input(input_file)
    for intent_path, pt, en, response_array in intents:
        name = normalize_intent_name(intent_path)
        print(f"📥 Criando intent '{name}' com {len(pt)} exemplos PT/EN")
        create_files(intent_path, pt, en, response_array, base_dir)

    print("🎉 Finalizado: duplicatas removidas e exemplos equalizados!")


if __name__ == "__main__":
    main()
