# Error Detector

Программа для поиска ошибок в лог-файлах среди большого объёма обычных записей.

## Установка

Нужен только Python 3.10+ (стандартная библиотека).

## Запуск

```bash
# Поиск ошибок в файле
python error_detector.py путь/к/файлу.log

# С контекстом: 2 строки до и после каждой ошибки
python error_detector.py app.log -c 2

# Сохранить результат в файл
python error_detector.py app.log -o errors.txt

# Игнорировать регистр (error, ERROR, Error)
python error_detector.py app.log -i

# Свои паттерны из файла
python error_detector.py app.log --patterns my_patterns.txt
```

## Что ищет по умолчанию

- `ERROR`, `CRITICAL`, `FATAL`, `FAILED`
- `Exception`, `Traceback`
- `[ERROR]`, `[ERR]`
- Коды выхода (exit code 1, 2, …)
- HTTP-ошибки (4xx, 5xx)
- Строки с "Connection refused", "Timeout", "Out of memory"

## Свой файл паттернов

В `--patterns` передаёте текстовый файл. Каждая строка — один regex. Строки, начинающиеся с `#`, пропускаются.

Пример `my_patterns.txt`:

```
# мои метки
\[MYAPP-ERR\]
panic:
failed to connect
```

Формат с подписью: `Описание: регулярное выражение` (например: `OOM: Out of memory`).
