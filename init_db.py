# init_db.py
from webhook import app, db

with app.app_context():
    db.create_all()
print("✅ Database initialized")
