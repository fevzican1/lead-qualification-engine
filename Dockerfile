FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates fonts-liberation libnss3 libatk1.0-0 \
        libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxfixes6 libxrandr2 libgbm1 libasound2 \
        postgresql-client tesseract-ocr python3-opencv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Üretim hazırlığı: testleri ve AST linter'ı çalıştırabilir hale getir
RUN pip install --no-cache-dir pytest pytest-asyncio

CMD ["python", "telegram_sales_bot.py"]
