FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

# Install CPU-only torch first so sentence-transformers doesn't pull ~3GB of CUDA wheels.
RUN pip install --no-cache-dir --timeout=180 --retries=10 \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.3.0

RUN pip install --no-cache-dir --timeout=180 --retries=10 \
    -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
