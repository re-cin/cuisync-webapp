FROM python:3.11-slim

WORKDIR /app
# CPU-only torch keeps the image small (no CUDA)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Embedding model for POST /embed (public, about 90 MB): downloaded once while the Space builds,
# so it is ready offline and nothing has to be uploaded for it
RUN python -c "from transformers import AutoModel, AutoTokenizer; \
n = 'sentence-transformers/all-MiniLM-L6-v2'; \
AutoTokenizer.from_pretrained(n).save_pretrained('/app/embed_model'); \
AutoModel.from_pretrained(n).save_pretrained('/app/embed_model')"
COPY . .

EXPOSE 7860
# One worker = one copy of the 435 MB model in memory
CMD ["gunicorn", "-b", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "180", "server:app"]
