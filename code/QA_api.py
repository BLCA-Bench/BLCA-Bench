import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

def extract_answer_letter(answer_text):
    """Extract a valid A-E answer from a reference answer."""
    if not isinstance(answer_text, str):
        return None
    match = re.match(r"^\s*([A-E])(?=$|[.、,，:：\s)])", answer_text.upper())
    return match.group(1) if match else None

def extract_option_letter(option_text):
    """Allow a missing separator before a Chinese option body, without
    loosening the reference-answer or generated-answer parsers.
    """
    label = extract_answer_letter(option_text)
    if label is not None:
        return label
    if isinstance(option_text, str):
        match = re.match(r"^\s*([A-E])(?=[\u3400-\u9fff])", option_text.upper())
        if match:
            return match.group(1)
    return None


def format_question_for_llm(row):
    """构建发送给大模型的问题。"""
    options_text = '\n'.join(row['options'])
    prompt = f"""你是一位医学专家，请回答以下医学单选题。

### 题目
{row['question']}

### 选项
{options_text}

### 要求
1. 请直接给出最可能的正确答案
2. 你的回答格式必须为: "我选择的答案是: X"，其中X是选项字母(A、B、C、D或E)
3. 不要解释原因，直接给出答案

请直接给出你的答案。
"""
    return prompt

SYSTEM_MESSAGE = "你是一位经验丰富的医学专家，擅长解答医学考试题目。请直接给出最可能的正确答案。"

