from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

class ActionFallbackChatGPT(Action):
    def name(self) -> Text:
        return "action_fallback_chatgpt"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        last_user_message = tracker.latest_message.get("text")

        # 🔁 MOCK de resposta ChatGPT
        reply = (
            f"🤖 Não consegui entender exatamente sua pergunta: \"{last_user_message}\".\n"
            "Mas posso tentar ajudar! Pode reformular ou dar mais contexto?"
        )

        dispatcher.utter_message(text=reply)
        return []
