#!/usr/bin/env python3
"""
Поиск и анализ ошибок в лог-файлах.
Находит строки с ошибками и группирует их по уровням критичности.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path


# Паттерны для поиска ошибок (можно расширять)
DEFAULT_PATTERNS = [
    (r"\bERROR\b", "ERROR"),
    (r"\bCRITICAL\b", "CRITICAL"),
    (r"\bFATAL\b", "FATAL"),
    (r"\bFAILED\b", "FAILED"),
    (r"\bException\b", "Exception"),
    (r"\bTraceback\s*\(", "Traceback"),
    (r"^\s*Traceback", "Traceback"),
    (r"\bError\s*:", "Error:"),
    (r"\[ERROR\]", "[ERROR]"),
    (r"\[ERR\]", "[ERR]"),
    (r"exit code [1-9]\d*", "exit code"),
    (r"status[=:\s]+[45]\d{2}", "HTTP 4xx/5xx"),
    (r"Connection refused", "Connection refused"),
    (r"Timeout|Timed out", "Timeout"),
    (r"OutOfMemory|Out of memory", "Out of memory"),
]

LOG_LEVEL_ORDER = ["FATAL", "CRITICAL", "ERROR", "WARN", "INFO", "DEBUG", "UNKNOWN"]


def load_patterns(custom_patterns_file: Path | None) -> list[tuple[str, str]]:
    """Загружает паттерны: стандартные + опционально из файла."""
    patterns = list(DEFAULT_PATTERNS)
    if custom_patterns_file and custom_patterns_file.exists():
        for line in custom_patterns_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Формат: паттерн или "описание: паттерн"
            if ":" in line and not line.startswith("http"):
                desc, pat = line.split(":", 1)
                patterns.append((pat.strip(), desc.strip()))
            else:
                patterns.append((line, line[:40]))
    return patterns


def is_slow_operation(line: str, threshold_seconds: float = 2.0) -> bool:
    """
    Считает операцию «медленной», если явное время выполнения > threshold_seconds
    (понимает ms, s, sec, seconds, m, min) или в тексте явно упомянуто «slow».
    """
    text = line.lower()
    if "slow" in text:
        return True

    # Ищем конструкции вида:
    #   took 1234 ms
    #   duration=2.5s
    #   elapsed 3 sec
    #   time: 1.2 seconds
    duration_patterns = [
        r"\b(took|duration|elapsed|time)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(ms|msec|millisecond|milliseconds|s|sec|secs|second|seconds|m|min|mins|minute|minutes)\b",
        r"\b(\d+(?:\.\d+)?)\s*(ms|msec|millisecond|milliseconds|s|sec|secs|second|seconds|m|min|mins|minute|minutes)\s*(?:to complete|elapsed|total|exec|execution|query)\b",
    ]

    for pattern in duration_patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        if len(m.groups()) == 3:
            _, value_str, unit = m.groups()
        else:
            value_str, unit = m.groups()
        try:
            value = float(value_str)
        except ValueError:
            continue

        unit = unit.lower()
        seconds: float
        if unit.startswith("ms") or "millisecond" in unit:
            seconds = value / 1000.0
        elif unit.startswith("m") and unit not in ("ms", "msec"):  # min / minutes
            seconds = value * 60.0
        else:  # секунды
            seconds = value

        if seconds > threshold_seconds:
            return True

    return False


def detect_log_level(line: str, pattern_desc: str) -> str:
    """Определяет уровень логирования для строки."""
    text = line.upper()
    # Явные уровни в строке
    if "FATAL" in text:
        return "FATAL"
    if "CRITICAL" in text:
        return "CRITICAL"
    if " ERROR " in f" {text} " or "[ERROR]" in text:
        return "ERROR"
    if "WARN" in text or "WARNING" in text:
        return "WARN"
    if " INFO " in f" {text} ":
        return "INFO"
    if "DEBUG" in text or "TRACE" in text:
        return "DEBUG"

    desc = pattern_desc.upper()
    if any(x in desc for x in ("FATAL", "CRITICAL")):
        return "CRITICAL"
    if any(x in desc for x in ("ERROR", "EXCEPTION", "TRACEBACK")):
        return "ERROR"
    if "TIMEOUT" in desc or "HTTP 4XX/5XX" in desc:
        return "WARN"

    return "UNKNOWN"


def normalize_message(line: str) -> str:
    """
    Убирает типичный префикс с датой/временем и оставляет «суть» сообщения,
    чтобы лучше группировать повторяющиеся ошибки.
    """
    # Простейший срез: убираем дату в формате "YYYY-MM-DD ..." или "YYYY/MM/DD ..."
    normalized = re.sub(
        r"^\s*\d{4}[-/]\d{2}[-/]\d{2}T?\S*\s+",
        "",
        line,
    )
    # Убираем лишние пробелы
    return " ".join(normalized.split())


def find_errors(
    log_path: Path,
    patterns: list[tuple[str, str]],
    context_lines: int = 0,
    ignore_case: bool = False,
) -> list[tuple[int, str, str]]:
    """
    Сканирует файл и возвращает список (номер_строки, описание_паттерна, строка).
    """
    results = []
    flags = re.IGNORECASE if ignore_case else 0
    compiled = [(re.compile(p, flags), desc) for p, desc in patterns]

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"Ошибка чтения файла: {e}", file=sys.stderr)
        return []

    for i, line in enumerate(lines, start=1):
        for regex, desc in compiled:
            if regex.search(line):
                results.append((i, desc, line))
                break

    if context_lines <= 0:
        return results

    # Добавляем контекст (соседние строки) к каждой найденной
    expanded = []
    for num, desc, line in results:
        expanded.append((num, desc, line))
        start = max(0, num - 1 - context_lines)
        end = min(len(lines), num + context_lines)
        for j in range(start, end):
            if j + 1 == num:
                continue
            expanded.append((j + 1, "  (контекст)", lines[j]))
    # Сортируем по номеру строки и убираем дубликаты порядка
    expanded.sort(key=lambda x: (x[0], x[1] == "  (контекст)"))
    return expanded


def build_summary(results: list[tuple[int, str, str]]) -> str:
    """Строит сводку: по уровням, повторениям, таймаутам и «медленным» операциям."""
    if not results:
        return ""

    # Берём только реальные ошибки, без контекста
    real = [(n, d, l) for n, d, l in results if d != "  (контекст)"]
    if not real:
        return ""

    level_counts: Counter[str] = Counter()
    message_counts: Counter[str] = Counter()
    timeout_lines: list[tuple[int, str]] = []
    slow_lines: list[tuple[int, str]] = []

    for num, desc, line in real:
        level = detect_log_level(line, desc)
        level_counts[level] += 1

        normalized = normalize_message(line)
        if normalized:
            message_counts[normalized] += 1

        if re.search(r"\b(timeout|timed out)\b", line, re.IGNORECASE):
            timeout_lines.append((num, line))

        if is_slow_operation(line):
            slow_lines.append((num, line))

    parts: list[str] = []

    # Сводка по уровням
    parts.append("\n=== Сводка по уровням ===")
    total = sum(level_counts.values())
    for level in LOG_LEVEL_ORDER:
        if level_counts[level]:
            parts.append(f"{level:8}: {level_counts[level]}")
    if level_counts and total:
        parts.append(f"ВСЕГО   : {total}")

    # Повторяющиеся сообщения
    repeated = [(msg, cnt) for msg, cnt in message_counts.items() if cnt > 1]
    if repeated:
        parts.append("\n=== Повторяющиеся ошибки (топ 10) ===")
        for msg, cnt in sorted(repeated, key=lambda x: x[1], reverse=True)[:10]:
            parts.append(f"x{cnt}: {msg}")

    # Таймауты
    if timeout_lines:
        parts.append("\n=== Таймауты / сетевые задержки ===")
        for num, line in timeout_lines[:20]:
            parts.append(f"строка {num}: {line}")

    # Медленные операции
    if slow_lines:
        parts.append("\n=== Потенциально медленные операции ===")
        for num, line in slow_lines[:20]:
            parts.append(f"строка {num}: {line}")

    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Поиск ошибок в лог-файле",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python error_detector.py app.log
  python error_detector.py app.log -c 2
  python error_detector.py app.log -o errors.txt
  python error_detector.py app.log --patterns my_patterns.txt
        """,
    )
    parser.add_argument(
        "logfile",
        type=Path,
        help="Путь к лог-файлу",
    )
    parser.add_argument(
        "-c", "--context",
        type=int,
        default=0,
        metavar="N",
        help="Показывать N строк контекста до и после каждой ошибки (по умолчанию 0)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Сохранить результат в файл",
    )
    parser.add_argument(
        "-i", "--ignore-case",
        action="store_true",
        help="Игнорировать регистр при поиске",
    )
    parser.add_argument(
        "--patterns",
        type=Path,
        default=None,
        metavar="FILE",
        help="Файл с дополнительными regex-паттернами (по одному на строку)",
    )
    args = parser.parse_args()

    if not args.logfile.exists():
        print(f"Файл не найден: {args.logfile}", file=sys.stderr)
        sys.exit(1)
    if not args.logfile.is_file():
        print(f"Указан не файл: {args.logfile}", file=sys.stderr)
        sys.exit(1)

    patterns = load_patterns(args.patterns)
    results = find_errors(
        args.logfile,
        patterns,
        context_lines=args.context,
        ignore_case=args.ignore_case,
    )

    out_lines: list[str] = []
    for num, desc, line in results:
        out_lines.append(f"{num}: [{desc}] {line}")

    text = "\n".join(out_lines)
    summary = build_summary(results)
    if summary:
        if text:
            text_with_summary = f"{text}\n{summary}"
        else:
            text_with_summary = summary.lstrip()
    else:
        text_with_summary = text

    if args.output:
        args.output.write_text(text_with_summary, encoding="utf-8")
        print(
            f"Найдено записей: {len(results)}. "
            f"Результат (включая сводку) сохранён в {args.output}"
        )
    else:
        if not text_with_summary.strip():
            print("Ошибок не найдено.")
        else:
            print(f"Найдено записей: {len(results)}\n")
            print(text_with_summary)


if __name__ == "__main__":
    main()
