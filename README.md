# BLCA-Bench

BLCA-Bench is a Chinese–English question-answering dataset for evaluating large language models across the six whole-course management domains of bladder cancer: **Epidemiology, Etiology, Pathology, Diagnostics, Therapeutics, and Prophylaxis**. It contains **1,192 question pairs**, represented by **2,384 language-specific records**, drawn from medical examination question banks, clinical guidelines, an epidemiology report, and clinical cases.

## Repository structure

```text
BLCA-Bench/
├── README.md
├── requirements.txt
├── submit_test_samples.jsonl            
├── data/
│   ├── all_questions.jsonl              
│   ├── physician_questionnaire_50_questions.jsonl
│   └── by_source/
│       ├── medical_examination_question_banks.jsonl
│       ├── clinical_cases.jsonl
│       └── guidelines_and_epidemiology_report.jsonl
├── source/
│   ├── guidelines/
│   │   ├── AUA/            
│   │   ├── EAU/                  
│   │   └── NCCN/                
│   └── epidemiological_report/
│       └── Cancer statistics, 2025.pdf
└── code/
    ├── QA_api.py                       # Model evaluation
    └── question_construction/
        ├── process_single_guide.py
        ├── pdf_extract_and_generate.py
        ├── pdf_extract_fallback.py
        ├── multiprocess_ehr.py
        └── requirements_generation.txt
```

The root folder is shown as `BLCA-Bench/` for presentation. The PDF filenames inside the guideline folders are omitted above for readability.

## Repository contents

* [submit_test_samples.jsonl](submit_test_samples.jsonl) contains **100 Chinese–English question pairs (200 records)** selected from the complete dataset, covering all three sources and all six domains. It provides a quick introduction to the dataset’s content and format.
* [source/](source/) contains the PDF documents used for question construction: five guideline documents under `guidelines/AUA/`, `guidelines/EAU/` and `guidelines/NCCN/`, and *Cancer statistics, 2025* under `epidemiological_report/`.
* [code/question_construction/](code/question_construction/) contains the source-processing and question-construction scripts. [code/QA_api.py](code/QA_api.py) contains the model-evaluation script.
* [data/all_questions.jsonl](data/all_questions.jsonl) contains the complete BLCA-Bench question set.
* [data/by_source/](data/by_source/) contains the same records grouped by source: medical examination question banks (**311 pairs**), clinical cases (**404 pairs**), and guidelines and the epidemiology report (**477 pairs**). These files partition the complete dataset; they are not additional questions.
* [data/physician_questionnaire_50_questions.jsonl](data/physician_questionnaire_50_questions.jsonl) contains the separate **50-question Chinese physician questionnaire**. Treat it separately from the main benchmark.

Although anonymized, the clinical cases used as source material for question generation are not publicly shared to protect patient privacy and comply with ethical requirements. The resulting clinical-case questions are included in the dataset.

## Question records

The question files use UTF-8 JSON Lines (JSONL), with one JSON object per line. In the main benchmark, the Chinese and English versions share a `pair_id` and are distinguished by `language` (`zh` or `en`). Each record contains:

* `pair_id` and `language`: identifiers for the question pair and language version.
* `collection` and `source`: source grouping and provenance information.
* `category`: the original Chinese source category, with eight values in the retained dataset.
* `domain`: one of the six English management-domain labels listed above, used in both language versions.
* `question`: the question stem.
* `options`: two to five answer options in their supplied order.
* `answer`: the reference option letter.
* `explanation`: the reference rationale.

The physician-questionnaire records use a separate schema that includes `id` and `background`; they do not contain the main benchmark’s `category`, `domain` or `explanation` fields.

## Load the sample

Run the following Python code from the repository root. Loading the data requires only the Python standard library.

```python
import json
from pathlib import Path

path = Path("submit_test_samples.jsonl")
with path.open(encoding="utf-8-sig") as handle:
    records = [json.loads(line) for line in handle if line.strip()]

print("Language-specific records:", len(records))  # 200
print("Question pairs:", len({r["pair_id"] for r in records}))  # 100
print(records[0])
```

To load the complete benchmark, replace the path with `data/all_questions.jsonl`. Keep Chinese and English versions linked by `pair_id` when comparing language performance. For model evaluation, present the question stem and options in their supplied order; keep reference answers and explanations separate from the model input.

## Question construction

Install the construction dependencies from the repository root:

```powershell
python -m pip install -r code/question_construction/requirements_generation.txt
```

Set the API configuration for a compatible chat-completions endpoint:

```powershell
$env:BLCA_API_KEY = "YOUR_API_KEY"
$env:BLCA_API_URL = "API_URL"
$env:BLCA_MODEL = "YOUR_MODEL_ID"
```

Use `process_single_guide.py` to supply a PDF directly from `source/`. For example, this command requests 10 draft questions from the epidemiology report:

```powershell
python code/question_construction/process_single_guide.py "source/epidemiological_report/Cancer statistics, 2025.pdf" "epidemiology_report" 10
```

The command makes API requests and saves generated JSON files in `output/` relative to the working directory.

`pdf_extract_and_generate.py` implements PDF extraction and question generation, while `pdf_extract_fallback.py` provides an alternative text-extraction utility. The batch PDF entry point and `--list` helper use `data/guides/`, so use the explicit PDF-path command above with this repository’s `source/` layout.

`multiprocess_ehr.py` processes locally supplied clinical-case DOCX files from `data/ehrs/`, relative to the working directory, and writes constructed questions and intermediate files to `output/` and `intermediate/`.

## Model evaluation

`code/QA_api.py` constructs language-specific prompts, submits questions to an API, extracts option letters, and scores them against the reference answers. Its available command-line options can be inspected without making API requests:

```powershell
python code/QA_api.py --help
```

Once its inputs are prepared, the script prints an evaluation plan by default; live requests require `--run`. Evaluation outputs include item-level scores, API responses, a checkpoint, and an accuracy summary, normally written under `output/evaluation/`.

