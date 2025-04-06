<!-- Banner animado -->
<h1 align="center">🤖 English Interview Bot - Rasa Project</h1>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:4C83FF,100:0059FF&height=220&section=header&text=Rasa%20MuleSoft%20Bot&fontSize=40&fontColor=ffffff&animation=fadeIn" alt="Rasa Project" />
</p>

<p align="center">
  <a href="https://rasa.com">
    <img src="https://img.shields.io/badge/Rasa-3.x-purple.svg?logo=rasa" alt="Rasa" />
  </a>
  <a href="https://www.python.org">
    <img src="https://img.shields.io/badge/Python-3.9-blue?logo=python" alt="Python" />
  </a>
  <a href="#">
    <img src="https://img.shields.io/badge/NLP-Bot-brightgreen" alt="NLP" />
  </a>
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&pause=1000&color=47E3FF&center=true&vCenter=true&width=600&lines=Chatbot+com+Rasa+e+Python+3.9;Entrevistas+em+Ingl%C3%AAs+com+foco+em+MuleSoft;API-led+connectivity%2C+System%2C+Process+e+Experience+APIs" alt="Typing SVG" />
</p>

---

## 📌 Visão Geral
Este projeto foi criado com o objetivo de auxiliar desenvolvedores a se prepararem para entrevistas técnicas sobre **MuleSoft** e conceitos de integração como **API-led Connectivity**, abordando todas as camadas da arquitetura: **System API**, **Process API** e **Experience API**.

---

## 📚 Funcionalidades

- Chatbot treinado com **Rasa 3.x**
- Pipeline NLU com `DIETClassifier` e `CountVectorsFeaturizer`
- Intents específicas como `explain_mulesoft`, `explain_api_led`, `explain_experience_api`, `explain_process_api` e `explain_system_api`
- Respostas bem formuladas e técnicas para perguntas de entrevistas
- Execução via `rasa train`, `rasa shell` ou endpoints REST (Postman)
- Totalmente em **português**, focado no mercado brasileiro

---

## 🛠️ Como Executar

### Requisitos:
- Python 3.9+
- Virtualenv (opcional, mas recomendado)
- Rasa 3.x

### Passos:
```bash
# 1. Clone o repositório
$ git clone https://github.com/seu-usuario/english-interview-bot.git
$ cd english-interview-bot

# 2. Crie um ambiente virtual (opcional)
$ python -m venv venv
$ source venv/bin/activate  # ou venv\Scripts\activate no Windows

# 3. Instale as dependências
$ pip install -r requirements.txt

# 4. Treine o modelo
$ rasa train

# 5. Inicie o bot
$ rasa shell

# 6. Para testar via API REST:
$ rasa run --enable-api --cors "*" --debug
```

### Teste com curl ou Postman
```bash
curl -X POST http://localhost:5005/webhooks/rest/webhook \
  -H "Content-Type: application/json" \
  -d '{"sender": "test_user", "message": "O que é MuleSoft?"}'
```

---

## 🧠 Intents Suportadas

- `explain_mulesoft`: O que é MuleSoft?
- `explain_api_led`: Conceito de API-led e suas camadas
- `explain_experience_api`: Detalhes sobre a camada Experience API
- `explain_process_api`: Detalhes sobre a camada Process API
- `explain_system_api`: Detalhes sobre a camada System API

---

## 🗂️ Estrutura do Projeto
```
english-interview-bot/
├── data/
│   ├── nlu.yml              # Perguntas e exemplos por intent
│   ├── rules.yml            # Regras simples de resposta
│   └── stories.yml          # Histórias de diálogo
├── domain.yml               # Intents, slots, responses e config do domínio
├── config.yml               # Pipeline de NLP e políticas
├── actions/                 # (Opcional) ações customizadas em Python
├── models/                  # Modelos treinados
└── README.md                # Este documento
```

---

## ✨ Exemplo de Pergunta e Resposta

**Usuário**: O que é API-led?

**Bot**:
> API-led é um modelo de arquitetura criado pela MuleSoft que organiza as integrações em três camadas: System, Process e Experience APIs...

---

## 🙌 Contato

**Leonel Dorneles Porto**  
📧 [leoneldornelesporto@outlook.com.br](mailto:leoneldornelesporto@outlook.com.br)  
📱 +55 53 99180-4869  
🔗 [linkedin.com/in/leonel-dorneles-porto-b88600122](https://www.linkedin.com/in/leonel-dorneles-porto-b88600122)

---

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0059FF,100:4C83FF&height=100&section=footer" />
</p>
