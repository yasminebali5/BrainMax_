FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEBUG=False
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput
EXPOSE 8080
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_demo && gunicorn BrainMax.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120"]
