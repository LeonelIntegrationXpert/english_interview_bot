#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Testa todos os exemplos do input.txt contra um modelo Rasa rodando via HTTP
e gera um relatório de acertos/erros por intent.

Uso básico:
    python test_intents_from_input.py

Com parâmetros:
    python test_intents_from_input.py \
        --input-file input.txt \
        --rasa-url http://localhost:5005/model/parse \
        --max 50 \
        --errors-file errors_report.csv \
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
    Lê o input.txt e devolve uma lista de casos de teste:

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
            print(f"⚠️  Bloco {idx} ignorado (sem #intent:). Prévia: {block[:120].replace(os.linesep, ' ')}")
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
        print(f"ℹ️  {ignored_blocks} bloco(s) ignorados por falta de #intent:.")

    return test_cases


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
              workers: int = 4):
    """
    Executa os testes contra o Rasa e devolve:
      - stats_por_intent
      - lista_de_erros

    Com suporte a multithreading (workers) e progresso em tempo real.
    """
    total_cases = len(test_cases)
    if max_tests is not None:
        test_cases = test_cases[:max_tests]

    n_cases = len(test_cases)
    print(f"🔎 Rodando testes em {n_cases} exemplos (de {total_cases} disponíveis)...")

    stats = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0})
    errors = []

    # Se workers <= 1, volta pro modo sequencial
    if workers <= 1:
        for idx, case in enumerate(test_cases, 1):
            expected = case["intent"]
            text = case["text"]
            lang = case["lang"]

            pred, conf, raw = call_rasa_parse(rasa_url, text)
            ok = (pred == expected)

            stats[expected]["total"] += 1
            if ok:
                stats[expected]["correct"] += 1
            else:
                stats[expected]["wrong"] += 1
                errors.append({
                    "lang": lang,
                    "text": text,
                    "expected": expected,
                    "predicted": pred,
                    "confidence": conf,
                    "raw": raw,
                })

            if progress_every > 0 and (idx % progress_every == 0 or idx == n_cases):
                current_total = sum(v["total"] for v in stats.values())
                current_correct = sum(v["correct"] for v in stats.values())
                acc = (current_correct / current_total * 100.0) if current_total else 0.0
                pct = idx / n_cases * 100.0
                print(
                    f"  • {idx}/{n_cases} exemplos processados "
                    f"({pct:5.1f}%) | acurácia parcial: {acc:5.2f}%"
                )

            if sleep_between > 0:
                time.sleep(sleep_between)

        return stats, errors

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
                    "expected": "UNKNOWN",
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

            ok = (pred == expected)

            stats[expected]["total"] += 1
            if ok:
                stats[expected]["correct"] += 1
            else:
                stats[expected]["wrong"] += 1
                errors.append({
                    "lang": lang,
                    "text": text,
                    "expected": expected,
                    "predicted": pred,
                    "confidence": conf,
                    "raw": raw,
                })

            if progress_every > 0 and (idx % progress_every == 0 or idx == n_cases):
                current_total = sum(v["total"] for v in stats.values())
                current_correct = sum(v["correct"] for v in stats.values())
                acc = (current_correct / current_total * 100.0) if current_total else 0.0
                pct = idx / n_cases * 100.0
                print(
                    f"  • {idx}/{n_cases} exemplos processados "
                    f"({pct:5.1f}%) | acurácia parcial: {acc:5.2f}%"
                )

            if sleep_between > 0:
                time.sleep(sleep_between)

    return stats, errors


def print_report(stats, errors):
    """Mostra relatório no console."""
    total = sum(v["total"] for v in stats.values())
    total_correct = sum(v["correct"] for v in stats.values())
    total_wrong = sum(v["wrong"] for v in stats.values())

    acc = (total_correct / total * 100.0) if total else 0.0

    print("\n==================== RESULTADO GERAL ====================")
    print(f"Total de exemplos testados : {total}")
    print(f"Total de acertos           : {total_correct}")
    print(f"Total de erros             : {total_wrong}")
    print(f"Acurácia global            : {acc:.2f}%")

    print("\n==================== POR INTENT =========================")
    # ordena intents pelas que mais erraram (acurácia crescente)
    for intent, v in sorted(stats.items(),
                            key=lambda kv: (kv[1]["correct"] / kv[1]["total"]
                                            if kv[1]["total"] else 0.0)):
        t = v["total"]
        if t == 0:
            intent_acc = 0.0
        else:
            intent_acc = v["correct"] / t * 100.0
        print(f"- {intent:40s}  total={t:4d}  "
              f"ok={v['correct']:4d}  nok={v['wrong']:4d}  acc={intent_acc:6.2f}%")

    if errors:
        print("\n==================== ERROS (AMOSTRA) ====================")
        max_show = min(30, len(errors))
        for i, e in enumerate(errors[:max_show], 1):
            print(f"{i:02d}. [{e['lang']}] \"{e['text']}\"")
            print(f"      esperado : {e['expected']}")
            print(f"      predito  : {e['predicted']}  (conf={e['confidence']:.3f})")
        if len(errors) > max_show:
            print(f"... (+{len(errors) - max_show} erros adicionais não listados)")


def save_errors_csv(errors, path: str):
    """Salva os erros em CSV pra você abrir no Excel/Sheets."""
    import csv

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(["lang", "text", "expected_intent",
                         "predicted_intent", "confidence", "raw_json"])
        for e in errors:
            writer.writerow([
                e["lang"],
                e["text"],
                e["expected"],
                e["predicted"],
                f"{e['confidence']:.6f}",
                json.dumps(e["raw"], ensure_ascii=False),
            ])
    print(f"💾 Arquivo de erros salvo em: {path}")


# ========================= MAIN ========================== #

def main():
    parser = argparse.ArgumentParser(
        description="Testa exemplos do input.txt contra um modelo Rasa via HTTP"
    )
    parser.add_argument(
        "--input-file",
        dest="input_file",
        default="input.txt",
        help="Arquivo de entrada no formato com #intent/#pt/#en (default: input.txt)",
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
        help="Limite global de exemplos por idioma (pt/en) por bloco (#max).",
    )
    parser.add_argument(
        "--max-pt",
        dest="max_pt",
        type=int,
        default=None,
        help="Limite global só para exemplos em PT (#max_pt).",
    )
    parser.add_argument(
        "--max-en",
        dest="max_en",
        type=int,
        default=None,
        help="Limite global só para exemplos em EN (#max_en).",
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
        help="Se informado, salva erros em CSV neste caminho.",
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

    # 1) Monta dataset a partir do input.txt
    cases = parse_input_examples(
        args.input_file,
        global_max_pt=args.max_pt,
        global_max_en=args.max_en,
        global_max=args.max_both,
    )

    if not cases:
        print("⚠️ Nenhum exemplo encontrado no input. Verifique o formato.")
        sys.exit(1)

    print(f"✅ {len(cases)} exemplos carregados de {args.input_file}")

    # 2) Roda testes contra o Rasa
    stats, errors = run_tests(
        cases,
        rasa_url=args.rasa_url,
        sleep_between=args.sleep,
        max_tests=args.max_tests,
        progress_every=args.progress_every,
        workers=args.workers,
    )

    # 3) Mostra relatório
    print_report(stats, errors)

    # 4) Salva CSV de erros, se pedido
    if args.errors_file and errors:
        save_errors_csv(errors, args.errors_file)


if __name__ == "__main__":
    main()
