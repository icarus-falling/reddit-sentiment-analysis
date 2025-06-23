import os
from dotenv import load_dotenv
load_dotenv()
import sqlite3
import uuid
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import boto3
import praw
from collections import Counter, defaultdict

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Reddit API setup
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent="reddit-sentiment-app"
)

# DynamoDB setup
dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
table = dynamodb.Table('SentimentResults')

# SQLite setup
def create_db():
    conn = sqlite3.connect("sentiment_results.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            sentiment TEXT,
            score REAL,
            keyword TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

create_db()

def save_results(results):
    conn = sqlite3.connect("sentiment_results.db")
    c = conn.cursor()
    for result in results:
        c.execute("""
            INSERT INTO sentiment_results (title, sentiment, score, keyword, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (result["title"], result["sentiment"], result["score"], result["keyword"], result["timestamp"]))

        table.put_item(Item={
            "id": str(uuid.uuid4()),
            "title": result["title"],
            "sentiment": result["sentiment"],
            "score": str(result["score"]),
            "keyword": result["keyword"],
            "timestamp": result["timestamp"]
        })
    conn.commit()
    conn.close()

def get_recent_results(keywords=None):
    conn = sqlite3.connect("sentiment_results.db")
    c = conn.cursor()
    results = []
    if keywords:
        for keyword in keywords:
            c.execute("""
                SELECT keyword, title, sentiment, score, timestamp
                FROM sentiment_results
                WHERE keyword = ?
                ORDER BY timestamp DESC
                LIMIT 20
            """, (keyword,))
            rows = c.fetchall()
            results.extend(rows)
    else:
        c.execute("""
            SELECT keyword, title, sentiment, score, timestamp
            FROM sentiment_results
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        rows = c.fetchall()
        results.extend(rows)
    conn.close()
    return [
        {
            "keyword": row[0],
            "title": row[1],
            "sentiment": row[2],
            "score": row[3],
            "timestamp": row[4],
        }
        for row in results
    ]

def get_sentiment_counts():
    conn = sqlite3.connect("sentiment_results.db")
    c = conn.cursor()
    c.execute("SELECT sentiment FROM sentiment_results")
    sentiments = [row[0] for row in c.fetchall()]
    conn.close()
    return Counter(sentiments)

def get_sentiment_trend_data():
    conn = sqlite3.connect("sentiment_results.db")
    c = conn.cursor()
    c.execute("SELECT keyword, timestamp, score FROM sentiment_results ORDER BY timestamp")
    rows = c.fetchall()
    conn.close()
    trend_data = defaultdict(lambda: {"timestamps": [], "scores": []})
    for keyword, timestamp, score in rows:
        trend_data[keyword]["timestamps"].append(timestamp)
        trend_data[keyword]["scores"].append(score)
    return dict(trend_data)

def analyze_sentiment(keyword):
    posts = reddit.subreddit("all").search(keyword, limit=20)
    comprehend = boto3.client(
    'comprehend',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)
    results = []
    for post in posts:
        title = post.title.strip()
        if not title:
            continue
        response = comprehend.detect_sentiment(Text=title, LanguageCode='en')
        sentiment = response["Sentiment"]
        score = response["SentimentScore"]
        polarity = score.get(sentiment.capitalize(), 0.0)
        results.append({
            "title": title,
            "sentiment": sentiment,
            "score": round(polarity, 3),
            "keyword": keyword,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    return results

@app.get("/", response_class=HTMLResponse)
async def read_form(request: Request):
    recent_results = get_recent_results()
    sentiment_counts = get_sentiment_counts()
    trend_data = get_sentiment_trend_data()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "results": recent_results,
        "sentiment_counts": sentiment_counts,
        "trend_data": trend_data
    })

@app.post("/", response_class=HTMLResponse)
async def form_post(request: Request, keyword: str = Form(...)):
    keywords = [k.strip() for k in keyword.split(",") if k.strip()]
    all_sentiments = []
    for k in keywords:
        sentiments = analyze_sentiment(k)
        all_sentiments.extend(sentiments)
    save_results(all_sentiments)
    results = get_recent_results(keywords)
    sentiment_counts = Counter([r["sentiment"] for r in all_sentiments])
    trend_data = get_sentiment_trend_data()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "results": results,
        "sentiment_counts": dict(sentiment_counts),
        "trend_data": trend_data
    })
