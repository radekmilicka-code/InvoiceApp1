FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py qr_generator.py database.py schema.sql ./
COPY templates/ ./templates/
COPY DejaVuSans.ttf DejaVuSans-Bold.ttf DejaVuSans-Oblique.ttf ./

# Create data directory (persisted via Railway volume)
RUN mkdir -p /app/data

# Copy default/sample data (will be used only if no volume is mounted)
COPY data/products.json ./data/

# Railway injects PORT env variable
ENV PORT=8080
ENV FLASK_ENV=production

EXPOSE 8080

# Use gunicorn for production
CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 60 app:app
