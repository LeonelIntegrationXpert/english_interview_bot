import subprocess
import datetime

def get_git_changes():
    # Executa git status --porcelain para obter os arquivos modificados de forma simples
    result = subprocess.run(["git", "status", "--porcelain"], stdout=subprocess.PIPE, text=True)
    lines = result.stdout.strip().split('\n')
    
    changes = []

    for line in lines:
        if not line:
            continue
        status = line[:2].strip()
        file_path = line[3:].strip()
        changes.append(f"[{status}] {file_path}")
    
    return changes

def auto_commit():
    changes = get_git_changes()
    
    if not changes:
        print("✅ Nenhuma alteração para commitar.")
        return

    # Adiciona todos os arquivos
    subprocess.run(["git", "add", "."], check=True)

    # Monta mensagem de commit com timestamp e arquivos
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_message = f"🤖 Auto-commit em {timestamp}\n\nArquivos:\n" + "\n".join(changes)

    # Realiza o commit
    subprocess.run(["git", "commit", "-m", commit_message], check=True)

    print("✅ Commit realizado com sucesso.")

if __name__ == "__main__":
    auto_commit()
