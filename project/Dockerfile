FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py pyproject.toml ./
COPY suwapath ./suwapath

RUN useradd --create-home --uid 10001 suwapath
USER suwapath

EXPOSE 8000
CMD ["python", "main.py", "api", "--host", "0.0.0.0", "--port", "8000"]
