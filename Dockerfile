FROM python:3.12-slim
LABEL authors="EricUgoPaque"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements/testing.txt
COPY . .
EXPOSE 8000

# --no-access-log: uvicorn's access line is `GET /dso/resolve?postcode=…`, a home's
# postcode and street name. tests/test_privacy.py checks both Dockerfiles.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-access-log"]
