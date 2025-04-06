import subprocess
import os
import glob

def delete_old_models():
    model_dir = "models"
    if os.path.exists(model_dir):
        model_files = glob.glob(os.path.join(model_dir, "*.tar.gz"))
        if model_files:
            print(f"🧹 Removendo {len(model_files)} modelo(s) antigo(s) da pasta `{model_dir}`...")
            for file in model_files:
                os.remove(file)
            print("✅ Modelos antigos removidos com sucesso.\n")
        else:
            print(f"📁 Pasta `{model_dir}` encontrada, mas nenhum modelo .tar.gz para remover.\n")
    else:
        print(f"📁 Pasta `{model_dir}` não encontrada. Nenhuma ação necessária.\n")

def run_rasa_pipeline():
    try:
        delete_old_models()

        print("🔧 Treinando modelo com `rasa train`...\n")
        subprocess.run(["rasa", "train"], check=True)

        print("🚀 Iniciando o shell com `rasa shell`...\n")
        subprocess.run(["rasa", "shell"], check=True)
        #subprocess.run(["rasa", "shell", "nlu"], check=True)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao executar comando: {e}")

if __name__ == "__main__":
    run_rasa_pipeline()
