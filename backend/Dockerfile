FROM python:3.14-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

RUN useradd -m architeq
USER architeq

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
