FROM python:3.11-slim

WORKDIR /app

# Install dependencies in a separate layer.
# Docker caches this layer — reinstall only happens when requirements.txt changes,
# not on every code change. Speeds up rebuilds significantly.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download NLTK language data required by RAKE at runtime.
# Done at build time so the container starts instantly with no first-request delay.
RUN python -c "\
import nltk; \
nltk.download('stopwords'); \
nltk.download('punkt'); \
nltk.download('punkt_tab')"

# Copy application source after dependencies so code changes don't invalidate
# the pip install layer above
COPY . .

# Cloud Run injects the PORT environment variable. Default to 8080.
ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
