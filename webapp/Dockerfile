FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:6767", "--workers", "3", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "app:app"]