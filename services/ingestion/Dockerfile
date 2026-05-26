FROM python:3.12-slim AS base

# Build-time toggle: pass --build-arg WITH_NLP=1 to bake in Presidio +
# en_core_web_lg (adds ~600MB to the image). Default off so the standard
# ingestion + worker image stays small. Workers that need PII redaction
# should be built with this flag set.
ARG WITH_NLP=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the shared schema first (referenced by pyproject) then the service.
COPY packages/shared-schema/python /app/packages/shared-schema/python
COPY services/ingestion /app/services/ingestion

WORKDIR /app/services/ingestion

RUN pip install --upgrade pip \
 && pip install /app/packages/shared-schema/python \
 && pip install -e .

# NLP layer: Presidio + spaCy + en_core_web_lg. Gated on WITH_NLP so the
# regex-only image stays lean. When off, the redactor's lazy Presidio loader
# logs a one-time warning and falls back to regex-only.
RUN if [ "$WITH_NLP" = "1" ]; then \
        pip install -e ".[nlp]" \
     && python -m spacy download en_core_web_lg; \
    fi

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
