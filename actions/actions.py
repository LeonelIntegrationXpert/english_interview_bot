import logging
from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

logger = logging.getLogger(__name__)

class ActionFallback(Action):
    """
    🔄 Custom fallback action (MOCK).

    Esta ação é acionada quando o modelo não reconhece a intent com confiança suficiente.
    Retorna uma resposta mock sem chamar APIs externas.
    """

    def name(self) -> Text:
        """
        Nome desta action, conforme declarado em domain.yml.
        """
        return "action_fallback"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        """
        Executa o fallback mock:
        1. Obtém a última mensagem do usuário.
        2. Retorna uma mensagem solicitando reformulação.
        """
        user_message = tracker.latest_message.get("text", "")
        logger.debug(f"[ActionFallback MOCK] User message: {user_message!r}")

        # 🚧 MOCK response
        reply = (
            f"🤖 Não consegui entender exatamente sua pergunta: \"{user_message}\".\n"
            "Mas posso tentar ajudar! Pode reformular ou dar mais contexto?"
        )

        dispatcher.utter_message(text=reply)
        return []
