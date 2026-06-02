# AI-Powered Amazon UAE Shopping Assistant

An AI-powered shopping assistant that helps users discover, compare, and evaluate Amazon UAE grooming products using Google's Gemini AI.

## Features

* Live product catalog sourced from Amazon UAE
* Product filtering by brand, price, and rating
* AI-powered recommendations using Gemini
* Best-value product suggestions
* Real-time product catalog updates from Google Sheets
* Interactive web-based chat interface
* Flask backend with REST API endpoints

## Tech Stack

### Backend

* Python
* Flask
* Flask-CORS
* Pandas

### AI

* Google Gemini 2.5 Flash

### Frontend

* HTML
* CSS
* JavaScript

### Data Management

* Google Sheets
* JSON-based scraping pipeline

## How It Works

1. Product data is collected through a scraping pipeline.
2. Product information is stored and synchronized with Google Sheets.
3. The Flask backend loads the latest product catalog.
4. Gemini receives the catalog as context and answers user queries.
5. Users interact through a responsive chat interface.

## Example Questions

* What is the cheapest product?
* Which Philips shavers are rated above 4.2?
* Recommend the best value shaver under 150 AED.
* Compare Braun vs Philips products.
* Are there any shavers for women?

## Installation

```bash
git clone <repository-url>
cd ai-shopping-assistant

pip install -r requirements.txt
```

Create a `.env` file:

```env
GOOGLE_API_KEY=your_api_key_here
```

Run the application:

```bash
python app.py
```

Visit:

```
http://127.0.0.1:5000
```

## Project Structure

```
app.py
static/
├── index.html
├── chat.html

scraper2.py
scraper4.py
merge2.py
requirements.txt
```

## Future Improvements

* Multi-category product support
* Product image integration
* User accounts and saved searches
* Semantic product search
* Price history tracking
* Recommendation personalization

## Author

Saachi Kanda
American University of Sharjah
Information Systems and Business Analytics Student

