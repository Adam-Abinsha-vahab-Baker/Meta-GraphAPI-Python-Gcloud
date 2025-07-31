import os
import json
from datetime import datetime, timezone
import requests
from flask import Flask, request, render_template
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from dotenv import load_dotenv
import imaplib
import smtplib
import email
from email.mime.text import MIMEText

# ──────────────────────────────
load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
PAGE_ID = os.getenv("PAGE_ID")  # Add this to your .env!
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# ──────────────── EMAIL REPLY CONFIG ────────────────
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
APP_PASSWORD = os.getenv("APP_PASSWORD")

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

OPENAI_MODEL = "gpt-4o-mini"

def get_latest_email(prefer_unread=True):
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    imap.login(EMAIL_ADDRESS, APP_PASSWORD) # type: ignore
    imap.select("inbox")

    status, messages = imap.search(None, 'ALL')
    mail_ids = messages[0].split()
    mail_ids.reverse()

    chosen_id = None
    for mail_id in mail_ids:
        status, data = imap.fetch(mail_id, "(FLAGS)")
        flags_str = b''
        if data and isinstance(data[0], tuple):
            flags_str = data[0][0]
        elif data and isinstance(data[0], bytes):
            flags_str = data[0]
        if b'\\Seen' not in flags_str and prefer_unread:
            chosen_id = mail_id
            break

    if not chosen_id and mail_ids:
        chosen_id = mail_ids[0]

    if not chosen_id:
        print("No emails found.")
        imap.logout()
        return None, None, None, None

    status, data = imap.fetch(chosen_id, "(RFC822)")
    raw_email = None
    if data and isinstance(data[0], tuple):
        raw_email = data[0][1]
    elif data and isinstance(data[0], bytes):
        raw_email = data[0]
    if not raw_email:
        print("Could not fetch email body.")
        imap.logout()
        return None, None, None, None
    import email.utils as email_utils
    msg = email.message_from_bytes(raw_email)
    sender = email_utils.parseaddr(msg['From'])[1]
    subject = msg['Subject']
    message_id = msg.get('Message-ID')
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(errors='ignore')
                else:
                    body = str(payload)
                break
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            body = payload.decode(errors='ignore')
        else:
            body = str(payload)
    imap.store(chosen_id, '+FLAGS', '\\Seen')
    imap.logout()
    print(f"Sender: {sender}\nSubject: {subject}\nBody: {body[:50]}...\nMessage-ID: {message_id}")
    return sender, subject, body, message_id

def generate_ai_reply(email_body):
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant for business email replies. Our opening times are 9am to 5pm weekdays."},
                {"role": "user", "content": f"Reply to this email: {email_body}"}
            ],
            max_tokens=80,
            temperature=0.5
        )
        content = response.choices[0].message.content if response.choices and hasattr(response.choices[0], 'message') and response.choices[0].message and hasattr(response.choices[0].message, 'content') else None
        return content.strip() if content else "Hey thanks for reaching out, our opening times are from 9am to 5pm weekdays!."
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return "Hey thanks for reaching out, our opening times are from 9am to 5pm weekdays!."

def send_reply(to_email, subject, reply_text):
    smtp = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    smtp.ehlo()
    smtp.starttls()
    smtp.login(EMAIL_ADDRESS, APP_PASSWORD)

    msg = MIMEText(reply_text)
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = to_email
    msg['Subject'] = f"Re: {subject}"

    smtp.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
    print(f"Reply sent to: {to_email}")
    smtp.quit()

# ──────────────── FLASK APP INIT ────────────────
app = Flask(__name__, static_url_path='/app2/static')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
db = SQLAlchemy(app)

# ──────────────── EMAIL REPLY ENDPOINT ────────────────
@app.route("/email_reply", methods=["POST", "GET"])
def email_reply():
    result = get_latest_email()
    if result and len(result) == 4:
        sender, subject, body, message_id = result
        if sender and body and message_id:
            if not EmailLog.query.filter_by(message_id=message_id).first():
                ai_reply = generate_ai_reply(body)
                send_reply(sender, subject, ai_reply)
                created_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log = EmailLog(message_id, sender, subject, body, ai_reply, created_time)
                db.session.add(log)
                db.session.commit()
            return f"Reply sent to {sender} for subject '{subject}'.", 200
        else:
            return "No email found to reply to.", 404
    return "No email found to reply to.", 404

# ──────────────────────────────
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String, index=True)
    comment_id = db.Column(db.String, index=True, unique=True)
    item = db.Column(db.String)
    message = db.Column(db.String)
    openai_insight = db.Column(db.String)
    created_time = db.Column(db.String)
    raw_json = db.Column(db.Text)
    commented = db.Column(db.Boolean, default=False)

    def __init__(self, post_id, comment_id, item, message, openai_insight, created_time, raw_json, commented=False):
        self.post_id = post_id
        self.comment_id = comment_id
        self.item = item
        self.message = message
        self.openai_insight = openai_insight
        self.created_time = created_time
        self.raw_json = raw_json
        self.commented = commented

# ──────────────────────────────

def create_tables():
    db.create_all()

