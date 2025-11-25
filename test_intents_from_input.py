#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Testa exemplos contra um modelo Rasa rodando via HTTP
e gera um relatório de acertos/erros (modo rotulado)
ou apenas de previsões (modo texto livre).

- Cria pasta automática por teste: reports/test_XXX_YYYYMMDD_HHMMSS
- Salva CSV dentro da pasta
- Gera report.html dentro da pasta, lendo o CSV via JS

Exemplo de uso:

python test_intents_from_input.py \
  --input-file input.txt \
  --rasa-url http://localhost:5005/model/parse \
  --workers 15 \
  --progress-every 100
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


# =============== HELPERS DE PASTA / RUN ================== #

def create_test_run_dir(base_dir: str = "reports") -> str:
    """
    Cria uma pasta única para este teste.

    Ex: reports/test_001_20251125_230501
    """
    os.makedirs(base_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    existing = [
        d for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d)) and d.startswith("test_")
    ]
    # índice sequencial simples (não precisa ser perfeito)
    idx = len(existing) + 1
    run_name = f"test_{idx:03d}_{timestamp}"
    run_dir = os.path.join(base_dir, run_name)
    os.makedirs(run_dir, exist_ok=True)
    print(f"📁 Pasta deste teste: {run_dir}")
    return run_dir


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
    records = []  # aqui vão os ERROS (labeled) ou TODAS as previsões (unlabeled)

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
    print(f"💾 CSV salvo em: {path}")


