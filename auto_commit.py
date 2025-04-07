import subprocess
import datetime

def run_command(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.strip()

def get_git_changes():
    result = run_command(["git", "status", "--porcelain"])
    lines = result.split('\n')

    changes = []
    for line in lines:
        if not line:
            continue
        status = line[:2].strip()
        file_path = line[3:].strip()
        changes.append(f"[{status}] {file_path}")
    
    return changes

def get_git_user_info():
    name = run_command(["git", "config", "user.name"])
    email = run_command(["git", "config", "user.email"])
    return name, email

def get_current_branch():
    return run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])

def auto_commit():
    print("🔍 Verificando alterações no repositório Git...\n")
    changes = get_git_changes()

    if not changes:
        print("✅ Nenhuma alteração para commitar.\n")
        return

    # Coleta informações
    username, email = get_git_user_info()
    branch = get_current_branch()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Adiciona arquivos
    subprocess.run(["git", "add", "."], check=True)

    # Monta mensagem de commit
    commit_message = (
        f"🤖 Auto-commit realizado em {timestamp}\n"
        f"👤 Autor: {username} <{email}>\n"
        f"🌿 Branch: {branch}\n"
        f"📦 Arquivos alterados ({len(changes)}):\n"
        + "\n".join(changes)
    )

    # Faz o commit
    subprocess.run(["git", "commit", "-m", commit_message], check=True)

    # Push para o branch atual
    print("\n🚀 Enviando commit para o repositório remoto...\n")
    subprocess.run(["git", "push", "origin", branch], check=True)

    print("✅ Commit e push concluídos com sucesso!")
    print("🔒 Detalhes salvos no histórico do Git.\n")

if __name__ == "__main__":
    auto_commit()
