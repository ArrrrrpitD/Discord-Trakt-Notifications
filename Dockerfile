FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .

# Create data directory
RUN mkdir -p data

# Run the application
CMD ["python", "-u", "main.py"]