def save_html_report(stats, labeled: bool, run_dir: str, csv_filename: str | None):
    """
    Gera um report.html dentro da pasta do teste.

    - Usa STATS embutido em JS para mostrar totais e acurácia por intent.
    - Lê o CSV (erros/predictions) via fetch(CSV_FILENAME) para listar exemplos.
    """
    html_path = os.path.join(run_dir, "report.html")
    stats_json = json.dumps(stats, ensure_ascii=False)
    csv_js = json.dumps(csv_filename)  # pode ser None
    labeled_js = "true" if labeled else "false"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <title>Rasa NLU Test Report</title>
  <style>
    :root {{
      --bg: #0b1020;
      --bg-card: #151a2c;
      --bg-soft: #1d2338;
      --accent: #4f46e5;
      --accent-soft: rgba(79,70,229,0.15);
      --accent-danger: #dc2626;
      --accent-warn: #ea580c;
      --text-main: #e5e7eb;
      --text-soft: #9ca3af;
      --border-subtle: #1f2937;
      --radius-lg: 14px;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      padding: 24px;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top left, #1f2937 0, #020617 45%, #000 100%);
      color: var(--text-main);
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    h1 {{
      font-size: 26px;
      margin: 0 0 4px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    h1 span.badge {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid rgba(79,70,229,0.4);
    }}
    .subtitle {{
      font-size: 13px;
      color: var(--text-soft);
      margin-bottom: 20px;
    }}
    .grid-summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .card {{
      background: rgba(15,23,42,0.9);
      border-radius: var(--radius-lg);
      padding: 12px 14px;
      border: 1px solid rgba(148,163,184,0.15);
      box-shadow: 0 14px 40px rgba(15,23,42,0.7);
      backdrop-filter: blur(10px);
    }}
    .card h2 {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-soft);
      margin: 0 0 6px;
    }}
    .card .value {{
      font-size: 22px;
      font-weight: 600;
      margin-bottom: 2px;
    }}
    .card .helper {{
      font-size: 11px;
      color: var(--text-soft);
    }}
    .value.good {{
      color: #22c55e;
    }}
    .value.warn {{
      color: var(--accent-warn);
    }}
    .value.bad {{
      color: var(--accent-danger);
    }}
    .section-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin: 20px 0 8px;
    }}
    .section-title h3 {{
      margin: 0;
      font-size: 16px;
    }}
    .chip {{
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--bg-soft);
      color: var(--text-soft);
      border: 1px solid var(--border-subtle);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    thead {{
      background: rgba(15,23,42,0.9);
    }}
    th, td {{
      padding: 8px 10px;
      text-align: left;
      border-bottom: 1px solid rgba(31,41,55,0.7);
      vertical-align: top;
    }}
    th {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-soft);
    }}
    tr:nth-child(even) td {{
      background: rgba(15,23,42,0.5);
    }}
    tr:hover td {{
      background: rgba(30,64,175,0.2);
    }}
    .table-wrapper {{
      border-radius: var(--radius-lg);
      border: 1px solid rgba(148,163,184,0.2);
      overflow: hidden;
      background: rgba(15,23,42,0.8);
      box-shadow: 0 18px 40px rgba(15,23,42,0.9);
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      border: 1px solid rgba(148,163,184,0.4);
      background: rgba(15,23,42,0.9);
    }}
    .pill.ok {{
      border-color: rgba(34,197,94,0.6);
      color: #bbf7d0;
      background: rgba(22,163,74,0.2);
    }}
    .pill.bad {{
      border-color: rgba(239,68,68,0.6);
      color: #fecaca;
      background: rgba(239,68,68,0.15);
    }}
    .pill.mid {{
      border-color: rgba(245,158,11,0.6);
      color: #fef3c7;
      background: rgba(245,158,11,0.15);
    }}
    .tag-intent {{
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 11px;
      padding: 3px 6px;
      border-radius: 999px;
      background: rgba(15,23,42,0.9);
      border: 1px solid rgba(148,163,184,0.4);
      color: #e5e7eb;
      white-space: nowrap;
    }}
    .layout-two-cols {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.8fr);
      gap: 16px;
      align-items: flex-start;
    }}
    .filters {{
      display: flex;
      gap: 8px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }}
    .filters input {{
      flex: 1;
      min-width: 180px;
      padding: 6px 8px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.4);
      background: rgba(15,23,42,0.9);
      color: var(--text-main);
      font-size: 12px;
    }}
    .filters select {{
      padding: 6px 8px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.4);
      background: rgba(15,23,42,0.9);
      color: var(--text-main);
      font-size: 12px;
    }}
    .muted {{
      color: var(--text-soft);
      font-size: 12px;
    }}
    .intent-row.active td {{
      background: rgba(79,70,229,0.2) !important;
    }}
    .pill-dot {{
      width: 6px;
      height: 6px;
      border-radius: 999px;
      margin-right: 6px;
      background: #22c55e;
    }}
    .pill-dot.bad {{
      background: #ef4444;
    }}
    .pill-dot.mid {{
      background: #eab308;
    }}
    .alert-soft {{
      margin-top: 6px;
      font-size: 11px;
      color: var(--text-soft);
    }}
    .badge-soft {{
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 999px;
      background: rgba(15,23,42,0.9);
      border: 1px solid rgba(148,163,184,0.3);
      color: var(--text-soft);
    }}
    @media (max-width: 960px) {{
      .grid-summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .layout-two-cols {{
        grid-template-columns: minmax(0, 1fr);
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>
        Rasa NLU Test Report
        <span class="badge">{'Rotulado' if labeled else 'Texto livre'}</span>
      </h1>
      <div class="subtitle">
        Visão consolidada da qualidade das intents, com breakdown por intent e exemplos de erros.
      </div>
    </header>

    <section class="grid-summary" id="summary-cards">
      <!-- Populado via JS -->
    </section>

    <section class="layout-two-cols">
      <div>
        <div class="section-title">
          <h3>Intents &amp; Acurácia</h3>
          <span class="chip" id="intents-count-chip"></span>
        </div>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Intent (esperada)</th>
                <th>Total</th>
                <th>OK</th>
                <th>Erros</th>
                <th>Acurácia</th>
              </tr>
            </thead>
            <tbody id="intent-rows">
              <!-- Populado via JS -->
            </tbody>
          </table>
        </div>
        <div class="alert-soft">
          Clique em uma intent para filtrar a tabela de exemplos de erro ao lado.
        </div>
      </div>

      <div id="errors-section">
        <div class="section-title">
          <h3>Exemplos de Erro</h3>
          <span class="chip" id="errors-count-chip"></span>
        </div>
        <div class="filters">
          <input
            type="text"
            id="search-input"
            placeholder="Filtrar por texto, intent esperada ou predita..."
          />
          <select id="filter-intent">
            <option value="">Todas as intents</option>
          </select>
        </div>
        <div class="table-wrapper" id="errors-table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Lang</th>
                <th>Texto</th>
                <th>Esperada</th>
                <th>Predita</th>
                <th>Conf.</th>
              </tr>
            </thead>
            <tbody id="error-rows">
              <!-- Populado via JS -->
            </tbody>
          </table>
        </div>
        <div class="alert-soft" id="errors-hint">
          São mostrados apenas exemplos com mismatch (esperada ≠ predita) em modo rotulado.
        </div>
      </div>
    </section>
  </div>

  <script>
    const LABELED = {labeled_js};
    const CSV_FILENAME = {csv_js};
    const STATS = {stats_json};

    function computeGlobalStats(stats) {{
      let total = 0;
      let correct = 0;
      let wrong = 0;
      let intentsCount = 0;

      for (const [intent, v] of Object.entries(stats)) {{
        if (intent === "__NO_INTENT__") continue;
        total += v.total || 0;
        correct += v.correct || 0;
        wrong += v.wrong || 0;
        intentsCount += 1;
      }}

      const acc = total > 0 ? (correct / total) * 100 : 0;
      return {{ total, correct, wrong, acc, intentsCount }};
    }}

    function createSummaryCards() {{
      const s = computeGlobalStats(STATS);
      const container = document.getElementById('summary-cards');
      container.innerHTML = '';

      function classifyAcc(acc) {{
        if (acc >= 95) return 'good';
        if (acc >= 85) return 'mid';
        return 'bad';
      }}

      const accClass = classifyAcc(s.acc);

      const cards = [
        {{
          title: 'Acurácia Global',
          value: s.acc.toFixed(2) + '%',
          valueClass: accClass,
          helper: 'Com base em ' + s.total + ' exemplos testados.'
        }},
        {{
          title: 'Total de Exemplos',
          value: s.total.toString(),
          valueClass: '',
          helper: 'Acertos: ' + s.correct + ' • Erros: ' + s.wrong
        }},
        {{
          title: 'Quantidade de Intents',
          value: s.intentsCount.toString(),
          valueClass: '',
          helper: 'Apenas intents esperadas (sem __NO_INTENT__).'
        }},
        {{
          title: 'Modo de Teste',
          value: LABELED ? 'Rotulado' : 'Texto livre',
          valueClass: '',
          helper: LABELED
            ? 'Comparando expected_intent vs predicted_intent.'
            : 'Somente distribuição de intents preditas.'
        }}
      ];

      for (const c of cards) {{
        const card = document.createElement('div');
        card.className = 'card';
        const valClass = c.valueClass ? 'value ' + c.valueClass : 'value';
        card.innerHTML = `
          <h2>${{c.title}}</h2>
          <div class="${{valClass}}">${{c.value}}</div>
          <div class="helper">${{c.helper}}</div>
        `;
        container.appendChild(card);
      }}
    }}

    function buildIntentTable() {{
      const tbody = document.getElementById('intent-rows');
      const selectFilter = document.getElementById('filter-intent');
      const chip = document.getElementById('intents-count-chip');
      tbody.innerHTML = '';
      selectFilter.innerHTML = '<option value="">Todas as intents</option>';

      const intents = [];
      for (const [intent, v] of Object.entries(STATS)) {{
        if (intent === "__NO_INTENT__") continue;
        const total = v.total || 0;
        const correct = v.correct || 0;
        const wrong = v.wrong || 0;
        const acc = total > 0 ? (correct / total) * 100 : 0;
        intents.push({{ intent, total, correct, wrong, acc }});
      }}

      // Ordena por acurácia crescente (pior primeiro)
      intents.sort((a, b) => a.acc - b.acc);

      const totalIntents = intents.length;
      chip.textContent = `${{totalIntents}} intents`;

      // Popular select de filtro
      for (const item of intents) {{
        const opt = document.createElement('option');
        opt.value = item.intent;
        opt.textContent = item.intent;
        selectFilter.appendChild(opt);
      }}

      for (const item of intents) {{
        const tr = document.createElement('tr');
        tr.dataset.intent = item.intent;

        let pillCls = 'pill mid';
        let pillDotCls = 'pill-dot mid';
        if (item.acc >= 95) {{
          pillCls = 'pill ok';
          pillDotCls = 'pill-dot';
        }} else if (item.acc < 85) {{
          pillCls = 'pill bad';
          pillDotCls = 'pill-dot bad';
        }}

        tr.innerHTML = `
          <td><span class="tag-intent">${{item.intent}}</span></td>
          <td>${{item.total}}</td>
          <td>${{item.correct}}</td>
          <td>${{item.wrong}}</td>
          <td>
            <span class="${{pillCls}}">
              <span class="${{pillDotCls}}"></span>
              ${{item.acc.toFixed(2)}}%
            </span>
          </td>
        `;
        tr.addEventListener('click', () => {{
          // marca linha ativa
          document.querySelectorAll('#intent-rows tr').forEach(r => r.classList.remove('active'));
          tr.classList.add('active');
          // aplica filtro na tabela de erros
          const filterSelect = document.getElementById('filter-intent');
          filterSelect.value = item.intent;
          applyErrorFilters();
        }});
        tbody.appendChild(tr);
      }}
    }}

    function parseCsv(text) {{
      const lines = text.trim().split(/\\r?\\n/);
      if (!lines.length) return {{ header: [], rows: [] }};
      const header = lines[0].split(';');
      const rows = [];
      for (let i = 1; i < lines.length; i++) {{
        const line = lines[i];
        if (!line.trim()) continue;
        const cols = line.split(';');
        if (cols.length < header.length) continue;
        const obj = {{}};
        header.forEach((h, idx) => {{
          obj[h] = cols[idx];
        }});
        rows.push(obj);
      }}
      return {{ header, rows }};
    }}

    let ERROR_ROWS = [];

    async function loadErrorsCsv() {{
      const wrapper = document.getElementById('errors-table-wrapper');
      const section = document.getElementById('errors-section');
      const chip = document.getElementById('errors-count-chip');

      if (!CSV_FILENAME) {{
        section.style.display = 'none';
        return;
      }}

      try {{
        const resp = await fetch(CSV_FILENAME);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const text = await resp.text();
        const parsed = parseCsv(text);
        ERROR_ROWS = parsed.rows || [];

        chip.textContent = `${{ERROR_ROWS.length}} exemplo(s)`;
        renderErrorTable(ERROR_ROWS);
      }} catch (e) {{
        console.error('Erro ao carregar CSV de erros:', e);
        wrapper.innerHTML = '<div class="muted" style="padding: 12px;">Não foi possível carregar o CSV de erros. Abra este relatório via servidor HTTP local (ex: <code>python -m http.server</code>) ou use o input file.</div>';
      }}
    }}

    function renderErrorTable(rows) {{
      const tbody = document.getElementById('error-rows');
      tbody.innerHTML = '';

      if (!rows.length) {{
        tbody.innerHTML = '<tr><td colspan="5" class="muted">Nenhum erro registrado para os filtros atuais.</td></tr>';
        return;
      }}

      for (const r of rows) {{
        const tr = document.createElement('tr');
        const conf = parseFloat(r.confidence || '0');
        let confClass = 'pill ok';
        let dotClass = 'pill-dot';
        if (conf < 0.7) {{
          confClass = 'pill bad';
          dotClass = 'pill-dot bad';
        }} else if (conf < 0.9) {{
          confClass = 'pill mid';
          dotClass = 'pill-dot mid';
        }}
        tr.innerHTML = `
          <td>${{r.lang || ''}}</td>
          <td>${{r.text || ''}}</td>
          <td><span class="tag-intent">${{r.expected_intent || ''}}</span></td>
          <td><span class="tag-intent">${{r.predicted_intent || ''}}</span></td>
          <td>
            <span class="${{confClass}}">
              <span class="${{dotClass}}"></span>
              ${{conf.toFixed(3)}}
            </span>
          </td>
        `;
        tbody.appendChild(tr);
      }}
    }}

    function applyErrorFilters() {{
      const text = (document.getElementById('search-input').value || '').toLowerCase();
      const intentFilter = document.getElementById('filter-intent').value || '';

      const filtered = ERROR_ROWS.filter(r => {{
        const haystack = [
          r.text || '',
          r.expected_intent || '',
          r.predicted_intent || ''
        ].join(' ').toLowerCase();

        const matchesText = !text || haystack.includes(text);
        const matchesIntent = !intentFilter || (r.expected_intent === intentFilter);
        return matchesText && matchesIntent;
      }});

      renderErrorTable(filtered);
      const chip = document.getElementById('errors-count-chip');
      chip.textContent = `${{filtered.length}} exemplo(s) filtrados`;
    }}

    document.addEventListener('DOMContentLoaded', () => {{
      createSummaryCards();
      buildIntentTable();
      loadErrorsCsv();

      const input = document.getElementById('search-input');
      const select = document.getElementById('filter-intent');

      if (input) input.addEventListener('input', applyErrorFilters);
      if (select) select.addEventListener('change', applyErrorFilters);
    }});
  </script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 Relatório HTML salvo em: {html_path}")


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
        help="Nome do CSV dentro da pasta de teste (default: errors_labeled.csv ou predictions_free.csv).",
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

    # 3) Cria pasta do teste
    run_dir = create_test_run_dir()

    # 4) Salva CSV de erros/previsões (se houver registros)
    csv_filename = None
    if records:
        if args.errors_file:
            csv_filename = os.path.basename(args.errors_file)
        else:
            csv_filename = "errors_labeled.csv" if labeled else "predictions_free.csv"
        csv_path = os.path.join(run_dir, csv_filename)
        save_records_csv(records, csv_path)
    else:
        print("ℹ️ Nenhum registro para CSV (sem erros em modo rotulado ou sem previsões em texto livre).")

    # 5) Mostra relatório em texto
    print_report(stats, records, labeled=labeled)

    # 6) Gera HTML bonito
    save_html_report(stats, labeled, run_dir, csv_filename)


if __name__ == "__main__":
    main()
