import subprocess
import os
import glob
import sys
import shutil
import time

VENV_DIR = "venv_rasa"
PYTHON_INSTALLER_PATH = "installers/python-3.10.9-amd64.exe"
MIN_SUPPORTED_VERSION = (3, 8)
MAX_SUPPORTED_VERSION = (3, 11)

def print_header(msg):
    print("\n" + "="*60)
    print(f"{msg}")
    print("="*60 + "\n")

def is_python_supported():
    return MIN_SUPPORTED_VERSION <= sys.version_info <= MAX_SUPPORTED_VERSION

def install_python():
    print_header("⚙️ Python versão suportada não encontrada. Tentando instalar...")
    if os.path.exists(PYTHON_INSTALLER_PATH):
        subprocess.run([PYTHON_INSTALLER_PATH], check=True)
        print("✅ Instalação iniciada. Após concluir, reabra o terminal e execute novamente o script.")
    else:
        print(f"❌ Instalador não encontrado em `{PYTHON_INSTALLER_PATH}`. Instale manualmente.")
    sys.exit(1)

def create_virtualenv():
    if not os.path.exists(VENV_DIR):
        print_header(f"🐍 Criando ambiente virtual `{VENV_DIR}`...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
        print("✅ Ambiente virtual criado.")
        print("\n⚡ Ative o ambiente e execute este script novamente.\n")
        sys.exit(0)
    else:
        print(f"✅ Ambiente virtual `{VENV_DIR}` já existe.\n")

def install_dependencies():
    print_header("📦 Verificando dependências...")
    required_packages = [
        "rasa",
        "rasa-sdk",
        "tensorflow",
        "transformers",    # necessário para LanguageModelFeaturizer
        "sentencepiece"    # necessário para tokenização de subpalavras
    ]
    for package in required_packages:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "show", package],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print(f"✅ Pacote `{package}` já instalado.")
        except subprocess.CalledProcessError:
            print(f"📦 Instalando `{package}`...")
            subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)
    print("\n✅ Todas as dependências estão instaladas.\n")

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def delete_old_models():
    print_header("🧹 Limpando modelos antigos...")
    model_dir = "models"
    if os.path.exists(model_dir):
        model_files = glob.glob(os.path.join(model_dir, "*.tar.gz"))
        if model_files:
            print(f"🧹 Removendo {len(model_files)} modelo(s) antigo(s)...")
            for file in model_files:
                os.remove(file)
            print("✅ Modelos antigos removidos.")
        else:
            print("📁 Nenhum modelo `.tar.gz` encontrado.")
    else:
        print("📁 Pasta `models` não encontrada. Nenhuma ação necessária.")

def delete_caches():
    print_header("🧹 Limpando caches...")
    cache_dirs = [".rasa", "__pycache__"]
    pyc_files = glob.glob("**/*.pyc", recursive=True)

    for dir_name in cache_dirs:
        if os.path.exists(dir_name):
            print(f"🧹 Removendo cache `{dir_name}`...")
            shutil.rmtree(dir_name, ignore_errors=True)

    for file in pyc_files:
        try:
            os.remove(file)
        except Exception as e:
            print(f"⚠️ Erro ao remover {file}: {e}")

    print("✅ Caches limpos.\n")

def run_rasa_pipeline(test_file=None):
    install_dependencies()
    clear_console()
    delete_caches()
    delete_old_models()

    # Inicia o servidor de ações customizadas em background
    print_header("🚀 Iniciando servidor de ações customizadas...")
    action_proc = subprocess.Popen(
        [sys.executable, "-m", "rasa", "run", "actions"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # dá um tempo para o server subir antes de prosseguir
    time.sleep(5)

    try:
        python_cmd = [sys.executable, "-m", "rasa"]

        if test_file:
            test_path = os.path.join("tests", test_file)
            if not os.path.isfile(test_path):
                print(f"❌ Arquivo de teste `{test_path}` não encontrado.")
                return
            print_header("🧪 Executando testes do Rasa...")
            subprocess.run(python_cmd + ["test", "--stories", test_path], check=True)
        else:
            print_header("🔧 Treinando modelo do Rasa...")
            subprocess.run(python_cmd + ["train"], check=True)

            print_header("🚀 Iniciando o Rasa Server...")
            subprocess.run(
                python_cmd + ["run", "--enable-api", "--cors", "*", "--debug"],
                check=True
            )
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao executar comando: {e}")
    finally:
        # Finaliza o servidor de ações
        print_header("🛑 Finalizando servidor de ações customizadas...")
        action_proc.terminate()
        action_proc.wait()

def try_find_and_use_python310():
    local_programs = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Python')
    if not os.path.exists(local_programs):
        return False

    python_versions = [d for d in os.listdir(local_programs) if os.path.isdir(os.path.join(local_programs, d))]
    python_310 = next((v for v in python_versions if v.startswith('Python310')), None)
    if python_310:
        python_310_exe = os.path.join(local_programs, python_310, 'python.exe')
        if os.path.exists(python_310_exe):
            print_header(f"⚡ Python 3.10 detectado em `{python_310_exe}`.")
            print("🔁 Reexecutando o script usando Python 3.10...\n")
            subprocess.run([python_310_exe, __file__, *sys.argv[1:]])
            sys.exit(0)
    return False

if __name__ == "__main__":
    clear_console()
    print_header("🚀 Setup e pipeline Rasa Automatizados")

    if not is_python_supported():
        if not try_find_and_use_python310():
            install_python()

    create_virtualenv()

    # Se quiser passar um arquivo de teste, basta fazer:
    #   python run_rasa.py nome_do_teste.yml
    test_file = sys.argv[1] if len(sys.argv) > 1 else None
    run_rasa_pipeline(test_file)
