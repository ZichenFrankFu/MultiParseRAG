FROM python:3.12.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data && chown app:app /app/data
USER app
EXPOSE 8080
CMD ["multiparserag"]
