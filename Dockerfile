# ClaroUnify Hub — imagem do backend (FastAPI + frontend estático embutido)
FROM python:3.12-slim

WORKDIR /app

# Instala dependências primeiro (aproveita cache do Docker em rebuilds que só
# mudam código, não dependências).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY main.py db.py ./
COPY static/ ./static/

# O banco SQLite vive num volume montado em /data (ver docker-compose.yml) —
# assim os dados sobrevivem a `docker compose down`/rebuild da imagem.
ENV DATABASE_PATH=/data/clarounify.db
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
