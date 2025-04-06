import subprocess

def run_rasa_pipeline():
    try:
        print("🔧 Treinando modelo com `rasa train`...")
        subprocess.run(["rasa", "train"], check=True)

        print("\n🚀 Iniciando o shell com `rasa shell`...\n")
        subprocess.run(["rasa", "shell"], check=True)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao executar comando: {e}")

if __name__ == "__main__":
    run_rasa_pipeline()
