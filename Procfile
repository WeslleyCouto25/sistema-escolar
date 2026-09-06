web: python migrate.py && exec gunicorn app:app --worker-class gthread --workers 1 --threads 4 --timeout 180 --keep-alive 5 --max-requests 500 --max-requests-jitter 50
