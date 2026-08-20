FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY requirements*.txt ./

RUN python -m pip install --upgrade pip \
    && if [ -f requirements-lock.txt ]; then \
         python -m pip install -r requirements-lock.txt; \
       else \
         python -m pip install -r requirements.txt; \
       fi

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]