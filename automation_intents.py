import os
import re
import subprocess
import sys

# Instala dependências necessárias, se não existirem
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
yaml.allow_unicode = True  # Permite salvar emojis diretamente

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

def create_files(intent, pt_examples, en_examples, vr_pt, vr_en, base_dir):
    intent_folder = os.path.join(base_dir, intent)
    os.makedirs(intent_folder, exist_ok=True)

    formatted_examples = (
        "# 🌟 Português\n" +
        '\n'.join(f"- {ex.strip('- ').strip()}" for ex in pt_examples if ex.strip()) +
        "\n\n# 🇺🇸 English\n" +
        '\n'.join(f"- {ex.strip('- ').strip()}" for ex in en_examples if ex.strip())
    )

    questions = {
        'version': '3.1',
        'nlu': [
            {
                'intent': intent,
                'examples': LiteralScalarString(formatted_examples)
            }
        ]
    }

    responses = {
        'responses': {
            f'utter_{intent}': [
                {
                    'custom': {
                        'response_array': [
                            [
                                {'vr_pt': LiteralScalarString(vr_pt.strip())},
                                {'vr_en': LiteralScalarString(vr_en.strip())}
                            ]
                        ]
                    }
                }
            ]
        }
    }

    with open(os.path.join(intent_folder, 'questions.yml'), 'w', encoding='utf-8') as qf:
        yaml.dump(questions, qf)

    with open(os.path.join(intent_folder, 'responses.yml'), 'w', encoding='utf-8') as rf:
        yaml.dump(responses, rf)

def update_file(file_path, data_key, intent):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.load(file) or {}
    else:
        data = {}

    if 'version' not in data:
        data['version'] = '3.1'
    if data_key not in data or data[data_key] is None:
        data[data_key] = []

    entry_name = f"{data_key[:-1]}_{intent}"
    existing_names = set()
    for entry in data[data_key]:
        if entry:
            name = entry.get('rule') or entry.get('story')
            if name:
                existing_names.add(name)

    if entry_name not in existing_names:
        entry = {
            'rule' if data_key == 'rules' else 'story': entry_name,
            'steps': [{'intent': intent}, {'action': f'utter_{intent}'}]
        }
        data[data_key].append(entry)

        with open(file_path, 'w', encoding='utf-8') as file:
            yaml.dump(data, file)

def clean_up_files(base_dir, rules_path, stories_path, domain_path):
    existing_intents = set(name for name in os.listdir(base_dir)
                           if os.path.isdir(os.path.join(base_dir, name)))

    for path, key in [(rules_path, 'rules'), (stories_path, 'stories')]:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as file:
                data = yaml.load(file) or {}

            if key in data:
                data[key] = [
                    entry for entry in data[key]
                    if entry.get('steps') and any(
                        step.get('intent') in existing_intents for step in entry.get('steps')
                    )
                ]

                with open(path, 'w', encoding='utf-8') as file:
                    yaml.dump(data, file)

    if os.path.exists(domain_path):
        with open(domain_path, 'r', encoding='utf-8') as file:
            domain_data = yaml.load(file) or {}

        if 'intents' not in domain_data or domain_data['intents'] is None:
            domain_data['intents'] = []

        domain_data['intents'] = [i for i in domain_data['intents'] if i in existing_intents]

        with open(domain_path, 'w', encoding='utf-8') as file:
            yaml.dump(domain_data, file)

def update_domain(intent, domain_path):
    if os.path.exists(domain_path):
        with open(domain_path, 'r', encoding='utf-8') as file:
            domain_data = yaml.load(file) or {}
    else:
        domain_data = {}

    if 'intents' not in domain_data or domain_data['intents'] is None:
        domain_data['intents'] = []

    if intent not in domain_data['intents']:
        domain_data['intents'].append(intent)

        with open(domain_path, 'w', encoding='utf-8') as file:
            yaml.dump(domain_data, file)

def verify_existing_intents(base_dir, rules_path, stories_path, domain_path):
    existing_intents = [name for name in os.listdir(base_dir)
                        if os.path.isdir(os.path.join(base_dir, name))]

    for intent in existing_intents:
        update_file(rules_path, 'rules', intent)
        update_file(stories_path, 'stories', intent)
        update_domain(intent, domain_path)

def main():
    input_file = 'input.txt'
    base_dir = 'data/mulesoft/api_led'
    rules_path = 'data/rules.yml'
    stories_path = 'data/stories.yml'
    domain_path = 'domain.yml'

    clean_up_files(base_dir, rules_path, stories_path, domain_path)
    verify_existing_intents(base_dir, rules_path, stories_path, domain_path)

    intents = parse_input(input_file)
    for intent, pt_examples, en_examples, vr_pt, vr_en in intents:
        create_files(intent, pt_examples, en_examples, vr_pt, vr_en, base_dir)
        update_file(rules_path, 'rules', intent)
        update_file(stories_path, 'stories', intent)
        update_domain(intent, domain_path)

    print("🎉 Processo concluído com sucesso!")

if __name__ == "__main__":
    main()