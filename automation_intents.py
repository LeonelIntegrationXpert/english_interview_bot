import os
import re
import yaml

def parse_input_txt(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    intent = re.search(r'#intent:(.*)', content).group(1).strip()

    pt_examples = re.findall(r'#pt:(.*?)(?:#en:|#vr_pt:)', content, re.DOTALL)[0].strip().splitlines()
    en_examples = re.findall(r'#en:(.*?)(?:#vr_pt:|#vr_en:)', content, re.DOTALL)[0].strip().splitlines()

    vr_pt = re.findall(r'#vr_pt:(.*?)(?:#vr_en:)', content, re.DOTALL)[0].strip()
    vr_en = re.findall(r'#vr_en:(.*)', content, re.DOTALL)[0].strip()

    return intent, pt_examples, en_examples, vr_pt, vr_en

def create_nlu(intent, pt_examples, en_examples, intent_folder):
    nlu_data = {
        'version': '3.1',
        'nlu': [
            {
                'intent': intent,
                'examples': "\n".join(
                    ["# 🇧🇷 Português"] + [f"- {ex.strip()}" for ex in pt_examples if ex.strip()] +
                    ["", "# 🇺🇸 English"] + [f"- {ex.strip()}" for ex in en_examples if ex.strip()]
                )
            }
        ]
    }

    nlu_file_path = os.path.join(intent_folder, 'questions.yml')
    with open(nlu_file_path, 'w', encoding='utf-8') as file:
        yaml.dump(nlu_data, file, allow_unicode=True, sort_keys=False)

    print(f"✅ NLU gerado: {nlu_file_path}")

def create_responses(intent, vr_pt, vr_en, intent_folder):
    responses_data = {
        'responses': {
            f'utter_{intent}': [
                {
                    'custom': {
                        'response_array': [
                            [
                                {'vr_pt': vr_pt},
                                {'vr_en': vr_en}
                            ]
                        ]
                    }
                }
            ]
        }
    }

    responses_file_path = os.path.join(intent_folder, 'responses.yml')
    with open(responses_file_path, 'w', encoding='utf-8') as file:
        yaml.dump(responses_data, file, allow_unicode=True, sort_keys=False)

    print(f"✅ Responses gerado: {responses_file_path}")

def create_story(intent, intent_folder):
    stories_data = {
        'version': '3.1',
        'stories': [
            {
                'story': f'story_{intent}',
                'steps': [
                    {'intent': intent},
                    {'action': f'utter_{intent}'}
                ]
            }
        ]
    }

    stories_file_path = os.path.join(intent_folder, 'stories.yml')
    with open(stories_file_path, 'w', encoding='utf-8') as file:
        yaml.dump(stories_data, file, allow_unicode=True, sort_keys=False)

    print(f"✅ Stories gerado: {stories_file_path}")

def create_rule(intent, intent_folder):
    rules_data = {
        'version': '3.1',
        'rules': [
            {
                'rule': f'rule_{intent}',
                'steps': [
                    {'intent': intent},
                    {'action': f'utter_{intent}'}
                ]
            }
        ]
    }

    rules_file_path = os.path.join(intent_folder, 'rules.yml')
    with open(rules_file_path, 'w', encoding='utf-8') as file:
        yaml.dump(rules_data, file, allow_unicode=True, sort_keys=False)

    print(f"✅ Rules gerado: {rules_file_path}")

def main():
    input_file = 'input.txt'
    output_base_dir = os.path.join('data', 'mulesoft', 'dataweave')

    intent, pt_examples, en_examples, vr_pt, vr_en = parse_input_txt(input_file)

    # Criação automática da subpasta da intent
    intent_folder = os.path.join(output_base_dir, intent)
    os.makedirs(intent_folder, exist_ok=True)

    # Gerar arquivos do Rasa
    create_nlu(intent, pt_examples, en_examples, intent_folder)
    create_responses(intent, vr_pt, vr_en, intent_folder)
    create_story(intent, intent_folder)
    create_rule(intent, intent_folder)

    print(f"🎉 Todos os arquivos foram gerados com sucesso na pasta '{intent_folder}'!")

if __name__ == "__main__":
    main()
