#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Testa exemplos contra um modelo Rasa rodando via HTTP
e gera um relatório de acertos/erros (modo rotulado)
ou apenas de previsões (modo texto livre).

Uso básico (dataset rotulado tipo input.txt):
    python test_intents_from_input.py

Com parâmetros:
    python test_intents_from_input.py \
        --input-file input.txt \
        --rasa-url http://localhost:5005/model/parse \
        --max 50 \
        --errors-file reports/errors_labeled.csv \
        --workers 8 \
        --progress-every 50

Dataset de texto livre (ex: input_val.txt, só perguntas):
    python test_intents_from_input.py \
        --input-file input_val.txt \
        --rasa-url http://localhost:5005/model/parse \
        --errors-file reports/predictions_free.csv \
        --workers 8 \
        --progress-every 50
"""

import os
import re
import sys
import json
import time
import argparse
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Dependências mínimas
REQUIRED_PACKAGES = ["requests"]


def install_missing_packages():
    """Instala pacotes Python que estiverem faltando."""
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package.split('.')[0])
        except ImportError:
            print(f"📦 Instalando dependência: {package} ...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])


install_missing_packages()
import requests  # noqa: E402


# =============== PARSE DO INPUT.TXT (FORMATO LEONEL) ================== #

def slug_to_phrase(slug: str) -> str:
    phrase = re.sub(r'[._\\-/]+', ' ', slug).strip()
    phrase = re.sub(r'\s+', ' ', phrase)
    return phrase or slug


def parse_limits(block: str,
                 global_max_pt: int | None,
                 global_max_en: int | None,
                 global_max: int | None):
    """Lógica de #max_pt / #max_en / #max igual ao seu outro script."""
    def pick(tag):
        m = re.search(rf"(?mi)^\s*{tag}\s*:\s*(\d+)\s*$", block)
        return int(m.group(1)) if m else None

    max_pt = pick('#max_pt')
    max_en = pick('#max_en')
    max_both = pick('#max')

    # Precedência: específicos > #max > globais
    if max_both is not None:
        max_pt = max_pt if max_pt is not None else max_both
        max_en = max_en if max_en is not None else max_both

    if max_pt is None:
        max_pt = global_max_pt if global_max_pt is not None else (global_max if global_max is not None else None)
    if max_en is None:
        max_en = global_max_en if global_max_en is not None else (global_max if global_max is not None else None)

    return max_pt, max_en


def extract_list(block: str, tag: str):
    """
    Extrai lista de exemplos após '#pt:' ou '#en:' até o próximo marcador/tag/---/EOF,
    ignorando linhas de controle tipo '#rp_1', '#rp', etc.
    """
    m = re.search(rf'(?smi)^\s*{re.escape(tag)}\s*:(.*?)(?=^\s*#\w+\s*:|^\s*---\s*$|\Z)', block)
    if not m:
        return []

    out = []
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        cleaned = ln.strip().lstrip('-').strip()
        if not cleaned:
            continue
        # NÃO pode entrar nada que seja marcador de regra/peso: #rp, #rp_1, etc
        if re.match(r"^\s*#rp", cleaned, flags=re.IGNORECASE):
            continue
        out.append(cleaned)

    return out


