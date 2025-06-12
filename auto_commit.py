import subprocess
import datetime
import re
import tempfile
import os

DEFAULT_NAME = "Leonel Dorneles Porto"
DEFAULT_EMAIL = "leoneldornelesporto@outlook.com.br"

def run_command(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.strip()

def get_git_changes():
    result = run_command(["git", "status", "--porcelain"])
    lines = result.split('\n')

    changes = []
    for line in lines:
        if not line.strip():
            continue

        match = re.match(r"^(\S+)\s+(.*)$", line.strip())
        if match:
            status, file_path = match.groups()
            changes.append(f"[{status}] {file_path}")
        else:
            changes.append(f"[?] {line.strip()}")
    return changes

def ensure_git_user_info():
    name = run_command(["git", "config", "user.name"])
    email = run_command(["git", "config", "user.email"])

    if not name:
        subprocess.run(["git", "config", "--local", "user.name", DEFAULT_NAME], check=True)
        name = DEFAULT_NAME
    if not email:
        subprocess.run(["git", "config", "--local", "user.email", DEFAULT_EMAIL], check=True)
        email = DEFAULT_EMAIL

    return name, email

def get_current_branch():
    return run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])

def auto_commit():
    print("🔍 Verificando alterações no repositório Git...\n")
    changes = get_git_changes()

    if not changes:
        print("✅ Nenhuma alteração para commitar.\n")
        return

    username, email = ensure_git_user_info()
    branch = get_current_branch()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    subprocess.run(["git", "add", "."], check=True)

    commit_message = (
        f"🤖 Auto-commit realizado em {timestamp}\n"
        f"👤 Autor: {username} <{email}>\n"
        f"🌿 Branch: {branch}\n"
        f"📦 Arquivos alterados ({len(changes)}):\n"
        + "\n".join(changes)
    )

    # Escrever mensagem de commit em arquivo temporário para evitar limite de tamanho
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tf:
        tf.write(commit_message)
        temp_file = tf.name

    # Usar --file para ler a mensagem de commit do arquivo
    subprocess.run(["git", "commit", "--file", temp_file], check=True)

    # Remover arquivo temporário
    try:
        os.remove(temp_file)
    except OSError:
        pass

    print("\n🚀 Enviando commit para o repositório remoto...\n")
    subprocess.run(["git", "push", "origin", branch], check=True)

    print("✅ Commit e push concluídos com sucesso!")
    print("🔒 Detalhes salvos no histórico do Git.\n")

if __name__ == "__main__":
    auto_commit()
