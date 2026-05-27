FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Flat layout: code + data live together at the repo root.
COPY . .

ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

EXPOSE 8000

# Backend. Set LLM_ENDPOINT / LLM_MODEL to point at your LLM.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
