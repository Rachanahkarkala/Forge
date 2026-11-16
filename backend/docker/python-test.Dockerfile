
FROM python:3.12-slim

RUN pip install --no-cache-dir pytest

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
