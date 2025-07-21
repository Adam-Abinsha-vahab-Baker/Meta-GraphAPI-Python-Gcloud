
import requests
from flask import Flask, request, render_template, url_for
from flask_sqlalchemy import SQLAlchemy
import json
from datetime import datetime
import os
from dotenv import load_dotenv


load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
db = SQLAlchemy(app)

# Database model
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String, index=True)
    item = db.Column(db.String)
    message = db.Column(db.String)
    created_time = db.Column(db.String)
    raw_json = db.Column(db.Text)

    def __init__(self, post_id, item, message, created_time, raw_json):
        self.post_id = post_id
        self.item = item
        self.message = message
        self.created_time = created_time
        self.raw_json = raw_json


# Load sensitive tokens from .env
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
events = []

def get_post_details(post_id, access_token):
    url = f"https://graph.facebook.com/{post_id}"
    params = {
        "fields": "from,message,story,attachments,created_time",
        "access_token": access_token
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        print("Failed to fetch post details:", response.text)
        return None

@app.before_request
def create_tables():
    db.create_all()

@app.route("/webhook", methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        """
        Facebook will send a GET request to verify the webhook.
        It includes:
        - hub.mode
        - hub.verify_token
        - hub.challenge

        If you respond with the challenge, Facebook knows your endpoint is legit.
        """
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("✅ Webhook verified!")
            return challenge, 200
        else:
            print("❌ Webhook verification failed.")
            return 'Verification failed', 403

    elif request.method == 'POST':
        data = request.get_json()
        print("📥 New webhook event:", data)

        if 'entry' in data and 'changes' in data['entry'][0]:
            changes = data['entry'][0]['changes']
            for change in changes:
                if change.get('field') in ['feed', 'mention']:
                    event_data = change['value']
                    post_id = event_data.get('post_id')
                    # Convert UNIX timestamp if present and not already ISO
                    ct = event_data.get('created_time')
                    if isinstance(ct, int) or (isinstance(ct, str) and ct.isdigit()):
                        event_data['created_time'] = datetime.utcfromtimestamp(int(ct)).strftime('%Y-%m-%d %H:%M:%S UTC')
                    if post_id:
                        details = get_post_details(post_id, PAGE_ACCESS_TOKEN)
                        if details:
                            event_data['fetched_details'] = details
                    print(f"Event detected (field={change.get('field')}):", event_data)
                    # Store in memory
                    events.append(event_data)
                    # Store in DB
                    db.session.add(Event(
                        post_id=event_data.get('post_id'),
                        item=event_data.get('item'),
                        message=event_data.get('fetched_details', {}).get('message', event_data.get('message')),
                        created_time=event_data.get('fetched_details', {}).get('created_time', event_data.get('created_time')),
                        raw_json=json.dumps(event_data)
                    ))
                    db.session.commit()
        return 'EVENT_RECEIVED', 200

@app.route("/events")
def show_events():
    db_events = Event.query.order_by(Event.id.desc()).all()
    events_for_template = []
    for e in db_events:
        try:
            event_dict = json.loads(e.raw_json)
        except Exception:
            event_dict = {}
        events_for_template.append(event_dict)
    return render_template("events.html", events=events_for_template)

if __name__ == "__main__":
    # Run Flask locally on port 8080
    app.run(debug=True, port=8080)
