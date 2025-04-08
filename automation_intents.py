import os
import re
import yaml

# Lê o input.txt e separa as intents
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

# Cria arquivos questions.yml e responses.yml
def create_files(intent, pt_examples, en_examples, vr_pt, vr_en, base_dir):
    intent_folder = os.path.join(base_dir, intent)
    os.makedirs(intent_folder, exist_ok=True)

    questions = {
        'version': '3.1',
        'nlu': [{'intent': intent, 'examples': '\n'.join(
            ["# 🇧🇷 Português"] + [f"- {ex.strip()}" for ex in pt_examples if ex.strip()] +
            ["", "# 🇺🇸 English"] + [f"- {ex.strip()}" for ex in en_examples if ex.strip()]
        )}]
    }
    responses = {'responses': {f'utter_{intent}': [{'custom': {'response_array': [[{'vr_pt': vr_pt}, {'vr_en': vr_en}]]}}]}}

    with open(os.path.join(intent_folder, 'questions.yml'), 'w', encoding='utf-8') as qf:
        yaml.dump(questions, qf, allow_unicode=True, sort_keys=False)

    with open(os.path.join(intent_folder, 'responses.yml'), 'w', encoding='utf-8') as rf:
        yaml.dump(responses, rf, allow_unicode=True, sort_keys=False)

# Atualiza os arquivos existentes rules.yml, stories.yml e domain.yml
def update_file(file_path, data_key, intent):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)
    else:
        data = {'version': '3.1', data_key: []}

    entry = {'rule' if data_key == 'rules' else 'story': f'{data_key[:-1]}_{intent}',
             'steps': [{'intent': intent}, {'action': f'utter_{intent}'}]}

    data[data_key].append(entry)

    with open(file_path, 'w', encoding='utf-8') as file:
        yaml.dump(data, file, allow_unicode=True, sort_keys=False)

# Atualiza o domain.yml
def update_domain(intent, domain_path):
    with open(domain_path, 'r', encoding='utf-8') as file:
        domain_data = yaml.safe_load(file)

    if 'intents' not in domain_data:
        domain_data['intents'] = []

    if intent not in domain_data['intents']:
        domain_data['intents'].append(intent)

    with open(domain_path, 'w', encoding='utf-8') as file:
        yaml.dump(domain_data, file, allow_unicode=True, sort_keys=False)

# Execução Principal
def main():
    input_file = 'input.txt'
    base_dir = 'data/mulesoft/dataweave'
    intents = parse_input(input_file)

    for intent, pt_examples, en_examples, vr_pt, vr_en in intents:
        create_files(intent, pt_examples, en_examples, vr_pt, vr_en, base_dir)

        update_file('data/rules.yml', 'rules', intent)
        update_file('data/stories.yml', 'stories', intent)
        update_domain(intent, 'domain.yml')

    print("🎉 Arquivos gerados e atualizados com sucesso!")

if __name__ == "__main__":
    main()