def parse_input_examples(file_path: str,
                         global_max_pt: int | None,
                         global_max_en: int | None,
                         global_max: int | None):
    """
    Lê o input.txt e devolve uma lista de casos de teste (modo ROTULADO):

    [
      {"intent": "greeting/hello", "lang": "pt", "text": "oi"},
      {"intent": "greeting/hello", "lang": "en", "text": "hi"},
      ...
    ]
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = [b for b in re.split(r'(?m)^\s*---\s*$', content) if b.strip()]

    test_cases = []
    seen_pt, seen_en = set(), set()
    ignored_blocks = 0

    def dedupe_global(lines, seen_global):
        out = []
        for entry in lines:
            if entry and entry not in seen_global:
                seen_global.add(entry)
                out.append(entry)
        return out

    for idx, raw in enumerate(blocks, 1):
        block = raw.strip()
        if not block:
            continue

        m_intent = re.search(r'(?mi)^\s*#intent\s*:\s*(.+)$', block)
        if not m_intent:
            # Aqui é ok ignorar, esse modo é só pra arquivo rotulado
            ignored_blocks += 1
            continue
        intent = m_intent.group(1).strip()

        # limites desse bloco (seguindo mesma lógica do script de geração)
        max_pt, max_en = parse_limits(block, global_max_pt, global_max_en, global_max)

        pt_examples = extract_list(block, '#pt')
        en_examples = extract_list(block, '#en')

        # dedupe global por idioma
        pt_examples = dedupe_global(list(dict.fromkeys(pt_examples)), seen_pt)
        en_examples = dedupe_global(list(dict.fromkeys(en_examples)), seen_en)

        # aplicar limites
        if max_pt is not None:
            pt_examples = pt_examples[:max_pt]
        if max_en is not None:
            en_examples = en_examples[:max_en]

        for ex in pt_examples:
            test_cases.append({"intent": intent, "lang": "pt", "text": ex})
        for ex in en_examples:
            test_cases.append({"intent": intent, "lang": "en", "text": ex})

    if ignored_blocks:
        print(f"ℹ️  {ignored_blocks} bloco(s) ignorados por falta de #intent: (modo rotulado).")

    return test_cases


# =============== PARSE DE ARQUIVO TEXTO LIVRE (SEM #intent) =============== #

def parse_unlabeled_file(file_path: str):
    """
    Lê um arquivo com perguntas livres, uma por linha (sem #intent),
    e devolve casos de teste com intent=None:

    [
      {"intent": None, "lang": "?", "text": "oi, tudo bem com você hoje?"},
      ...
    ]
    """
    cases = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            # Se quiser, dá pra ignorar comentários começando com '#'
            if text.startswith('#'):
                continue
            cases.append({"intent": None, "lang": "?", "text": text})

    return cases


# =============== NORMALIZAÇÃO DE INTENTS ================== #

def normalize_intent(name: str | None) -> str | None:
    """
    Normaliza nome de intent para comparação.

    Exemplos:
      "greeting/hello" -> "hello"
      "softskills/receiving_feedback" -> "receiving_feedback"
      "hello" -> "hello"
      None -> None
    """
    if not name:
        return None
    # pega só a última parte depois de "/", se existir
    return name.split('/')[-1].strip()


# =============== TESTE CONTRA O RASA VIA HTTP ================== #

def call_rasa_parse(rasa_url: str, text: str, timeout: float = 10.0):
    """
    Chama o /model/parse do Rasa e devolve (intent_name, confidence, raw_json).
    """
    payload = {"text": text}
    try:
        resp = requests.post(rasa_url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return None, 0.0, {"error": f"HTTP error: {e}"}

    if resp.status_code != 200:
        return None, 0.0, {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return None, 0.0, {"error": f"Invalid JSON response: {resp.text[:200]}"}

    intent = data.get("intent") or {}
    intent_name = intent.get("name")
    confidence = float(intent.get("confidence") or 0.0)
    return intent_name, confidence, data


def run_tests(test_cases,
              rasa_url: str,
              sleep_between: float = 0.0,
              max_tests: int | None = None,
              progress_every: int = 25,
              workers: int = 4,
              labeled: bool = True):
    """
    Executa os testes contra o Rasa e devolve:
      - stats
      - records (erros em modo rotulado, ou todas as previsões em modo texto livre)

    labeled=True  -> compara expected vs predicted, calcula acurácia.
    labeled=False -> ignora expected, só conta distribuição de intents preditas.
    """
    total_cases = len(test_cases)
    if max_tests is not None:
        test_cases = test_cases[:max_tests]

    n_cases = len(test_cases)
    modo = "ROTULADO (#intent)" if labeled else "TEXTO LIVRE (sem #intent)"
    print(f"🔎 Modo: {modo}")
    print(f"🔎 Rodando testes em {n_cases} exemplos (de {total_cases} disponíveis)...")

    stats = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0})
    records = []  # erros (labeled) ou todas as previsões (unlabeled)

    # Se workers <= 1, modo sequencial
    if workers <= 1:

        for idx, case in enumerate(test_cases, 1):
            expected = case["intent"]  # pode ser None em modo unlabeled
            text = case["text"]
            lang = case["lang"]

            pred, conf, raw = call_rasa_parse(rasa_url, text)

            if labeled:
                norm_expected = normalize_intent(expected)
                norm_pred = normalize_intent(pred)
                ok = (norm_pred == norm_expected)
                key = expected or "__NO_INTENT__"
            else:
                ok = True  # não faz sentido "erro" aqui
                key = pred or "__NO_INTENT__"

            stats[key]["total"] += 1
            if labeled:
                if ok:
                    stats[key]["correct"] += 1
                else:
                    stats[key]["wrong"] += 1
                    records.append({
                        "lang": lang,
                        "text": text,
                        "expected": expected,
                        "predicted": pred,
                        "confidence": conf,
                        "raw": raw,
                    })
            else:
                # Em modo texto livre, guardamos TODAS as previsões em records
                records.append({
                    "lang": lang,
                    "text": text,
                    "expected": expected,
                    "predicted": pred,
                    "confidence": conf,
                    "raw": raw,
                })

            if progress_every > 0 and (idx % progress_every == 0 or idx == n_cases):
                current_total = sum(v["total"] for v in stats.values())
                if labeled:
                    current_correct = sum(v["correct"] for v in stats.values())
                    acc = (current_correct / current_total * 100.0) if current_total else 0.0
                    pct = idx / n_cases * 100.0
                    print(
                        f"  • {idx}/{n_cases} exemplos processados "
                        f"({pct:5.1f}%) | acurácia parcial: {acc:5.2f}%"
                    )
                else:
                    pct = idx / n_cases * 100.0
                    print(
                        f"  • {idx}/{n_cases} exemplos processados "
                        f"({pct:5.1f}%)"
                    )

            if sleep_between > 0:
                time.sleep(sleep_between)

        return stats, records

    # ---------- MODO MULTITHREAD ----------
    def worker(case):
        """Função executada em cada thread."""
        expected = case["intent"]
        text = case["text"]
        lang = case["lang"]

        pred, conf, raw = call_rasa_parse(rasa_url, text)
        return {
            "expected": expected,
            "lang": lang,
            "text": text,
            "predicted": pred,
            "confidence": conf,
            "raw": raw,
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker, case) for case in test_cases]

        for idx, fut in enumerate(as_completed(futures), 1):
            try:
                res = fut.result()
            except Exception as e:
                # Se der erro inesperado na thread, registra como erro genérico
                res = {
                    "expected": None,
                    "lang": "??",
                    "text": "",
                    "predicted": None,
                    "confidence": 0.0,
                    "raw": {"error": f"worker exception: {e}"},
                }

            expected = res["expected"]
            lang = res["lang"]
            text = res["text"]
            pred = res["predicted"]
            conf = res["confidence"]
            raw = res["raw"]

            if labeled:
                norm_expected = normalize_intent(expected)
                norm_pred = normalize_intent(pred)
                ok = (norm_pred == norm_expected)
                key = expected or "__NO_INTENT__"
            else:
                ok = True
                key = pred or "__NO_INTENT__"

            stats[key]["total"] += 1
            if labeled:
                if ok:
                    stats[key]["correct"] += 1
                else:
                    stats[key]["wrong"] += 1
                    records.append({
                        "lang": lang,
                        "text": text,
                        "expected": expected,
                        "predicted": pred,
                        "confidence": conf,
                        "raw": raw,
                    })
            else:
                records.append({
                    "lang": lang,
                    "text": text,
                    "expected": expected,
                    "predicted": pred,
                    "confidence": conf,
                    "raw": raw,
                })

            if progress_every > 0 and (idx % progress_every == 0 or idx == n_cases):
                current_total = sum(v["total"] for v in stats.values())
                if labeled:
                    current_correct = sum(v["correct"] for v in stats.values())
                    acc = (current_correct / current_total * 100.0) if current_total else 0.0
                    pct = idx / n_cases * 100.0
                    print(
                        f"  • {idx}/{n_cases} exemplos processados "
                        f"({pct:5.1f}%) | acurácia parcial: {acc:5.2f}%"
                    )
                else:
                    pct = idx / n_cases * 100.0
                    print(
                        f"  • {idx}/{n_cases} exemplos processados "
                        f"({pct:5.1f}%)"
                    )

            if sleep_between > 0:
                time.sleep(sleep_between)

    return stats, records


def print_report(stats, records, labeled: bool):
    """Mostra relatório no console, adaptado para modo rotulado ou texto livre."""
    print("\n==================== RESULTADO GERAL ====================")

    if labeled:
        total = sum(v["total"] for v in stats.values())
        total_correct = sum(v["correct"] for v in stats.values())
        total_wrong = sum(v["wrong"] for v in stats.values())
        acc = (total_correct / total * 100.0) if total else 0.0

        print(f"Total de exemplos testados : {total}")
        print(f"Total de acertos           : {total_correct}")
        print(f"Total de erros             : {total_wrong}")
        print(f"Acurácia global            : {acc:.2f}%")

        print("\n==================== POR INTENT (esperada) ==============")
        for intent, v in sorted(
            stats.items(),
            key=lambda kv: (kv[1]["correct"] / kv[1]["total"]
                            if kv[1]["total"] else 0.0)
        ):
            t = v["total"]
            intent_acc = (v["correct"] / t * 100.0) if t else 0.0
            print(
                f"- {str(intent):40s}  total={t:4d}  "
                f"ok={v['correct']:4d}  nok={v['wrong']:4d}  acc={intent_acc:6.2f}%"
            )

        if records:
            print("\n==================== ERROS (AMOSTRA) ====================")
            max_show = min(30, len(records))
            for i, e in enumerate(records[:max_show], 1):
                print(f"{i:02d}. [{e['lang']}] \"{e['text']}\"")
                print(f"      esperado : {e['expected']}")
                print(f"      predito  : {e['predicted']}  (conf={e['confidence']:.3f})")
            if len(records) > max_show:
                print(f"... (+{len(records) - max_show} erros adicionais não listados)")

    else:
        # Modo texto livre: stats está como distribuição por intent predita
        total = sum(v["total"] for v in stats.values())
        print(f"Total de exemplos testados : {total}")
        print("\n==================== DISTRIBUIÇÃO POR INTENT PREDITA ===")
        for intent, v in sorted(
            stats.items(),
            key=lambda kv: kv[1]["total"],
            reverse=True
        ):
            t = v["total"]
            pct = (t / total * 100.0) if total else 0.0
            print(f"- {str(intent):40s}  hits={t:4d}  ({pct:6.2f}%)")

        print("\n(Em modo texto livre não há acurácia, só distribuição de previsões.)")


def save_records_csv(records, path: str):
    """
    Salva registros em CSV pra você abrir no Excel/Sheets.

    Em modo rotulado: só erros.
    Em modo texto livre: todas as previsões.
    """
    import csv

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            "lang", "text", "expected_intent",
            "predicted_intent", "confidence", "raw_json"
        ])
        for e in records:
            writer.writerow([
                e.get("lang", ""),
                e.get("text", ""),
                "" if e.get("expected") is None else e.get("expected"),
                e.get("predicted"),
                f"{e.get('confidence', 0.0):.6f}",
                json.dumps(e.get("raw", {}), ensure_ascii=False),
            ])
    print(f"💾 Arquivo salvo em: {path}")


# ========================= MAIN ========================== #

def main():
    parser = argparse.ArgumentParser(
        description="Testa exemplos de um arquivo contra um modelo Rasa via HTTP"
    )
    parser.add_argument(
        "--input-file",
        dest="input_file",
        default="input.txt",
        help="Arquivo de entrada. Pode ser rotulado (#intent/#pt/#en) ou texto livre.",
    )
    parser.add_argument(
        "--rasa-url",
        dest="rasa_url",
        default="http://localhost:5005/model/parse",
        help="URL do endpoint /model/parse do Rasa (default: http://localhost:5005/model/parse)",
    )
    parser.add_argument(
        "--max",
        dest="max_both",
        type=int,
        default=None,
        help="Limite global de exemplos por idioma (pt/en) por bloco (#max). Só afeta modo rotulado.",
    )
    parser.add_argument(
        "--max-pt",
        dest="max_pt",
        type=int,
        default=None,
        help="Limite global só para exemplos em PT (#max_pt). Só afeta modo rotulado.",
    )
    parser.add_argument(
        "--max-en",
        dest="max_en",
        type=int,
        default=None,
        help="Limite global só para exemplos em EN (#max_en). Só afeta modo rotulado.",
    )
    parser.add_argument(
        "--sleep",
        dest="sleep",
        type=float,
        default=0.0,
        help="Pausa (segundos) entre requisições ao Rasa (default: 0.0).",
    )
    parser.add_argument(
        "--max-tests",
        dest="max_tests",
        type=int,
        default=None,
        help="Limite máximo de exemplos a serem testados (debug).",
    )
    parser.add_argument(
        "--errors-file",
        dest="errors_file",
        default=None,
        help="Se informado, salva CSV com erros (modo rotulado) ou todas as previsões (modo texto livre).",
    )
    parser.add_argument(
        "--progress-every",
        dest="progress_every",
        type=int,
        default=25,
        help="A cada quantos exemplos mostrar andamento (default: 25).",
    )
    parser.add_argument(
        "--workers",
        dest="workers",
        type=int,
        default=4,
        help="Número de threads (workers) para chamadas paralelas ao Rasa (default: 4).",
    )

    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"❌ Arquivo de entrada não encontrado: {args.input_file}")
        sys.exit(1)

    # 1) Tenta ler como arquivo ROTULADO (input.txt-style)
    cases = parse_input_examples(
        args.input_file,
        global_max_pt=args.max_pt,
        global_max_en=args.max_en,
        global_max=args.max_both,
    )

    labeled = True
    if not cases:
        # 2) Se não achou #intent, cai para modo TEXTO LIVRE
        print("⚠️ Nenhum bloco com #intent encontrado. "
              "Interpretando arquivo como perguntas soltas (sem rótulo)...")
        cases = parse_unlabeled_file(args.input_file)
        labeled = False

    if not cases:
        print("⚠️ Nenhuma linha utilizável encontrada no arquivo. Verifique o conteúdo.")
        sys.exit(1)

    print(f"✅ {len(cases)} exemplos carregados de {args.input_file}")

    # 2) Roda testes contra o Rasa
    stats, records = run_tests(
        cases,
        rasa_url=args.rasa_url,
        sleep_between=args.sleep,
        max_tests=args.max_tests,
        progress_every=args.progress_every,
        workers=args.workers,
        labeled=labeled,
    )

    # 3) Mostra relatório
    print_report(stats, records, labeled=labeled)

    # 4) Salva CSV, se pedido
    if args.errors_file and records:
        save_records_csv(records, args.errors_file)


if __name__ == "__main__":
    main()