def parse_llm_answer(answer_text):
    """Extract A-E while prioritizing the exact requested response format."""
    if not isinstance(answer_text, str) or not answer_text.strip():
        return None
    text = answer_text.strip().upper()
    patterns = (
        r"(?:我选择的答案是|答案是|ANSWER\s*(?:IS)?)[\s:：]*[（(]?([A-E])[）)]?",
        r"^[\s（(]*([A-E])[）).。、,，:：\s]*$",
        r"^[\s（(]*([A-E])[）).。、,，:：\s]",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None

EN_SYSTEM = 'You are an experienced medical expert, skilled at answering medical examination questions. Please directly provide the most likely correct answer.'

EN_TEMPLATE = '''You are a medical expert. Please answer the following medical single-choice question.

### Question
{question}

### Options
{options}

### Requirements
1. Directly provide the most likely correct answer.
2. Your response must be: "My answer is: X", where X is A, B, C, D, or E.
3. Do not explain your reasoning; provide the answer directly.

Please give your answer directly.
'''

def read_jsonl(path):
    with path.open(encoding='utf-8-sig') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_inputs(dataset, model):
    if dataset == 'benchmark':
        questions = read_jsonl(ROOT / 'data/all_questions.jsonl')
        outcomes = ROOT / 'data/model_item_outcomes.csv'
        prompts = None
    else:
        folder = ROOT / 'data/physician_questionnaire'
        questions = read_jsonl(folder / 'physician50_questions.jsonl')
        outcomes = folder / 'physician50_model_outcomes.csv'
        prompts = {row['id']: row['messages'] for row in read_jsonl(folder / 'physician50_prompt_messages.jsonl')}
    with outcomes.open(encoding='utf-8-sig', newline='') as handle:
        settings = {(row['pair_id'], row['language']): row for row in csv.DictReader(handle) if row['model'] == model}
    items = []
    for question in questions:
        language = question['language']
        answer = extract_answer_letter(question['answer'])
        labels = [extract_option_letter(option) for option in question['options']]
        if not question['question'].strip() or not 2 <= len(labels) <= 5 or None in labels or len(set(labels)) != len(labels) or answer not in labels:
            raise ValueError('Invalid question, options or reference answer: ' + question['pair_id'])
        if prompts is not None:
            messages = prompts[question['id']]
        elif language == 'zh':
            messages = [{'role': 'system', 'content': SYSTEM_MESSAGE},
                        {'role': 'user', 'content': format_question_for_llm(question)}]
        elif language == 'en':
            messages = [{'role': 'system', 'content': EN_SYSTEM},
                        {'role': 'user', 'content': EN_TEMPLATE.format(question=question['question'], options='\n'.join(question['options']))}]
        else:
            raise ValueError('Unknown language: ' + str(language))
        saved = settings.get((question['pair_id'], language))
        items.append({'question': question, 'messages': messages, 'reference_answer': answer,
                      'parameters': {'temperature': float(saved['temperature']) if saved else 0.1,
                                     'max_tokens': int(saved['max_tokens']) if saved else 100,
                                     'reasoning_effort': saved.get('reasoning_effort') or 'none' if saved else 'none'}})
    keys = [(item['question']['pair_id'], item['question']['language']) for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate pair_id and language keys')
    return items


def export_outputs(folder, rows, total):
    fields = ['model', 'pair_id', 'language', 'reference_answer', 'parsed_answer',
              'is_correct', 'status', 'temperature', 'max_tokens']
    with (folder / 'model_item_outcomes.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values = {field: row[field] for field in fields}
            values['is_correct'] = 'true' if row['is_correct'] else 'false'
            writer.writerow(values)
    with (folder / 'responses.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    correct = sum(row['is_correct'] for row in rows)
    save_json(folder / 'summary.json', {'assigned': total, 'recorded': len(rows),
              'complete': len(rows) == total, 'correct': correct,
              'accuracy_over_all_assigned': correct / total if total else None,
              'status_counts': dict(Counter(row['status'] for row in rows))})


def evaluate(item, model, endpoint, key, attempts, timeout, record_attempt, used_attempts=0):
    payload = {'model': model, 'messages': item['messages'], **item['parameters']}
    for attempt in range(used_attempts + 1, attempts + 1):
        record_attempt(attempt)
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        status, content, parsed = 'api_failed', None, None
        response, error, http_status = None, None, None
        transient, delay = False, min(2 ** attempt, 30)
        try:
            request = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as handle:
                http_status = handle.status
                response_text = handle.read().decode('utf-8').replace(key, '[REDACTED]')
            status = 'invalid'
            response = response_text
            response = json.loads(response_text)
            choices = response.get('choices') if isinstance(response, dict) else None
            content = choices[0].get('message', {}).get('content') if isinstance(choices, list) and choices else None
            parsed = parse_llm_answer(content)
            status = 'success' if parsed else 'invalid'
            if not parsed:
                error = 'Empty, refused or unparseable response; no automatic retry.'
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            transient = exc.code in RETRYABLE_STATUS_CODES
            status = 'api_failed' if transient else 'permanent_error'
            response = exc.read().decode('utf-8', errors='replace').replace(key, '[REDACTED]')
            error = 'HTTP ' + str(exc.code)
            try:
                delay = max(delay, min(float(exc.headers.get('Retry-After', 0)), 60))
            except (TypeError, ValueError):
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            transient, error = True, type(exc).__name__
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            status, error = 'invalid', type(exc).__name__
        row = {'model': model, 'pair_id': item['question']['pair_id'], 'language': item['question']['language'],
               'reference_answer': item['reference_answer'], 'parsed_answer': parsed,
               'is_correct': status == 'success' and parsed == item['reference_answer'], 'status': status,
               **item['parameters'], 'messages': item['messages'], 'request': payload,
               'response': response, 'raw_output': content, 'http_status': http_status, 'error': error,
               'attempt': attempt, 'started_at': started,
               'latency_seconds': round(time.monotonic() - clock, 6)}
        yield row
        if not transient or attempt == attempts:
            return
        time.sleep(delay)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='Exact API model ID')
    parser.add_argument('--dataset', choices=['benchmark', 'physician50'], default='benchmark')
    parser.add_argument('--language', choices=['zh', 'en', 'both'], default='both')
    parser.add_argument('--api-url', default=os.environ.get('BLCA_API_URL', ''), help='Full HTTPS chat/completions URL')
    parser.add_argument('--output', type=Path, help='Separate output folder; archived inputs are never overwritten')
    parser.add_argument('--max-tokens', type=int, help='Override retained per-item token caps; new model default is 100')
    parser.add_argument('--max-attempts', type=int, default=4, help='Maximum attempts per item for transient failures')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--run', action='store_true', help='Send live API requests; otherwise only print the plan')
    args = parser.parse_args()
    if not args.model.strip() or any(c.isspace() for c in args.model) or args.model.startswith('sk-'):
        parser.error('Use an exact model ID without whitespace.')
    if not 1 <= args.max_attempts <= 6 or not 0 < args.timeout <= 180 or (args.max_tokens is not None and not 1 <= args.max_tokens <= 4096):
        parser.error('Invalid attempt, timeout or token limit.')
    items = load_inputs(args.dataset, args.model)
    items = [item for item in items if args.language == 'both' or item['question']['language'] == args.language]
    if not items:
        parser.error('No questions match the language selection.')
    for item in items:
        if args.max_tokens is not None:
            item['parameters']['max_tokens'] = args.max_tokens
    safe_model = re.sub(r'[^\w.-]', '_', args.model)
    folder = (args.output or ROOT / 'output/evaluation' / safe_model / args.dataset / args.language).resolve()
    # Restrict writes to disposable evaluation output locations.
    for protected in ['data', 'analysis', 'code', 'docs']:
        if folder == (ROOT / protected).resolve() or (ROOT / protected).resolve() in folder.parents:
            parser.error('Output must be outside the packaged data, analysis, code and docs folders.')
    print(json.dumps({'model': args.model, 'dataset': args.dataset, 'items': len(items),
                      'language': args.language, 'output': str(folder), 'live_requests': args.run}, ensure_ascii=False))
    if not args.run:
        print('Plan only: no API request and no output files written. Add --run to evaluate.')
        return
    url = urllib.parse.urlsplit(args.api_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error('Set BLCA_API_URL or --api-url to the full HTTPS endpoint without credentials or query parameters.')
    key = os.environ.get('BLCA_API_KEY', '')
    if not key:
        parser.error('Set BLCA_API_KEY in the environment.')
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / 'checkpoint.json'
    manifest = {'model': args.model, 'dataset': args.dataset, 'endpoint': args.api_url,
                'language': args.language, 'max_attempts': args.max_attempts, 'timeout': args.timeout, 'items': items}
    state = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {'manifest': manifest, 'results': {}, 'inflight': None, 'attempt_counts': {}}
    if state['manifest'] != manifest:
        raise ValueError('Input, prompt or settings changed; use a separate output folder.')
    if state['inflight']:
        raise ValueError('The last request has an uncertain outcome. Inspect checkpoint/attempts before starting a separate output folder.')
    save_json(checkpoint, state)
    for ordinal, item in enumerate(items):
        item_key = str(ordinal)
        if item_key in state['results']:
            continue
        used_attempts = state['attempt_counts'].get(item_key, 0)
        if used_attempts >= args.max_attempts:
            raise ValueError('Attempt limit reached without a final checkpoint. Inspect attempts.jsonl before proceeding.')
        def record_attempt(attempt):
            state['inflight'] = {'ordinal': ordinal, 'attempt': attempt}
            state['attempt_counts'][item_key] = attempt
            save_json(checkpoint, state)
        for row in evaluate(item, args.model, args.api_url, key, args.max_attempts, args.timeout, record_attempt, used_attempts):
            with (folder / 'attempts.jsonl').open('a', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            state['inflight'] = None
            save_json(checkpoint, state)
        returned_models = {saved['response'].get('model') for saved in state['results'].values()
                           if isinstance(saved.get('response'), dict) and saved['response'].get('model')}
        returned_model = row['response'].get('model') if isinstance(row.get('response'), dict) else None
        if returned_model and returned_models and returned_model not in returned_models:
            row.update(status='model_changed', is_correct=False, error='Returned model ID changed; evaluation stopped.')
        state['results'][item_key] = row
        save_json(checkpoint, state)
        rows = [state['results'][str(i)] for i in range(len(items)) if str(i) in state['results']]
        export_outputs(folder, rows, len(items))
        print(f'{ordinal + 1}/{len(items)} {row["pair_id"]} {row["language"]}: {row["status"]}')
        if row['status'] in ('api_failed', 'permanent_error', 'model_changed'):
            raise SystemExit('Service failure recorded; evaluation stopped. Inspect the response before continuing.')
    rows = [state['results'][str(i)] for i in range(len(items)) if str(i) in state['results']]
    export_outputs(folder, rows, len(items))
    print('Evaluation complete. Outputs saved to ' + str(folder))


if __name__ == '__main__':
    main()
