import psycopg2
import logging

# Конфигурация PostgreSQL
db_config = {
    "host": "91.84.97.19",
    "port": 5432,
    "database": "snapidb",
    "user": "moxy",
    "password": "moxy1337"
}

# Подключение к PostgreSQL
try:
    conn = psycopg2.connect(**db_config)
    logging.info("Успешно подключились к PostgreSQL!")
except Exception as e:
    logging.error(f"Ошибка подключения к PostgreSQL: {e}")
    raise

# Создай курсор для выполнения запросов
cursor = conn.cursor()
