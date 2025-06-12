import os
import re
import subprocess
import sys

REQUIRED_PACKAGES = ["ruamel.yaml"]

def install_missing_packages():
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package.split('.')[0])
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_missing_packages()

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

yaml = YAML()
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.allow_unicode = True

def normalize_intent_name(intent_path):
    return intent_path.strip().split('/')[-1]

def parse_input(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read().split('---')

    intents = []
    for intent_block in content:
        intent = re.search(r'#intent:(.*)', intent_block).group(1).strip()
        pt_examples = re.findall(r'#pt:(.*?)(?:#en:)', intent_block, re.DOTALL)[0].strip().splitlines()
        en_examples = re.findall(r'#en:(.*?)(?:#vr_pt:)', intent_block, re.DOTALL)[0].strip().splitlines()
        vr_pt = re.findall(r'#vr_pt:(.*?)(?:#vr_en:)', intent_block, re.DOTALL)[0].strip()
        vr_en = re.findall(r'#vr_en:(.*)', intent_block, re.DOTALL)[0].strip()
        intents.append((intent, pt_examples, en_examples, vr_pt, vr_en))
    return intents

def clean_multiline_response(text):
    """
    Limpa espaços e prepara conteúdo multilinha para YAML sem duplicar '|'.
    Se houver '|' acidental no input.txt, remove automaticamente.
    """
    cleaned = text.strip()
    if cleaned.startswith('|'):
        cleaned = cleaned.lstrip('|').strip()
    return LiteralScalarString(cleaned + '\n')

def create_files(intent_path, pt_examples, en_examples, vr_pt, vr_en, base_dir):
    intent_folder = os.path.join(base_dir, *intent_path.split('/'))
    os.makedirs(intent_folder, exist_ok=True)
    intent_name = normalize_intent_name(intent_path)

    # Combine examples
    formatted_examples = '\n'.join(
        f"- {ex.strip('- ').strip()}"
        for ex in (pt_examples + en_examples)
        if ex.strip()
    ) + '\n'

    # Prepare questions.yml
    questions = {
        'version': '3.1',
        'nlu': [
            {
                'intent': intent_name,
                'examples': LiteralScalarString(formatted_examples)
            }
        ]
    }

    # Prepare responses.yml
    responses = {
        'version': '3.1',
        'responses': {
            f'utter_{intent_name}': [
                {
                    'custom': {
                        'response_array': [[
                            {'vr_pt': clean_multiline_response(vr_pt)},
                            {'vr_en': clean_multiline_response(vr_en)}
                        ]]
                    }
                }
            ]
        }
    }

    # Write questions.yml
    with open(os.path.join(intent_folder, 'questions.yml'), 'w', encoding='utf-8') as qf:
        yaml.dump(questions, qf)

    # Write responses.yml
    with open(os.path.join(intent_folder, 'responses.yml'), 'w', encoding='utf-8') as rf:
        yaml.dump(responses, rf)

def update_file(file_path, data_key, intent_path):
    intent_name = normalize_intent_name(intent_path)

    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.load(file) or {}
    else:
        data = {}

    if 'version' not in data:
        data['version'] = '3.1'
    if data_key not in data or data[data_key] is None:
        data[data_key] = []

    entry_name = f"{data_key[:-1]}_{intent_name}"
    existing = {entry.get('rule') or entry.get('story') for entry in data[data_key] if entry}

    if entry_name not in existing:
        data[data_key].append({
            'rule' if data_key == 'rules' else 'story': entry_name,
            'steps': [{'intent': intent_name}, {'action': f'utter_{intent_name}'}]
        })

        with open(file_path, 'w', encoding='utf-8') as file:
            yaml.dump(data, file)

def update_domain(intent_path, domain_path):
    intent_name = normalize_intent_name(intent_path)

    if os.path.exists(domain_path):
        with open(domain_path, 'r', encoding='utf-8') as file:
            domain_data = yaml.load(file) or {}
    else:
        domain_data = {}

    if 'intents' not in domain_data or domain_data['intents'] is None:
        domain_data['intents'] = []

    if intent_name not in domain_data['intents']:
        domain_data['intents'].append(intent_name)

        with open(domain_path, 'w', encoding='utf-8') as file:
            yaml.dump(domain_data, file)

def clean_up_files(base_dir, rules_path, stories_path, domain_path):
    existing_intents = set()
    for root, dirs, _ in os.walk(base_dir):
        for d in dirs:
            existing_intents.add(d)

    for path, key in [(rules_path, 'rules'), (stories_path, 'stories')]:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as file:
                data = yaml.load(file) or {}
            if key in data:
                data[key] = [e for e in data[key] if any(s.get('intent') in existing_intents for s in e.get('steps', []))]
            with open(path, 'w', encoding='utf-8') as file:
                yaml.dump(data, file)

    if os.path.exists(domain_path):
        with open(domain_path, 'r', encoding='utf-8') as file:
            domain_data = yaml.load(file) or {}
        domain_data['intents'] = [i for i in domain_data.get('intents', []) if i in existing_intents]
        with open(domain_path, 'w', encoding='utf-8') as file:
            yaml.dump(domain_data, file)

def main():
    input_file = 'input.txt'
    base_dir = 'data'
    rules_path = 'data/rules.yml'
    stories_path = 'data/stories.yml'
    domain_path = 'domain.yml'

    clean_up_files(base_dir, rules_path, stories_path, domain_path)
    intents = parse_input(input_file)
    for intent_path, pt, en, vr_pt, vr_en in intents:
        create_files(intent_path, pt, en, vr_pt, vr_en, base_dir)
        update_file(rules_path, 'rules', intent_path)
        update_file(stories_path, 'stories', intent_path)
        update_domain(intent_path, domain_path)

    print("🎉 Intents processadas com sucesso!")

if __name__ == "__main__":
    main()
