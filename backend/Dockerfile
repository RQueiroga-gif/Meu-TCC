FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# PyTorch apenas para CPU seria instalado aqui se sentence-transformers for usado:
# RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8001

# O seed e idempotente: na primeira subida cria usuarios e indexa as amostras;
# nas seguintes apenas confere e repoe o que faltar no indice.
CMD ["sh", "-c", "python -m scripts.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8001"]
