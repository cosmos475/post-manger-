FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render/Heroku/Koyeb/Railway/Cloud Run all inject $PORT at runtime;
# main.py already reads it via config.py (falls back to 10000 locally).
ENV PORT=10000
EXPOSE 10000

CMD ["python", "main.py"]
