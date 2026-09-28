#!/usr/bin/env bash
# Завершать выполнение скрипта при любой ошибке
set -o errexit

# 1. Установка зависимостей
pip install -r requirements.txt

# 2. Сборка всей статики (CSS/JS) в одну папку
python manage.py collectstatic --no-input

# 3. Применение миграций к базе данных
python manage.py migrate