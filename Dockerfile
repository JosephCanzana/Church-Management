# The image
FROM python:3.12-slim

# Set /app as the working directory for all following commands
WORKDIR /app

# Just for cleaning
ENV PYTHONDONTWRITEBYTECODE=1
# For less buffer
ENV PYTHONUNBUFFERED=1

# update linux,  get postgre, remove all unecessaary 
RUN apt-get update && apt-get install -y libpq-dev gcc curl && rm -rf /var/lib/apt/lists/*

# copy in app and then install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# everything to /app
COPY . .

# This is just notes so my app is in locahost:8000
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]