# ──────────────────────────────
def get_openai_insight(message):
    if not OPENAI_API_KEY or not message:
        return None
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful, polite assistant for a Facebook page. "
                               "Write a short, friendly, positive reply that thanks the user "
                               "without mentioning the company name. Keep it warm and professional."
                               "Our opening times are Monday to Friday, 9 AM to 5 PM. "
                },
                {
                    "role": "user",
                    "content": f"User wrote: '{message}'. Write a short, friendly business comment reply."
                }
            ],
            max_tokens=60,
            temperature=0.5
        )
        return response.choices[0].message.content.strip() # type: ignore
    except Exception as e:
        print("OpenAI API error:", e)
        return None

# ──────────────────────────────
def post_facebook_comment(target_id, message, access_token):
    url = f"https://graph.facebook.com/{target_id}/comments"
    params = {
        "message": message,
        "access_token": access_token
    }
    response = requests.post(url, data=params)
    if response.status_code == 200:
        print("✅ Posted reply:", response.json())
        return response.json()
    else:
        print("❌ Failed to post:", response.text)
        return None

# ──────────────────────────────
@app.route("/webhook", methods=['GET', 'POST']) # type: ignore
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        return 'Verification failed', 403

    elif request.method == 'POST':
        data = request.get_json()
        print("📥 Incoming:", data)

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                field = change.get("field")
                if field not in ("feed", "mention"):
                    continue

                post_id = value.get("post_id")
                comment_id = value.get("comment_id")
                parent_id = value.get("parent_id")
                actor_id = (value.get("from") or {}).get("id")
                item_type = value.get("item")
                raw_json = json.dumps(value)

                # Ignore if comment is from the Page itself
                if actor_id and PAGE_ID and actor_id == PAGE_ID:
                    continue

                # Only handle top-level comments
                if parent_id and parent_id != post_id:
                    continue

                # Skip if already handled
                if comment_id and Event.query.filter_by(comment_id=comment_id, commented=True).first():
                    continue

                message_text = value.get('message')
                insight = get_openai_insight(message_text)
                did_comment = False

                if insight and comment_id:
                    try:
                        post_facebook_comment(comment_id, insight, PAGE_ACCESS_TOKEN)
                        did_comment = True
                    except Exception as e:
                        print("❌ Failed to post reply:", e)

                # Normalize created_time
                created_time = value.get("created_time")
                if created_time and (str(created_time).isdigit() or isinstance(created_time, int)):
                    created_time = datetime.utcfromtimestamp(int(created_time)).strftime("%Y-%m-%d %H:%M:%S UTC")

                evt = Event(
                    post_id=post_id,
                    comment_id=comment_id,
                    item=item_type,
                    message=message_text,
                    openai_insight=insight,
                    created_time=created_time,
                    raw_json=raw_json,
                    commented=did_comment
                )
                db.session.add(evt)
                db.session.commit()
        return 'EVENT_RECEIVED', 200

# ──────────────────────────────
@app.route("/events")
def show_events():
    db_events = Event.query.order_by(Event.id.desc()).all()
    parsed_events = []
    for e in db_events:
        try:
            event_dict = json.loads(e.raw_json)
        except Exception:
            event_dict = {}
        # Fill missing details
        event_dict['openai_insight'] = e.openai_insight
        event_dict['created_time'] = e.created_time
        parsed_events.append(event_dict)
    return render_template("events.html", events=parsed_events)

# ──────────────────────────────
import threading
import time

# ──────────────── EMAIL LOG DB ────────────────
class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String, unique=True, index=True)
    sender = db.Column(db.String)
    subject = db.Column(db.String)
    body = db.Column(db.Text)
    ai_reply = db.Column(db.Text)
    created_time = db.Column(db.String)

    def __init__(self, message_id, sender, subject, body, ai_reply, created_time):
        self.message_id = message_id
        self.sender = sender
        self.subject = subject
        self.body = body
        self.ai_reply = ai_reply
        self.created_time = created_time

# ──────────────── EMAIL CHECK LOOP ────────────────
def email_check_loop(interval=60):
    from datetime import datetime, timezone
    with app.app_context():
        while True:
            result = get_latest_email()
            if result and len(result) == 4:
                sender, subject, body, message_id = result
                if sender and body and message_id:
                    # Only reply if not already replied
                    if not EmailLog.query.filter_by(message_id=message_id).first():
                        ai_reply = generate_ai_reply(body)
                        send_reply(sender, subject, ai_reply)
                        created_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                        log = EmailLog(message_id, sender, subject, body, ai_reply, created_time)
                        db.session.add(log)
                        db.session.commit()
            time.sleep(interval)

# ──────────────── EMAILS PAGE ────────────────
@app.route("/emails")
def show_emails():
    db_emails = EmailLog.query.order_by(EmailLog.id.desc()).all()
    parsed_emails = []
    for e in db_emails:
        parsed_emails.append({
            'sender': e.sender,
            'subject': e.subject,
            'body': e.body,
            'ai_reply': e.ai_reply,
            'created_time': e.created_time
        })
    return render_template("emails.html", emails=parsed_emails)

# ──────────────── MAIN ────────────────
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    threading.Thread(target=email_check_loop, args=(60,), daemon=True).start()
    app.run(debug=True, port=8080,host="0.0.0.0")
